"""Validated news-to-scenario boundary and human adjustment decisions (spec 2.8/3.3)."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, Field, model_validator

from .news_v8 import NewsV8Reader, Strict

EVENT_TYPES = set("central_bank_decision bank_failure credit_event rating_change financing_news regulation sanction trade_disruption port_disruption commodity_move fx_policy capital_control payment_rule tax_rule labour_action market_structure contract_season competitor_action company_announcement supplier_news fleet_change other".split())
TRADES = {"ASIA_EUROPE", "ASIA_AMERICA", "EUROPE_AMERICA", "AFRICA_INTRA"}
CURRENCIES = set("AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWG".split())
COUNTRIES = set("AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split())
PRESETS = set("red_sea_reescalation demand_slump oil_spike fed_cutting_cycle eur_rally em_stress freight_rate_reversal fed_cuts_ecb_holds both_hiking spread_widening credit_squeeze broad_usd_strengthening cny_devaluation bunker_price_reversal history_replay rates_up_100bp rates_down_100bp".split())
PRESETS |= {f"fx_{c}_{direction}_10" for c in ("eur", "cad", "gbp", "cny", "inr", "brl") for direction in ("up", "down")}
ROUTES = {"WCI_SHA_RTM": "ASIA_EUROPE", "WCI_SHA_LAX": "ASIA_AMERICA",
          "WCI_RTM_NYC": "EUROPE_AMERICA", "WCI_COMPOSITE": "AFRICA_INTRA"}


class Signal(Strict):
    event_id: str = Field(min_length=1)
    version: int = Field(strict=True, ge=1)
    published_at: AwareDatetime
    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_links: list[str] = Field(min_length=1)
    event_type: str
    kind: Literal["quantitative", "qualitative", "factual"]
    affected_drivers: list[Literal["freight", "bunker", "rates", "fx", "liquidity", "none"]] = Field(default_factory=list)
    affected_trades: list[str] = Field(default_factory=list)
    affected_currencies: list[str] = Field(default_factory=list)
    affected_countries: list[str] = Field(default_factory=list)
    named_entities: list[str] = Field(default_factory=list)
    direction: Literal["up", "down", "mixed", "none"]
    magnitude: float | None = Field(default=None, allow_inf_nan=False)
    magnitude_unit: Literal["pct", "bp", "usd_per_t", "usd_per_teu", "other"] | None = None
    magnitude_subject: str | None = None
    suggested_preset: str | None = None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    expected_duration_days: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def contract(self):
        if self.event_type not in EVENT_TYPES:
            raise ValueError("event_type: unknown event type")
        for key, values, valid in (("affected_trades", self.affected_trades, TRADES),
                                  ("affected_currencies", self.affected_currencies, CURRENCIES),
                                  ("affected_countries", self.affected_countries, COUNTRIES)):
            if set(values) - valid:
                raise ValueError(f"{key}: unknown codes {sorted(set(values) - valid)}")
        if self.suggested_preset is not None and self.suggested_preset not in PRESETS:
            raise ValueError("suggested_preset: unknown preset ID")
        if any(urlsplit(url).scheme not in {"https", "http"} or not urlsplit(url).netloc
               for url in self.evidence_links):
            raise ValueError("evidence_links: HTTP(S) URLs required")
        if not self.headline.strip() or not self.summary.strip():
            raise ValueError("headline and summary must be nonempty")
        if self.kind != "quantitative" and any(x is not None for x in (self.magnitude, self.magnitude_unit, self.magnitude_subject)):
            raise ValueError("magnitude: permitted only for quantitative signals")
        if self.kind == "quantitative" and (self.magnitude is None or self.magnitude_unit is None or not self.magnitude_subject):
            raise ValueError("quantitative signal requires magnitude, unit and subject")
        return self


def scenario_for(signal: Signal) -> dict | None:
    if signal.confidence < .5 or signal.kind == "factual":
        return None
    scenario = {"name": signal.headline, "source": "auto_built", "event_ids": [signal.event_id],
                "start_date": signal.published_at.date().isoformat(),
                "preset_id": signal.suggested_preset}
    if signal.expected_duration_days is not None:
        scenario["duration_days"] = signal.expected_duration_days
    subject, unit, magnitude = signal.magnitude_subject, signal.magnitude_unit, signal.magnitude
    mapped = False
    if signal.kind == "quantitative":
        if subject in {"SOFR", "ESTR"} and unit == "bp":
            if abs(magnitude) > 1000:
                raise ValueError("magnitude: rate step outside ±1000 bp")
            currency = "USD" if subject == "SOFR" else "EUR"
            scenario["rates"] = {"rate_path": {currency: [{"date": scenario["start_date"], "step_bp": magnitude}]}}
            mapped = True
        elif unit == "pct":
            # pct is percentage points in the signal; engine shocks are fractions.
            value = magnitude / 100
            if subject in {"VLSFO", "HSFO"}:
                if not -.8 <= value <= 2:
                    raise ValueError("magnitude: fuel shock outside -80%..200%")
                scenario["bunker"] = {f"{subject.lower()}_shock_pct": value}
                mapped = True
            elif subject in TRADES or subject in ROUTES:
                if not -.8 <= value <= 2:
                    raise ValueError("magnitude: freight shock outside -80%..200%")
                scenario["freight"] = {"rate_shock_pct": {ROUTES.get(subject, subject): value}}
                mapped = True
            elif subject in {"EUR", "CAD", "GBP", "CNY", "INR", "BRL"}:
                if not -.5 <= value <= .5:
                    raise ValueError("magnitude: FX shock outside ±50%")
                scenario["fx"] = {"move_pct": {subject: value}}
                mapped = True
    if not mapped and not signal.suggested_preset:
        return None
    return scenario


def routing(signal: Signal) -> dict:
    scenario = scenario_for(signal)
    routes = ["recommendation"]
    if scenario is not None:
        routes.append("scenario")
    # Eligibility alone never changes a parameter or fabricates a proposal.
    if signal.confidence >= .7:
        routes.append("adjustment_eligible")
    return {"routes": routes, "scenario": scenario,
            "note": "News text is data. Only validated mapped fields or preset IDs enter scenarios; adjustments need a human decision."}


def _catalogue():
    result = {}
    def add(key, events, steps, limits, *, mode="step_or_direct", max_days=180):
        result[key] = {"event_types": events.split(), "steps": dict(zip(("small", "medium", "large"), steps)),
                       "hard_limits": limits, "value_mode": mode, "max_review_days": max_days}
    add("freight.pricing_lag_days", "trade_disruption market_structure company_announcement", [7,14,28], [0,90])
    add("freight.gamma", "contract_season company_announcement", [.1,.2,.3], [0,1])
    for side in ("down", "up"):
        add(f"freight.leakage_gap_{side}", "market_structure trade_disruption", [.05,.1,.15], [.1,.6])
        add(f"freight.leakage_share_{side}", "market_structure competitor_action", [.05,.1,.15], [0,.3])
    for trade in TRADES:
        add(f"freight.non_usd_share.{trade}", "payment_rule fx_policy company_announcement", [.02,.05,.1], [0,.3])
    add("freight.delay_affected_share", "trade_disruption payment_rule company_announcement", [.1,.25,.5], [0,1])
    add("bunker.hub_weight_sin", "trade_disruption port_disruption sanction", [.05,.1,.2], [.3,.9])
    add("bunker.scrubber_share", "regulation fleet_change company_announcement", [.02,.05,.1], [0,.4])
    add("bunker.mfr_share", "company_announcement market_structure", [.1,.2,.3], [0,1])
    add("bunker.mfr_lag_months", "company_announcement", [1,2,3], [0,6])
    add("bunker.supplier_lag_days", "supplier_news payment_rule", [7,14,21], [0,90])
    add("rates.new_issue_premium_bp", "financing_news rating_change credit_event", [25,50,100], [-100,400])
    add("rates.interest_bearing_cash_share", "bank_failure capital_control company_announcement", [.05,.1,.2], [.5,1])
    add("rates.zim_tranche_on", "financing_news company_announcement", [], [0,1], mode="switch")
    for name, events, steps, cap in (("cad", "company_announcement regulation", [.05,.1,.2], .8),
                                    ("eur_debt", "company_announcement", [.1,.25,.5], 1),
                                    ("inr_brl", "company_announcement capital_control", [.1,.25,.5], 1)):
        add(f"fx.hedge_ratio.{name}", events, steps, [0,cap])
    add("fx.obligation_shift_months", "company_announcement capital_control payment_rule", [1,3,6], [0,24])
    add("fx.inr_brl_obligations_on", "company_announcement", [], [0,1], mode="switch")
    return result


CATALOGUE = _catalogue()


class Proposal(Strict):
    param_key: str
    old_value: float | bool
    new_value: float | bool
    step: Literal["small", "medium", "large"] | None = None
    effective_date: date
    review_date: date
    event_ids: list[str] = Field(min_length=1)
    justification: str = Field(min_length=1)
    proposed_by: str = Field(min_length=1)


class IntelligenceStore:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = RLock()
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS intelligence_signals (
            event_id TEXT, version INTEGER, payload TEXT, routing TEXT, received_at TEXT,
            PRIMARY KEY(event_id,version));
          CREATE TABLE IF NOT EXISTS intelligence_adjustments (
            id TEXT PRIMARY KEY, payload TEXT, status TEXT, event_versions TEXT,
            review_flag INTEGER DEFAULT 0, actor TEXT, decided_at TEXT);
          CREATE TABLE IF NOT EXISTS intelligence_audit (
            id INTEGER PRIMARY KEY, action TEXT, subject TEXT, actor TEXT, at TEXT, details TEXT);
        ''')

    def _audit(self, action, subject, actor, details):
        self.db.execute("INSERT INTO intelligence_audit(action,subject,actor,at,details) VALUES(?,?,?,?,?)",
                        (action, subject, actor, datetime.now(timezone.utc).isoformat(), json.dumps(details)))

    def _latest(self, event_id):
        return self.db.execute("SELECT * FROM intelligence_signals WHERE event_id=? ORDER BY version DESC LIMIT 1", (event_id,)).fetchone()

    def submit(self, payload: dict) -> dict:
        signal = Signal.model_validate(payload)
        route = routing(signal)
        with self.lock, self.db:
            old = self._latest(signal.event_id)
            if old and old["version"] >= signal.version:
                raise ValueError("version: stale or duplicate event version")
            self.db.execute("INSERT INTO intelligence_signals VALUES(?,?,?,?,?)",
                            (signal.event_id, signal.version, signal.model_dump_json(), json.dumps(route), datetime.now(timezone.utc).isoformat()))
            if old:
                for row in self.db.execute("SELECT * FROM intelligence_adjustments WHERE status IN ('proposed','approved')").fetchall():
                    versions = json.loads(row["event_versions"])
                    if signal.event_id in versions and versions[signal.event_id] < signal.version:
                        if row["status"] == "proposed":
                            self.db.execute("UPDATE intelligence_adjustments SET status='withdrawn' WHERE id=?", (row["id"],))
                        else:
                            self.db.execute("UPDATE intelligence_adjustments SET review_flag=1 WHERE id=?", (row["id"],))
            self._audit("signal_submitted", signal.event_id, "signal_router", {"version": signal.version, **route})
        return {**signal.model_dump(mode="json"), **route}

    def signals(self):
        with self.lock:
            rows = self.db.execute('''SELECT s.* FROM intelligence_signals s JOIN
              (SELECT event_id,MAX(version) v FROM intelligence_signals GROUP BY event_id) x
              ON s.event_id=x.event_id AND s.version=x.v ORDER BY received_at DESC''').fetchall()
            return [{**json.loads(row["payload"]), **json.loads(row["routing"])} for row in rows]

    def ingest_v8(self, result: dict):
        """V8 lacks calibrated signal confidence: imported Events are context only."""
        sources = {article["article_id"]: article for article in result["sources"]}
        ingested, skipped = [], []
        for event in result["events"]:
            articles = [sources[key] for key in event["article_ids"]]
            times = [a["published_at"] for a in articles if a.get("published_at")]
            if not times:
                skipped.append({"event_id": event["event_id"], "reason": "No source publication timestamp"})
                continue
            ident = "v8:" + event["event_id"]
            with self.lock:
                row = self._latest(ident)
            old = json.loads(row["payload"]) if row else None
            if old and old["confidence"] >= .5:
                skipped.append({"event_id": ident, "reason": "Preserving reviewed structured signal"})
                continue
            payload = {"event_id": ident, "version": old["version"] + 1 if old else 1,
                       "published_at": max(times), "headline": event["title"], "summary": event["summary"],
                       "evidence_links": list(dict.fromkeys(a["url"] for a in articles)),
                       "event_type": "other", "kind": "factual", "direction": "none", "confidence": 0.0,
                       "affected_drivers": [], "named_entities": []}
            comparable = Signal.model_validate(payload).model_dump(mode="json")
            if old and {k: v for k, v in old.items() if k != "version"} == {k: v for k, v in comparable.items() if k != "version"}:
                continue
            try:
                self.submit(payload)
                ingested.append(ident)
            except ValueError as exc:
                skipped.append({"event_id": ident, "reason": str(exc)})
        return {"ingested": ingested, "skipped": skipped,
                "note": "V8 Events have no signal confidence. These factual context signals cannot trigger scenarios or adjustments."}

    def propose(self, payload: dict):
        proposal = Proposal.model_validate(payload)
        entry = CATALOGUE.get(proposal.param_key)
        if entry is None:
            raise ValueError("param_key: not in the adjustment catalogue")
        if not proposal.justification.strip() or not proposal.proposed_by.strip():
            raise ValueError("A justification and named proposer are required")
        duration = (proposal.review_date - proposal.effective_date).days
        if not 0 < duration <= entry["max_review_days"]:
            raise ValueError("review_date: outside catalogue review window")
        with self.lock, self.db:
            signals, versions = [], {}
            for event_id in proposal.event_ids:
                row = self._latest(event_id)
                if row is None:
                    raise ValueError("event_ids: unknown source event")
                signal = Signal.model_validate_json(row["payload"])
                if signal.confidence < .7 or signal.event_type not in entry["event_types"]:
                    raise ValueError("Source event confidence or event_type cannot support this parameter")
                signals.append(signal)
                versions[event_id] = signal.version
            if entry["value_mode"] == "switch":
                if type(proposal.new_value) is not bool or type(proposal.old_value) is not bool or proposal.step is not None:
                    raise ValueError("switch: boolean direct values required")
                if any(s.kind not in {"factual", "quantitative"} for s in signals):
                    raise ValueError("switch: factual or quantitative signal required")
            else:
                if isinstance(proposal.new_value, bool) or isinstance(proposal.old_value, bool):
                    raise ValueError("Numeric parameter cannot be boolean")
                if not math.isfinite(proposal.new_value) or not math.isfinite(proposal.old_value):
                    raise ValueError("Non-finite adjustment")
                if not entry["hard_limits"][0] <= proposal.new_value <= entry["hard_limits"][1]:
                    raise ValueError("new_value: outside catalogue hard limits")
                if proposal.step:
                    amount = entry["steps"][proposal.step]
                    if not math.isclose(abs(proposal.new_value - proposal.old_value), amount, abs_tol=1e-9):
                        raise ValueError("step: does not match catalogue step amount")
                elif any(s.kind != "quantitative" for s in signals):
                    raise ValueError("Direct numeric value requires quantitative source signals")
            ident = "adj_" + uuid4().hex
            self.db.execute("INSERT INTO intelligence_adjustments(id,payload,status,event_versions) VALUES(?,?,'proposed',?)",
                            (ident, proposal.model_dump_json(), json.dumps(versions)))
            self._audit("adjustment_proposed", ident, proposal.proposed_by, payload)
        item = next(item for item in self.adjustments() if item["id"] == ident)
        sizing = proposal.param_key in {"rates.interest_bearing_cash_share", "fx.obligation_shift_months"}
        item["warnings"] = (["Future-dated sizing adjustment applies only to runs whose T0 is on or after its effective date"]
                            if sizing and proposal.effective_date > date.today() else [])
        return item

    def adjustments(self):
        with self.lock:
            return [{**json.loads(row["payload"]), "id": row["id"], "adjustment_id": row["id"],
                     "status": row["status"], "review_flag": bool(row["review_flag"]),
                     "event_versions": json.loads(row["event_versions"]), "approver": row["actor"],
                     "decided_at": row["decided_at"]}
                    for row in self.db.execute("SELECT * FROM intelligence_adjustments ORDER BY rowid DESC").fetchall()]

    def decide(self, ident: str, decision: str, actor: str):
        if decision not in {"approved", "rejected", "revoked"} or not actor.strip():
            raise ValueError("A valid decision and named human actor are required")
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM intelligence_adjustments WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise ValueError("Unknown adjustment")
            if (decision == "revoked" and row["status"] != "approved") or (decision != "revoked" and row["status"] != "proposed"):
                raise ValueError("Decision is not valid for the adjustment's current status")
            self.db.execute("UPDATE intelligence_adjustments SET status=?,actor=?,decided_at=? WHERE id=?",
                            (decision, actor.strip(), datetime.now(timezone.utc).isoformat(), ident))
            self._audit("adjustment_" + decision, ident, actor.strip(), {})
        return next(item for item in self.adjustments() if item["id"] == ident)

    def active_adjustments(self, as_of: date):
        day = as_of.isoformat()
        return sorted([a for a in self.adjustments() if a["status"] == "approved" and
                       a["effective_date"] <= day < a["review_date"]],
                      key=lambda a: (a["effective_date"], a["decided_at"]))

    def applicable_adjustments(self, as_of: date):
        """Include future-dated approvals; the engine resolves each cash-flow date."""
        return sorted([a for a in self.adjustments() if a["status"] == "approved" and
                       a["review_date"] > as_of.isoformat()],
                      key=lambda a: (a["effective_date"], a["decided_at"]))


class Decision(Strict):
    decision: Literal["approved", "rejected", "revoked"]
    actor: str = Field(min_length=1)


class Collect(Strict):
    geographic_scopes: list[str] | None = Field(default=None, min_length=1, max_length=30)
    articles: list[dict] | None = None
    workers: int = Field(default=4, strict=True, ge=1, le=8)
    max_queries: int = Field(default=7, strict=True, ge=1, le=12)
    max_articles: int = Field(default=30, strict=True, ge=1)
    analysis_batch_size: int = Field(default=3, strict=True, ge=1, le=4)
    analysis_workers: int = Field(default=2, strict=True, ge=1, le=8)


def create_router(store: IntelligenceStore, reader: NewsV8Reader, dependencies=None, fx_rates=None):
    router = APIRouter(prefix="/api/v1/intelligence", dependencies=dependencies or [], tags=["intelligence-v8"])
    from .forecast import create_forecast_router
    router.include_router(create_forecast_router(store, reader, fx_rates=fx_rates))

    @router.get("/events")
    def events():
        return reader.events()

    @router.post("/import-result")
    def import_result(payload: dict):
        try:
            result = reader.import_result(payload)
            return {**result, "signal_ingestion": store.ingest_v8(result)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.get("/collection-status")
    def collection_status():
        import os
        from pathlib import Path
        from .news_progress import news_progress
        root = Path(os.environ.get("TREASURY_WORKSPACE", "."))
        status = news_progress(os.environ.get("NEWS_V8_DATABASE", str(root / "data/news-mvp-v8-id-constrained-v2.sqlite")))
        if status["status"] == "idle":
            saved, _ = reader.latest()
            if saved:
                status = {**status, "run_id": saved["run_id"], "status": saved["status"], "errors": saved["errors"], "warnings": saved.get("metrics", {}).get("warnings", [])}
        return status

    @router.post("/collect")
    def collect(body: Collect):
        try:
            result = reader.collect(body.model_dump(exclude_none=True))
            return {**result, "signal_ingestion": store.ingest_v8(result)}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/signals")
    def signals():
        return {"signals": store.signals()}

    @router.post("/signals")
    def submit(payload: dict | list[dict]):
        accepted, rejected, results = [], [], []
        for item in payload if isinstance(payload, list) else [payload]:
            try:
                result = store.submit(item)
                accepted.append(result["event_id"])
                results.append(result)
            except ValueError as exc:
                rejected.append({"event_id": item.get("event_id"), "errors": [str(exc)]})
        return {"accepted": accepted, "rejected": rejected, "results": results}

    @router.get("/catalogue")
    def catalogue():
        return {"catalogue": CATALOGUE}

    @router.get("/adjustments")
    def adjustments():
        return {"adjustments": store.adjustments()}

    @router.post("/adjustments")
    def propose(body: Proposal):
        try:
            return store.propose(body.model_dump(mode="json"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/adjustments/{ident}/decision")
    def decide(ident: str, body: Decision):
        try:
            return store.decide(ident, body.decision, body.actor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    return router
