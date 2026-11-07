"""Orchestration: builds the engine context from market data, runs modules, serves history, compound, news and audit."""
from __future__ import annotations

import os
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field

from . import compound as compound_mod
from .audit import AuditStore
from .config import Defaults
from .engines import bunker, freight, fx, interest
from .datasources import DataSources
from .engines.common import EngineContext, Liquidity, ScenarioRequest
from .explain import ExplanationProvider, explain
from .market import FixtureProvider, LiveProvider, MarketProvider, ObservationCache, market_block
from .materiality import Thresholds
from .models import (D, MODULES, ModuleResult, Provenance, Status, StrictModel, ValidationProblem)
from .news_context import latest_export, news_for_module

ENGINES = {"fx": fx, "interest": interest, "bunker": bunker, "freight": freight}
ECB_IDS = {"ECB_DFR", "ECB_ESTR"}


class OverviewCard(StrictModel):
    module: str
    title: str
    latest_value: float | None
    unit: str
    change: float | None
    change_unit: str | None
    change_pct: float | None
    latest_date: date | None
    source: str
    source_kind: str
    freshness: str
    market_provenance: Provenance
    impact_label: str
    impact_value: float | None
    impact_unit: str | None
    impact_provenance: Provenance
    impact_type: str
    status: Status
    worst_case_status: Status
    rule_id: str
    reliability: str
    thresholds_note: str
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


def default_clock(tz_name: str):
    tz = ZoneInfo(tz_name)

    def now() -> datetime:
        fixed = os.environ.get("SENSITIVITY_AS_OF")
        if fixed:
            return datetime.combine(date.fromisoformat(fixed), time(9, 0), tzinfo=tz)
        return datetime.now(tz)
    return now


