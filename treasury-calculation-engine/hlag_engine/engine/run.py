"""Orchestrator (spec 4.1, 4.7): run_scenario and replay. Deterministic: no LLM, network or randomness.

Steps: validate; liquidity inputs (T1 rolled forward to T0, P77); run context (three-layer resolver with
approved adjustments, plus unapproved ones for auto_built runs; market snapshot with stale flags; recent history;
one exposure register, deposits sized on the rolled-forward cash); fill params.rates.zim_tranche_on from the
resolved profile key (P50); baseline pass with zero_shock() and scenario pass, each running freight, bunker (on
that pass's volume lines), rates, fx; snap dates; 9A conversion and policy hedges; pair on the stable key; deltas,
attribution (full horizon and up to the minimum-headroom date) and G_d; anchor adjustment; liquidity path, tiers
and liquidity tests; Tier B status, desk exceptions and alerts; run summary; run record in one transaction.
"""

import hashlib
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from hlag_engine.company_profile.registers import Registers, load_registers
from hlag_engine.company_profile.resolve import RunContext, resolve
from hlag_engine.company_profile.scenario_validation import load_scenario_ranges, validate_scenario
from hlag_engine.company_profile.schema import MissingProfileValue, Profile, load_profile
from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import AdjustmentStatus, HedgeInstrument, LineCode, PassType
from hlag_engine.contracts.exposure import ExposureRegister, MissingInput
from hlag_engine.contracts.run_record import MarketSnapshotEntry, RunRecord
from hlag_engine.contracts.run_summary import RunFlags, RunSummary
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.data.store import Store
from hlag_engine.engine.anchor import GROUPS, AnchorAdjustment, anchor_adjustment
from hlag_engine.engine.attribution import Attribution, GrossAdverse, attribute, cumulative_delta, gross_adverse
from hlag_engine.engine.conversion import apply_policy_hedges, convert_lines, fx_paths, translation_memo
from hlag_engine.engine.direct import direct_cash_lines
from hlag_engine.engine.liquidity import (
    LiquidityInputs,
    Paths,
    Tiers,
    TreatmentFlags,
    build_paths,
    consumed_pct,
    first_breach,
    from_profile,
    margin_test,
    minimum,
    tiers,
)
from hlag_engine.engine.pairing import Pair, pair_lines
from hlag_engine.engine.record import ReplayError, canonical, load_profile_version, next_run_id, registers_hash, to_jsonable, write_run
from hlag_engine.engine.snapshot import build_history, build_snapshot, default_as_of
from hlag_engine.engine.status import TierB, desk_exceptions, explanations, load_tier_b, tier_b_status
from hlag_engine.engine.summary import build_summary
from hlag_engine.engine.timeline import snap
from hlag_engine.generator.generate import generate
from hlag_engine.modules.bunker import BunkerModule
from hlag_engine.modules.freight import FreightModule
from hlag_engine.modules.fx import FxModule
from hlag_engine.modules.interface import (
    DriverModule,
    ModuleInputError,
    ModuleInputs,
    ModuleOutput,
    ModuleRunContext,
    ProfileContext,
    SnapshotEntry,
)
from hlag_engine import PACKAGE_DIR
from hlag_engine.modules.rates import RatesModule

DEFAULT_STORE_PATH = PACKAGE_DIR / "data" / "market.sqlite"
ORDER = ("freight", "bunker", "rates", "fx")
FX_K_KEY = "fx.policy_hedge_k"
M = Decimal(1_000_000)

RegisterBuilder = Callable[[Profile, date, RunContext, float | None], ExposureRegister]


def default_modules() -> list[DriverModule]:
    return [FreightModule(), BunkerModule(), RatesModule(), FxModule()]


def default_register(profile: Profile, as_of: date, resolver: RunContext, cash_at_t0: float | None) -> ExposureRegister:
    return generate(profile, as_of, resolver, cash_at_t0=cash_at_t0)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Liquidity:
    inputs: LiquidityInputs
    paths: Paths
    base: Tiers
    scen: Tiers
    h_b: Decimal
    h_b_date: date
    h_s: Decimal
    h_s_date: date
    c_pct: Decimal | None
    breach: date | None
    anchor: AnchorAdjustment
    anchor_headline: dict[str, Decimal]
    margin: dict[str, Any]


@dataclass
class RunResult:
    record: RunRecord
    summary: RunSummary
    base_lines: list[CashLine]  # snapped and converted, including policy hedge settlements
    scen_lines: list[CashLine]
    pairs: list[Pair]
    attribution: Attribution
    attribution_to_min: Attribution
    gross_adverse: dict[str, GrossAdverse]
    liquidity: Liquidity
    outputs: dict[PassType, dict[str, ModuleOutput]]
    missing: list[MissingInput]
    warnings: list[str] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.record.run_id

    @property
    def total_delta_usd(self) -> Decimal:
        return self.attribution.total


class Engine:
    def __init__(self, store: Store, profile: Profile | None = None, registers: Registers | None = None,
                 modules: Sequence[DriverModule] | None = None, clock: Callable[[], datetime] = utc_now,
                 liquidity: LiquidityInputs | None = None, register_builder: RegisterBuilder | None = None):
        self.store = store
        self.profile = profile
        self.registers = registers
        self.modules = list(modules) if modules is not None else default_modules()
        self.clock = clock
        self.liquidity_override = liquidity  # fixtures (walkthrough, joint test) state their own starting tiers
        self.register_builder = register_builder or default_register
        self.tier_b: TierB = load_tier_b()
        self._anchor_cache: dict[str, AnchorAdjustment] = {}
        self._pass_cache: OrderedDict[str, tuple] = OrderedDict()  # zero-shock base passes by inputs (P85)
        names = [m.name for m in self.modules]
        if names != list(ORDER):
            raise ValueError(f"modules must run in the order {ORDER}, got {names}")

    # ---------------------------------------------------------------- public
    def run_scenario(self, params: ScenarioParams, as_of: date | None = None, write: bool = True,
                     pair_id: str | None = None, fill_zim: bool = True) -> RunResult:
        """fill_zim: set params.rates.zim_tranche_on from the profile (P50); run_pair passes False for the run
        whose state change sets the switch explicitly. pair_id: set by run_pair on both records."""
        profile = self.profile or load_profile()
        registers = self.registers or load_registers()
        as_of = as_of or default_as_of(self.store)
        validate_scenario(params, profile, load_scenario_ranges())
        adjustments = self._adjustments(params)
        snapshot = build_snapshot(self.store, as_of)
        history = build_history(self.store, as_of)
        return self._run(params, as_of, profile, registers, adjustments, snapshot, history, {}, write,
                         liquidity=self.liquidity_override, pair_id=pair_id, fill_zim=fill_zim)

    def replay(self, run_id: str) -> RunResult:
        """Rebuild a run from its record alone (4.7): profile version, adjustments, overrides, stored snapshot
        values, derived inputs and liquidity inputs, never the live market store. Writes nothing."""
        record = self.store.get_run_record(run_id)
        if record is None:
            raise ReplayError(f"unknown run {run_id}")
        if self.profile is not None and self.profile.content_hash == record.profile_hash:
            profile = self.profile
        else:
            profile = load_profile_version(record.profile_version, record.profile_hash)
        registers = self.registers or load_registers()
        snapshot = {
            sid: SnapshotEntry(value=float(e.value), value_date=e.value_date, unit=e.unit or "",
                               days_since_print=e.days_since_print, label=e.label, confirmed=e.confirmed,
                               filled=e.filled, stale=e.stale)
            for sid, e in record.market_snapshot.items()
        }
        stored_liquidity = record.results["liquidity_inputs"]
        result = self._run(record.params, record.as_of, profile, registers, list(record.adjustments), snapshot, {},
                           dict(record.derived_inputs), write=False, run_id=record.run_id,
                           created_at=record.created_at, liquidity=LiquidityInputs.from_dict(stored_liquidity),
                           liquidity_from_profile=stored_liquidity.get("roll_forward") is not None,
                           pair_id=record.pair_id, fill_zim=False)  # the record holds the filled params
        if registers_hash(registers) != record.registers_hash:
            result.warnings.append("registers have changed since the run (registers are not versioned, P62)")
        return result

    def replay_differences(self, run_id: str) -> list[str]:
        """Empty when replay reproduces the stored record and cash lines exactly."""
        stored = self.store.get_run_record(run_id)
        again = self.replay(run_id)
        diffs = []
        for name in ("results", "lines", "flags", "status", "alerts", "market_snapshot", "derived_inputs", "params"):
            if canonical(getattr(stored, name)) != canonical(getattr(again.record, name)):
                diffs.append(name)
        stored_lines = sorted(canonical(l) for l in self.store.get_cash_lines(run_id))
        new_lines = sorted(canonical(l) for l in self._stored_lines(again.record, again.base_lines + again.scen_lines))
        if stored_lines != new_lines:
            diffs.append("cash_lines")
        return diffs

    # ---------------------------------------------------------------- internals
    def _adjustments(self, params: ScenarioParams) -> list[Adjustment]:
        approved = self.store.list_adjustments(AdjustmentStatus.APPROVED)
        unapproved = []
        for adj_id in params.unapproved_adjustment_ids:
            adj = self.store.get_adjustment(adj_id)
            if adj is None:
                raise ValueError(f"unknown adjustment {adj_id}")
            unapproved.append(adj)
        return approved + unapproved

    def _pass(self, pass_type: PassType, params: ScenarioParams, resolver: RunContext, snapshot, history, derived,
              register: ExposureRegister, reg_hash: str | None = None) -> tuple[dict[str, ModuleOutput], list[MissingInput]]:
        key = None
        if pass_type is PassType.BASE and reg_hash is not None:
            key = hashlib.sha256(canonical({
                "profile": resolver.profile.content_hash, "as_of": resolver.as_of, "snapshot": snapshot,
                "params": _base_pass_key(params), "adjustments": list(resolver.adjustments),
                "unapproved": sorted(resolver.unapproved_ids),
                "derived": derived, "register": reg_hash, "modules": [type(m).__name__ for m in self.modules],
            }).encode()).hexdigest()
            if key in self._pass_cache:
                self._pass_cache.move_to_end(key)
                return self._pass_cache[key]
        result = self._run_modules(pass_type, params, resolver, snapshot, history, derived, register)
        if key is not None:
            self._pass_cache[key] = result
            while len(self._pass_cache) > 64:
                self._pass_cache.popitem(last=False)
        return result

    def _run_modules(self, pass_type, params, resolver, snapshot, history, derived, register):
        ctx = ModuleRunContext(ProfileContext(resolver), pass_type, snapshot, history, derived)
        outs: dict[str, ModuleOutput] = {}
        errors: list[MissingInput] = []
        for module in self.modules:
            volume = outs["freight"].volume_lines if module.name == "bunker" else ()
            try:
                out = module.run(ctx, params, register.items, ModuleInputs(tuple(volume)))
            except (ModuleInputError, MissingProfileValue) as exc:
                out = ModuleOutput()
                errors.append(MissingInput(key=getattr(exc, "key", module.name), status="module_error",
                                           affects=f"{module.name} ({pass_type.value} pass): {exc}"))
            outs[module.name] = out
        return outs, errors

    def _run(self, params: ScenarioParams, as_of: date, profile: Profile, registers: Registers,
             adjustments: list[Adjustment], snapshot: dict[str, SnapshotEntry],
             history: dict[str, list], stored_derived: dict[str, Any], write: bool,
             run_id: str | None = None, created_at: datetime | None = None,
             liquidity: LiquidityInputs | None = None, liquidity_from_profile: bool | None = None,
             pair_id: str | None = None, fill_zim: bool = True) -> RunResult:
        resolver = RunContext.for_scenario(profile, as_of, params, tuple(adjustments), registers)
        if fill_zim and not params.rates.zim_tranche_on and resolve("rates.zim_tranche_on", as_of, resolver):
            params = params.model_copy(update={"rates": params.rates.model_copy(update={"zim_tranche_on": True})})
        from_prof = liquidity is None if liquidity_from_profile is None else liquidity_from_profile
        liq_inputs = liquidity or from_profile(resolver)
        register = self.register_builder(profile, as_of, resolver, float(liq_inputs.t1_start) if from_prof else None)
        reg_hash = hashlib.sha256(canonical(register.items).encode()).hexdigest()
        fx_k = stored_derived.get(FX_K_KEY)
        module_derived = {k: v for k, v in stored_derived.items() if k != FX_K_KEY}

        outputs: dict[PassType, dict[str, ModuleOutput]] = {}
        errors: list[MissingInput] = []
        for pass_type in (PassType.BASE, PassType.SCENARIO):
            run_params = params.zero_shock() if pass_type is PassType.BASE else params
            outputs[pass_type], errs = self._pass(pass_type, run_params, resolver, snapshot, history, module_derived,
                                                  register, reg_hash)
            errors += errs

        lines = {pt: snap([l for name in ORDER for l in outs[name].cash_lines], as_of, pt) for pt, outs in outputs.items()}
        memo_usd = outputs[PassType.BASE]["fx"].diagnostics.get("memo_positions_usd", {})
        currencies = {l.currency for ls in lines.values() for l in ls} | set(memo_usd)
        paths = fx_paths(snapshot, params, currencies)
        hedges = {
            pt: apply_policy_hedges(outputs[pt]["fx"].policy_hedges, lines[pt], paths, pt, as_of, history, fx_k)
            for pt in (PassType.BASE, PassType.SCENARIO)
        }
        # Direct cash flows: scenario pass only, USD, never FX-hedged (deviation from spec; engine/direct.py).
        direct, direct_watch = direct_cash_lines(params, as_of, ProfileContext(resolver).horizon[1])
        direct = snap(direct, as_of, PassType.SCENARIO)
        base = convert_lines(lines[PassType.BASE] + hedges[PassType.BASE].settlement_lines, paths)
        scen = convert_lines(lines[PassType.SCENARIO] + hedges[PassType.SCENARIO].settlement_lines + direct, paths)
        pairs = pair_lines(base, scen)
        attribution = attribute(pairs)
        gross = gross_adverse(pairs, {m.name: set(m.gross_codes) for m in self.modules}, self._rates_sides())

        flags_t = TreatmentFlags.from_resolver(resolver)
        anchor = self._anchor(profile, as_of, params, resolver, snapshot, history, module_derived, register,
                              flags_t, base, adjustments, reg_hash)
        liq = self._liquidity(liq_inputs, base, scen, pairs, flags_t, as_of, resolver, registers, anchor, params)
        attribution_to_min = attribute(pairs, up_to=liq.h_s_date)

        missing = list(register.missing) + errors + hedges[PassType.BASE].missing + anchor.missing
        for name in ORDER:
            missing += [m for m in outputs[PassType.BASE][name].diagnostics.get("missing", [])]
        missing = _dedupe(missing)
        material = self._material_status(snapshot)
        stale_or_missing = material["missing"] + material["stale"]
        incomplete_reasons = ([f"stale or missing material series: {', '.join(stale_or_missing)}"] if stale_or_missing else []) \
            + ([f"missing inputs: {', '.join(sorted({m.key for m in missing}))}"] if missing else [])
        proxy = bool(outputs[PassType.BASE]["bunker"].diagnostics.get("proxy_based"))
        status = tier_b_status(liq.c_pct, liq.h_b <= 0, any(v < 0 for v in liq.scen.headroom.values()), proxy,
                               incomplete_reasons, self.tier_b)
        margin_short = [(name, m["lowest"], m["shortfall_date"]) for name, m in liq.margin.items()
                        if m and m["shortfall_date"] is not None]
        exceptions = desk_exceptions(gross, liq.h_b, self.tier_b, margin_short, stale_or_missing)

        derived = {name: outputs[PassType.BASE][name].diagnostics["derived_inputs"]
                   for name in ORDER if "derived_inputs" in outputs[PassType.BASE][name].diagnostics}
        derived[FX_K_KEY] = {
            c.key: {"rate": c.contract_rate, "source_date": c.k_source_date.isoformat() if c.k_source_date else None}
            for c in hedges[PassType.BASE].contracts if c.k_source == "history"
        }
        flags = RunFlags(
            proxy_based=proxy,
            uses_unconfirmed_data=any(not snapshot[s].confirmed for s in self._material_series() if s in snapshot),
            includes_unapproved_adjustments=bool(params.unapproved_adjustment_ids),
            incomplete_data=status.incomplete_data,
        )
        rid = run_id or next_run_id(self.store, as_of, params.name)
        alerts = explanations(rid, as_of, status, liq.c_pct, liq.h_b, exceptions, self.tier_b, list(params.event_ids))
        horizon_end = ProfileContext(resolver).horizon[1]
        record = RunRecord(
            run_id=rid, created_at=created_at or self.clock(), as_of=as_of, params=params,
            profile_version=profile.profile_version, profile_hash=profile.content_hash,
            adjustment_ids=[a.adjustment_id for a in adjustments], adjustments=adjustments,
            market_snapshot={sid: MarketSnapshotEntry(value=Decimal(repr(e.value)), value_date=e.value_date,
                                                      days_since_print=e.days_since_print, confirmed=e.confirmed,
                                                      unit=e.unit, label=e.label, filled=e.filled, stale=e.stale)
                             for sid, e in sorted(snapshot.items())},
            derived_inputs=to_jsonable(derived), registers_hash=registers_hash(registers),
            lines=to_jsonable({
                "cash_lines": {"base": len(base), "scenario": len(scen)},
                "volume_lines": {pt.value: outputs[pt]["freight"].volume_lines for pt in outputs},
                "policy_hedges": outputs[PassType.BASE]["fx"].policy_hedges,
                "hedge_contracts": {pt.value: [_contract(c) for c in hedges[pt].contracts] for pt in hedges},
                "memo_positions_usd": memo_usd,
            }),
            results={},  # filled below, once the summary exists
            status=status.status, flags=flags, alerts=alerts, pair_id=pair_id,
        )
        summary = build_summary(record, status, liq, attribution, exceptions, alerts)
        record = record.model_copy(update={"results": to_jsonable({
            "summary": summary,
            "status_reasons": status.reasons,
            "computed_status": status.computed,
            "pairs": len(pairs),
            "total_delta_usd": attribution.total,
            "attribution_full_horizon_usd": {**attribution.bars(), "total": attribution.total},
            "attribution_full_horizon_usd_m": _usd_m({**attribution.bars(), "total": attribution.total}),
            "attribution_to_min_headroom_usd": {**attribution_to_min.bars(), "total": attribution_to_min.total,
                                                "up_to": liq.h_s_date},
            "attribution_to_min_headroom_usd_m": _usd_m({**attribution_to_min.bars(), "total": attribution_to_min.total}),
            "gross_adverse_usd": {d: {"value": g.value, "date": g.date} for d, g in gross.items()},
            "paired_metrics": paired_metrics(pairs, self.modules),
            "cumulative_delta_usd": cumulative_delta(pairs),
            "liquidity_inputs": liq.inputs.to_dict(),
            "liquidity": _liquidity_results(liq),
            "translation_memo_usd": translation_memo(memo_usd, paths, horizon_end) if memo_usd else {},
            "missing_inputs": missing,
            "material_series": material,
            "module_metrics": {pt.value: {n: o.metrics for n, o in outs.items()} for pt, outs in outputs.items()},
            "notes": {n: o.notes for n, o in outputs[PassType.BASE].items()},
            "watch_list": {pt.value: {n: o.watch_list for n, o in outs.items()} for pt, outs in outputs.items()},
            "direct_cash": {"label": "ASSUMPTION", "total_usd": attribution.direct,
                            "lines": [{"effect_id": l.sub_id, "date": l.date, "amount_usd": l.amount_usd_scenario}
                                      for l in direct],
                            "watch_list": direct_watch},
        })})
        result = RunResult(record, summary, base, scen, pairs, attribution, attribution_to_min, gross, liq, outputs,
                           missing)
        if write:
            write_run(self.store, record, self._stored_lines(record, base + scen))
        return result

    # ---------------------------------------------------------------- anchor adjustment (3.7, P79)
    def _anchor(self, profile, as_of, params, resolver, snapshot, history, derived, register, flags_t, base_lines,
                adjustments, reg_hash) -> AnchorAdjustment:
        key = hashlib.sha256(canonical({
            "profile": profile.content_hash, "as_of": as_of, "snapshot": snapshot,
            "adjustments": adjustments, "overrides": params.overrides,
            "state": {"zim": params.rates.zim_tranche_on, "hedge_tests": params.hedge_tests},
            "register": reg_hash, "modules": [type(m).__name__ for m in self.modules],
        }).encode()).hexdigest()
        if key in self._anchor_cache:
            return self._anchor_cache[key]
        zero = params.zero_shock()

        def base_pass(step_snapshot):
            outs, errs = self._pass(PassType.BASE, zero, resolver, step_snapshot, history, derived, register, reg_hash)
            step_lines = snap([l for name in ORDER for l in outs[name].cash_lines], as_of, PassType.BASE)
            step_paths = fx_paths(step_snapshot, zero, {l.currency for l in step_lines})
            return convert_lines(step_lines, step_paths), errs

        result = anchor_adjustment(profile, snapshot, flags_t, base_lines, base_pass)
        self._anchor_cache[key] = result
        return result

    # ---------------------------------------------------------------- anchored baseline (4.10, D71)
    def anchored_baseline(self, as_of: date, snapshot: dict[str, SnapshotEntry]) -> tuple[Decimal, date]:
        """Minimum baseline headroom H_b* (anchor adjustment included) at T0 = as_of on a given market snapshot,
        with the active profile, approved adjustments and today's history. Used by the baseline change (P86)."""
        profile = self.profile or load_profile()
        registers = self.registers or load_registers()
        params = ScenarioParams(name="baseline change step", start_date=as_of, source="scheduled")
        adjustments = self._adjustments(params)
        resolver = RunContext.for_scenario(profile, as_of, params, tuple(adjustments), registers)
        if resolve("rates.zim_tranche_on", as_of, resolver):
            params = params.model_copy(update={"rates": params.rates.model_copy(update={"zim_tranche_on": True})})
        liq_inputs = self.liquidity_override or from_profile(resolver)
        register = self.register_builder(profile, as_of, resolver,
                                         None if self.liquidity_override else float(liq_inputs.t1_start))
        reg_hash = hashlib.sha256(canonical(register.items).encode()).hexdigest()
        history = build_history(self.store, as_of)
        outs, _ = self._pass(PassType.BASE, params, resolver, snapshot, history, {}, register, reg_hash)
        lines = snap([l for name in ORDER for l in outs[name].cash_lines], as_of, PassType.BASE)
        memo = outs["fx"].diagnostics.get("memo_positions_usd", {})
        paths = fx_paths(snapshot, params, {l.currency for l in lines} | set(memo))
        hedges = apply_policy_hedges(outs["fx"].policy_hedges, lines, paths, PassType.BASE, as_of, history)
        base = convert_lines(lines + hedges.settlement_lines, paths)
        flags_t = TreatmentFlags.from_resolver(resolver)
        anchor = self._anchor(profile, as_of, params, resolver, snapshot, history, {}, register, flags_t, base,
                              adjustments, reg_hash)
        path = build_paths(liq_inputs, base, flags_t, as_of, {}, anchor.total)
        low = minimum(tiers(path.l_base, liq_inputs, resolver, registers).headroom)
        return low.value, low.date

    # ---------------------------------------------------------------- liquidity (3.7, 4.4)
    def _liquidity(self, inputs: LiquidityInputs, base, scen, pairs, flags_t, as_of, resolver, registers,
                   anchor: AnchorAdjustment, params: ScenarioParams) -> Liquidity:
        delta_by_date: dict[date, Decimal] = defaultdict(Decimal)
        for p in pairs:
            delta_by_date[p.ref.date] += p.delta_usd
        paths = build_paths(inputs, base, flags_t, as_of, dict(delta_by_date), anchor.total)
        t_base = tiers(paths.l_base, inputs, resolver, registers)
        t_scen = tiers(paths.l_scen, inputs, resolver, registers)
        mb, ms = minimum(t_base.headroom), minimum(t_scen.headroom)
        headline = {}
        if anchor.computed:
            cumulative: dict[date, Decimal] = {}
            before = minimum(tiers(build_paths(inputs, base, flags_t, as_of, {}, {}).l_base, inputs, resolver,
                                   registers).headroom).value
            for group, _ in GROUPS:
                for d, a in anchor.by_driver.get(group, {}).items():
                    cumulative[d] = cumulative.get(d, Decimal(0)) + a
                after = minimum(tiers(build_paths(inputs, base, flags_t, as_of, {}, dict(cumulative)).l_base, inputs,
                                      resolver, registers).headroom).value
                headline[group] = after - before
                before = after
        margin = {}
        if any(t.instrument is HedgeInstrument.SWAP for t in params.hedge_tests):
            for name, tier_set, lines_, attr in (("base", t_base, base, "amount_usd_base"),
                                                 ("scenario", t_scen, scen, "amount_usd_scenario")):
                test = margin_test(tier_set, [l for l in lines_ if l.line_code is LineCode.MARGIN_CALL], attr, as_of)
                margin[name] = {"lowest": test.lowest, "lowest_date": test.lowest_date,
                                "shortfall_date": test.shortfall_date, "passed": test.passed}
        return Liquidity(inputs, paths, t_base, t_scen, mb.value, mb.date, ms.value, ms.date,
                         consumed_pct(mb.value, ms.value), first_breach(t_base.headroom, t_scen.headroom),
                         anchor, headline, margin)

    def _stored_lines(self, record: RunRecord, lines: list[CashLine]) -> list[CashLine]:
        event_ids = list(record.params.event_ids)
        return [l.model_copy(update={"run_id": record.run_id, "event_ids": event_ids}) for l in lines]

    def _rates_sides(self):
        rates = next(m for m in self.modules if m.name == "rates")
        debt = set(getattr(rates, "debt_side_codes", rates.gross_codes))
        cash = set(getattr(rates, "cash_side_codes", set()))
        return debt, cash

    def _material_series(self) -> list[str]:
        return sorted({s for m in self.modules for s in m.material_series})

    def _material_status(self, snapshot: dict[str, SnapshotEntry]) -> dict[str, list[str]]:
        series = self._material_series()
        return {"missing": [s for s in series if s not in snapshot],
                "stale": [s for s in series if s in snapshot and snapshot[s].stale]}