class SensitivityService:
    def __init__(self, *, defaults: Defaults | None = None, provider: MarketProvider | None = None, audit: AuditStore | None = None,
                 clock=None, news_dir: Path | None = None, explainer: ExplanationProvider | None = None,
                 sources: DataSources | None = None):
        self.base_defaults = defaults or Defaults.load()
        self.sources = sources or DataSources(os.environ.get("SENSITIVITY_DATA_DIR"))
        self._eff: tuple[int, Defaults] | None = None
        self.thresholds = Thresholds.from_defaults(self.base_defaults)
        self.tz = ZoneInfo(self.base_defaults["timezone"])
        self.clock = clock or default_clock(self.base_defaults["timezone"])
        self.audit = audit or AuditStore(":memory:")
        self.news_dir = Path(news_dir) if news_dir else None
        self.explainer = explainer
        if provider is None:
            fixtures = FixtureProvider()
            mode = os.environ.get("SENSITIVITY_MARKET_MODE", "fixture")
            provider = LiveProvider(fixtures, ObservationCache(Path(os.environ.get("SENSITIVITY_CACHE_DIR", "data/market_cache")))) \
                if mode == "live" else fixtures
        self.provider = provider

    @property
    def defaults(self) -> Defaults:
        """Demo defaults with any connected-data overrides applied (recomputed only when an override changes)."""
        if self._eff is None or self._eff[0] != self.sources.version:
            self._eff = (self.sources.version, self.sources.effective_defaults(self.base_defaults))
        return self._eff[1]

    def run(self, ctx: EngineContext, module: str, req: ScenarioRequest | None = None) -> ModuleResult:
        """Single place engines are invoked: injects the stored approved limit when the request has none."""
        req = req or ScenarioRequest()
        if req.limit is None and (lim := self.sources.limit_for(module)) is not None:
            req = req.model_copy(update={"limit": lim})
        return ENGINES[module].compute(ctx, req)

    # ------------------------------------------------------------ context
    def _series_ids(self) -> list[str]:
        d = self.defaults
        ids = [*(c["series"] for c in d["fx"]["currencies"].values()), d["fx"]["eurusd_series"], *d["interest"]["alt_series"],
               d["bunker"]["series"], d["freight"]["series"]]
        return list(dict.fromkeys(ids))

    def context(self, as_of: date | None = None) -> EngineContext:
        now = self.clock()
        if as_of is not None:
            now = datetime.combine(as_of, time(9, 0), tzinfo=self.tz)
        day = now.astimezone(self.tz).date()
        series, blocks, warnings = {}, {}, []
        for sid in self._series_ids():
            s = self.provider.get_series(sid, as_of=day, now=now)
            series[sid] = s
            blocks[sid] = market_block(s, day, change_in_bps=sid in ECB_IDS)
        liq = self.defaults["liquidity"]
        liq_date = date.fromisoformat(liq["balance_date"])
        amount = D(liq["reported_total_usd_m"])
        age = (day - liq_date).days
        if age > liq["max_age_days"]:
            warnings.append(f"Comparable liquidity is {age} days old (limit {liq['max_age_days']}): treated as unavailable")
            amount = None
        elif age > liq["info_age_days"]:
            warnings.append(f"Liquidity is the reported balance at {liq_date} ({age} days old), not a current balance")
        liquidity = Liquidity(amount_usd_m=amount, provenance=Provenance(liq["provenance"]), as_of=liq_date, note=liq["note"])
        eb = blocks[self.defaults["fx"]["eurusd_series"]]
        eurusd = D(eb.latest_value) if eb.latest_value is not None else None
        if eb.freshness != "FRESH" and eurusd is not None:
            warnings.append(f"EUR/USD rate dated {eb.latest_date} is {eb.freshness}; EUR conversions use that dated rate")
        positions, hedges = self.sources.exposures()
        return EngineContext(defaults=self.defaults, positions=positions, hedges=hedges, thresholds=self.thresholds, as_of=now, series=series, blocks=blocks,
                             liquidity=liquidity, eurusd=eurusd, eurusd_date=eb.latest_date,
                             eurusd_provenance=eb.provenance, warnings=warnings)

    # ----------------------------------------------------------- modules
    def compute(self, module: str, req: ScenarioRequest | None = None, as_of: date | None = None) -> ModuleResult:
        if module not in ENGINES:
            raise KeyError(module)
        return self.run(self.context(as_of), module, req)

    def overview(self, as_of: date | None = None, requests: dict[str, ScenarioRequest] | None = None) -> list[OverviewCard]:
        ctx = self.context(as_of)
        cards = []
        for module in MODULES:
            r = self.run(ctx, module, (requests or {}).get(module))
            m, h = r.market, r.headline
            cards.append(OverviewCard(
                module=module, title=r.title, latest_value=m.latest_value, unit=m.unit, change=m.change, change_unit=m.change_unit,
                change_pct=m.change_pct, latest_date=m.latest_date, source=m.source, source_kind=m.source_kind, freshness=m.freshness,
                market_provenance=m.provenance, impact_label=r.labels.get("impact", ""), impact_value=h.impact.value if h else None,
                impact_unit=h.impact.unit if h else None, impact_provenance=h.impact.provenance if h else Provenance.UNAVAILABLE,
                impact_type=r.impact_type.value, status=r.assessment.status, worst_case_status=r.worst_case_status,
                rule_id=r.assessment.rule_id, reliability=r.reliability.value, thresholds_note=r.assessment.thresholds_note,
                warnings=r.warnings[:3], error=m.error))
        return cards

    def history(self, module: str, window: str = "All", as_of: date | None = None, benchmark: str | None = None,
                currency: str | None = None) -> dict:
        if module not in ENGINES:
            raise KeyError(module)
        d = self.defaults
        ccy = (currency or d["fx"]["default_currency"]).upper()
        if ccy not in d["fx"]["currencies"]:
            raise ValidationProblem("currency must be one of " + ", ".join(d["fx"]["currencies"]))
        sid = {"fx": d["fx"]["currencies"][ccy]["series"], "interest": benchmark or d["interest"]["series"],
               "bunker": d["bunker"]["series"], "freight": d["freight"]["series"]}[module]
        if module == "interest" and sid not in d["interest"]["alt_series"]:
            raise ValidationProblem("benchmark must be one of " + ", ".join(d["interest"]["alt_series"]))
        ctx = self.context(as_of)
        series = ctx.series[sid]
        pts = series.history(window)
        return {"module": module, "series_id": sid, "window": window, "unit": series.unit, "frequency": series.frequency,
                "points": [{"date": p.date.isoformat(), "value": p.value} for p in pts],
                "point_count": len(pts), "gaps": series.gaps(), "interpolated": False,
                "market": ctx.blocks[sid].model_dump(mode="json")}

    def input_controls(self, module: str) -> dict:
        """Defaults and bounds for rendering editable controls (the same specs the engine validates against)."""
        r = self.compute(module)
        return {"module": module, "defaults_version": r.defaults_version,
                "controls": [i.model_dump(mode="json") for i in r.inputs],
                "shock_grid": [s.shock for s in r.scenarios], "mode": r.mode}

    def compound(self, req: compound_mod.CompoundRequest | None = None, as_of: date | None = None,
                 scenario_req: ScenarioRequest | None = None):
        req = req or compound_mod.CompoundRequest()
        bad = [m for m in req.modules if m not in ENGINES]
        if bad:
            raise ValidationProblem(f"Unknown module(s): {', '.join(bad)}")
        if len(set(req.modules)) != len(req.modules):
            raise ValidationProblem("A module may appear only once in a compound scenario")
        ctx = self.context(as_of)
        results = {m: self.run(ctx, m, req.scenarios.get(m)) for m in req.modules}
        return compound_mod.aggregate(ctx, results, req, scenario_req or ScenarioRequest()), results

    def news(self, module: str, limit: int = 10) -> dict:
        export = latest_export(self.news_dir) if self.news_dir else None
        return news_for_module(module, export, limit=limit)

    def explanation(self, result: ModuleResult) -> dict:
        return explain(result, self.explainer)

    # ------------------------------------------------------------ connected data
    def set_source(self, kind: str, data: dict, actor: str) -> dict:
        if not actor or not actor.strip():
            raise ValidationProblem("actor is required for a data change")
        entry = self.sources.put(kind, data, actor.strip(), today=self.clock().astimezone(self.tz).date())
        self.audit.append("data_source_set", actor=actor.strip(), details={"kind": kind, "data": entry["data"]})
        return entry

    def clear_source(self, kind: str, actor: str) -> bool:
        if not actor or not actor.strip():
            raise ValidationProblem("actor is required for a data change")
        existed = self.sources.delete(kind)
        self.audit.append("data_source_cleared", actor=actor.strip(), details={"kind": kind, "existed": existed})
        return existed

    def dashboard(self, months: int | None = None, as_of: date | None = None, shocks: dict | None = None) -> dict:
        from .dashboard import compose
        out = compose(self, months, as_of, shocks)
        out["data_sources"] = self.sources.status()
        return out

    # ------------------------------------------------------------- review
    def snapshot(self, module: str, req: ScenarioRequest, as_of: date | None = None) -> tuple[str, ModuleResult]:
        result = self.compute(module, req, as_of)            # recomputed here: a client cannot supply its own numbers
        sid = self.audit.save_snapshot(module, req.model_dump(mode="json"), result.model_dump(mode="json"))
        self.audit.append("snapshot", module=module, snapshot_id=sid, details={"status": result.assessment.status.value})
        return sid, result

    def review(self, module: str, req: ScenarioRequest, *, reviewer: str, comment: str | None, as_of: date | None = None) -> dict:
        if not reviewer.strip():
            raise ValidationProblem("A reviewer name is required")
        sid, result = self.snapshot(module, req, as_of)
        overrides = {k: v for k, v in req.overrides.items()}
        entry = self.audit.append("review", module=module, snapshot_id=sid, actor=reviewer.strip(), comment=comment, details={
            "formula_version": result.formula_version, "threshold_version": result.threshold_version,
            "defaults_version": result.defaults_version, "inputs": {i.key: i.quantity.raw for i in result.inputs},
            "assumption_overrides": overrides, "headline_shock": req.headline_shock, "mode": req.mode,
            "status": result.assessment.status.value, "rule_id": result.assessment.rule_id,
            "confirmed_triggers": [t.model_dump() for t in req.confirmed_triggers]})
        return {"snapshot_id": sid, "audit_seq": entry["seq"], "status": result.assessment.status.value}