def paired_metrics(pairs: Sequence[Pair], modules: Sequence[DriverModule]) -> dict:
    """Upside and downside per driver (favourable and adverse pair deltas) and gross vs net per driver (P87)."""
    up_down: dict[str, dict[str, Decimal]] = {}
    gross_net: dict[str, dict[str, Decimal]] = {}
    gross_codes = {m.name: set(m.gross_codes) for m in modules}
    for p in pairs:
        owner = p.ref.owner_spec.value
        d = p.delta_usd
        entry = up_down.setdefault(owner, {"upside": Decimal(0), "downside": Decimal(0)})
        entry["upside" if d > 0 else "downside"] += d if d else Decimal(0)
        g = gross_net.setdefault(owner, {"gross": Decimal(0), "net": Decimal(0)})
        g["net"] += d
        if p.ref.line_code in gross_codes.get(owner, set()):
            g["gross"] += d
    fx = gross_net.setdefault("fx", {"gross": Decimal(0), "net": Decimal(0)})
    fx["gross"] = sum((p.b * (p.y - p.x) for p in pairs if p.ref.line_code is not LineCode.HEDGE_SETTLEMENT), Decimal(0))
    return {"upside_downside_usd": up_down, "gross_net_usd": gross_net,
            "note": "net includes offsets (MFR, hedges); net depends on the assumed MFR share (7.8)"}


META_FIELDS = ("name", "start_date", "duration_days", "default_shape", "ramp_or_revert_weeks", "source", "event_ids",
               "preset_id")


def _base_pass_key(params: ScenarioParams) -> dict:
    """A zero-shock pass depends on state and overrides, not on the scenario's descriptive (meta) fields or shapes,
    so presets of one day share their base pass (P85)."""
    dumped = params.zero_shock().model_dump(mode="json")
    for f in META_FIELDS:
        dumped.pop(f, None)
    for block in ("freight", "bunker", "fx"):
        dumped[block].pop("shape", None)
    return dumped


def snapshot_from_record(record: RunRecord) -> dict[str, SnapshotEntry]:
    return {sid: SnapshotEntry(value=float(e.value), value_date=e.value_date, unit=e.unit or "",
                               days_since_print=e.days_since_print, label=e.label, confirmed=e.confirmed,
                               filled=e.filled, stale=e.stale)
            for sid, e in record.market_snapshot.items()}


def _dedupe(items: list[MissingInput]) -> list[MissingInput]:
    seen, out = set(), []
    for m in items:
        k = (m.key, m.status, m.affects)
        if k not in seen:
            seen.add(k)
            out.append(m)
    return out


def _usd_m(values: dict[str, Decimal]) -> dict[str, float]:
    return {k: round(float(v / M), 3) for k, v in values.items()}


def _liquidity_results(liq: Liquidity) -> dict:
    p = liq.paths
    return {
        "h_b_min_usd": liq.h_b, "h_b_min_date": liq.h_b_date, "h_s_min_usd": liq.h_s, "h_s_min_date": liq.h_s_date,
        "consumed_pct": liq.c_pct, "first_breach_date": liq.breach,
        "anchor_adjustment": {"computed": liq.anchor.computed, "note": liq.anchor.note, "label": "ASSUMPTION",
                              "by_driver": liq.anchor.by_driver, "headline_change_in_min_headroom_usd": liq.anchor_headline},
        "margin_test": liq.margin,
        "availability_cuts": {"base": liq.base.cuts, "scenario": liq.scen.cuts},
        "path": {d: {"baseline_flow": p.baseline_flow.get(d, Decimal(0)), "anchor": p.anchor.get(d, Decimal(0)),
                     "delta": p.delta.get(d, Decimal(0)), "l_base": p.l_base[d], "l_scen": p.l_scen[d],
                     "t1_base": liq.base.t1[d], "t1_scen": liq.scen.t1[d], "t2": liq.base.t2[d], "t3": liq.base.t3[d],
                     "t4_scen": liq.scen.t4[d], "h_base": liq.base.headroom[d], "h_scen": liq.scen.headroom[d]}
                 for d in p.dates},
    }


def _contract(c) -> dict:
    return {"policy": c.spec.policy.value, "hedged_sub_id": c.spec.hedged_sub_id, "currency": c.spec.currency.value,
            "notional": c.notional, "value_date": c.value_date, "contract_rate": c.contract_rate,
            "k_source": c.k_source, "k_source_date": c.k_source_date, "spot_at_value": c.spot_at_value,
            "settlement_usd": c.settlement_usd}


def run_scenario(params: ScenarioParams, as_of: date | None = None, engine: Engine | None = None) -> RunResult:
    """run_scenario (4.1): with no engine given, the active profile and the default store file."""
    if engine is None:
        engine = Engine(Store(DEFAULT_STORE_PATH))
    return engine.run_scenario(params, as_of)


__all__ = ["DEFAULT_STORE_PATH", "Engine", "RunResult", "run_scenario"]
