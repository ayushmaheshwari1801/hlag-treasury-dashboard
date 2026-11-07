"""State comparisons (spec 4.1 run_pair, D28) and the hypothetical hedge tool (5.3 test_hedge), P87.

run_pair runs the same scenario twice: run A as given, run B with the state change applied. State changes:
overrides (merged), hedge_tests (replaced), zim_tranche_on (explicit, no profile fill) and inr_brl_obligations_on
(as the profile override fx.inr_brl_obligations_on, P66). Both records get the same pair_id. The difference is
run B minus run A: scenario minimum headroom, the attribution bars and scenario-pass USD totals by line code,
labelled "state comparison". State is applied identically in both passes of each run, so it never appears as a
scenario delta.

test_hedge pairs the scenario without and with the hedge, and the mirrored scenario (every shock sign reversed)
without and with it: saving = H_s*(with) - H_s*(without) under the given scenario; cost = H_s*(without) -
H_s*(with) under the mirror. Mirror: price, rate, FX and volume moves, rate steps, the credit-spread change and the
consumption uplift are negated; transit delays become 0 (they cannot be negative); a negated value outside its
policy range is clamped to the range and noted.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from hlag_engine.company_profile.scenario_validation import ScenarioRanges, load_scenario_ranges
from hlag_engine.contracts.enums import LineCode
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.contracts.pair_result import DatedAmount, HedgeMetrics, MarginTest, PairDifference, PairResult
from hlag_engine.contracts.run_summary import Attribution as AttributionM
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.engine.record import next_pair_id
from hlag_engine.engine.run import Engine, RunResult
from hlag_engine.engine.snapshot import default_as_of

M = Decimal(1_000_000)
STATE_KEYS = ("overrides", "hedge_tests", "zim_tranche_on", "inr_brl_obligations_on")


def usd_m(x: Decimal) -> float:
    return round(float(x / M), 3)


def apply_state_change(params: ScenarioParams, change: dict[str, Any]) -> tuple[ScenarioParams, bool]:
    """(params with the state change, whether the ZIM switch is set explicitly)."""
    unknown = set(change) - set(STATE_KEYS)
    if unknown:
        raise ValueError(f"not a state change: {sorted(unknown)} (allowed: {', '.join(STATE_KEYS)})")
    update: dict[str, Any] = {}
    overrides = dict(params.overrides)
    if "overrides" in change:
        overrides.update(change["overrides"])
    if "inr_brl_obligations_on" in change:
        overrides["fx.inr_brl_obligations_on"] = bool(change["inr_brl_obligations_on"])
    if overrides != params.overrides:
        update["overrides"] = overrides
    if "hedge_tests" in change:
        update["hedge_tests"] = [HedgeTest.model_validate(h) for h in change["hedge_tests"]]
    explicit_zim = "zim_tranche_on" in change
    if explicit_zim:
        update["rates"] = params.rates.model_copy(update={"zim_tranche_on": bool(change["zim_tranche_on"])})
    return ScenarioParams.model_validate({**params.model_dump(), **{k: v for k, v in update.items()}}), explicit_zim


def _line_totals(result: RunResult) -> dict[LineCode, Decimal]:
    totals: dict[LineCode, Decimal] = defaultdict(Decimal)
    for line in result.scen_lines:
        totals[line.line_code] += line.amount_usd_scenario
    return totals


def difference(a: RunResult, b: RunResult) -> PairDifference:
    ta, tb = _line_totals(a), _line_totals(b)
    bars = {k: b.attribution.bars()[k] - a.attribution.bars()[k] for k in ("freight", "bunker", "rates", "fx", "direct")}
    return PairDifference(
        min_headroom_usd_m=usd_m(b.liquidity.h_s - a.liquidity.h_s),
        attribution_usd_m=AttributionM(**{k: usd_m(v) for k, v in bars.items()},
                                       total=usd_m(b.attribution.total - a.attribution.total)),
        line_totals_usd_m={code: usd_m(tb.get(code, Decimal(0)) - ta.get(code, Decimal(0)))
                           for code in sorted(set(ta) | set(tb), key=lambda c: c.value)},
    )


@dataclass
class PairRuns:
    result: PairResult
    run_a: RunResult
    run_b: RunResult
    notes: list[str] = field(default_factory=list)


def run_pair(engine: Engine, params: ScenarioParams, state_change: dict[str, Any], as_of=None) -> PairRuns:
    as_of = as_of or default_as_of(engine.store)
    changed, explicit_zim = apply_state_change(params, state_change)
    pair_id = next_pair_id(engine.store, as_of, params.name)
    run_a = engine.run_scenario(params, as_of, pair_id=pair_id)
    run_b = engine.run_scenario(changed, as_of, pair_id=pair_id, fill_zim=not explicit_zim)
    result = PairResult(pair_id=pair_id, run_a=run_a.summary, run_b=run_b.summary, difference=difference(run_a, run_b))
    engine.store.append_audit("engine", "pair_completed", pair_id,
                              {"run_a": run_a.run_id, "run_b": run_b.run_id, "state_change": list(state_change)})
    return PairRuns(result, run_a, run_b)


# ---------------------------------------------------------------- mirror and test_hedge (5.3)


def _clamp(value: float, bounds, name: str, notes: list[str]) -> float:
    low, high = bounds
    if value < low or value > high:
        clamped = min(max(value, low), high)
        notes.append(f"{name}: mirrored {value:g} clamped to {clamped:g}")
        return clamped
    return value


def mirror(params: ScenarioParams, ranges: ScenarioRanges | None = None) -> tuple[ScenarioParams, list[str]]:
    r = ranges or load_scenario_ranges()
    notes: list[str] = []
    f, b, rt, fx = params.freight, params.bunker, params.rates, params.fx
    freight = f.model_copy(update={
        "rate_shock_pct": {k: _clamp(-v, r.freight_rate_shock_pct, f"freight.rate_shock_pct.{k.value}", notes)
                           for k, v in f.rate_shock_pct.items()},
        "volume_shock_pct": {k: _clamp(-v, r.freight_volume_shock_pct, f"freight.volume_shock_pct.{k.value}", notes)
                             for k, v in f.volume_shock_pct.items()},
        "transit_delay_days": {},
        "rate_shock_path": {k: [p.model_copy(update={"pct": _clamp(-p.pct, r.freight_rate_shock_pct, "rate_shock_path", notes)})
                                for p in pts] for k, pts in f.rate_shock_path.items()},
    })
    if f.transit_delay_days:
        notes.append("transit delays set to 0 in the mirror (they cannot be negative)")
    bunker = b.model_copy(update={
        "vlsfo_shock_pct": _clamp(-b.vlsfo_shock_pct, r.bunker_fuel_shock_pct, "bunker.vlsfo_shock_pct", notes),
        "hsfo_shock_pct": _clamp(-b.hsfo_shock_pct, r.bunker_fuel_shock_pct, "bunker.hsfo_shock_pct", notes),
        "consumption_uplift_pct": _clamp(-b.consumption_uplift_pct, r.bunker_consumption_uplift_pct,
                                         "bunker.consumption_uplift_pct", notes),
    })
    rates = rt.model_copy(update={
        "rate_path": {c: [s.model_copy(update={"step_bp": _clamp(-s.step_bp, r.rates_step_bp, "rates.step_bp", notes)})
                          for s in steps] for c, steps in rt.rate_path.items()},
        "credit_spread_change_bp": _clamp(-rt.credit_spread_change_bp, r.rates_credit_spread_change_bp,
                                          "rates.credit_spread_change_bp", notes),
    })
    fxp = fx.model_copy(update={"move_pct": {c: _clamp(-v, r.fx_move_pct, f"fx.move_pct.{c.value}", notes)
                                             for c, v in fx.move_pct.items()}})
    mirrored = params.model_copy(update={"name": f"{params.name} (mirrored)", "freight": freight, "bunker": bunker,
                                         "rates": rates, "fx": fxp})
    return ScenarioParams.model_validate(mirrored.model_dump()), notes


def _dated(result: RunResult, hedge: HedgeTest, code: LineCode) -> list[DatedAmount]:
    by_date: dict = defaultdict(Decimal)
    for line in result.scen_lines:
        if line.line_code is code and line.owner_spec.value == hedge.module.value and hedge.target in line.sub_id:
            by_date[line.date] += line.amount_usd_scenario
    return [DatedAmount(date=d, usd_m=usd_m(v)) for d, v in sorted(by_date.items())]


def test_hedge(engine: Engine, hedge: HedgeTest, params: ScenarioParams, as_of=None) -> PairRuns:
    with_hedge = {"hedge_tests": [*params.hedge_tests, hedge]}
    given = run_pair(engine, params, with_hedge, as_of)
    mirrored_params, notes = mirror(params)
    flipped = run_pair(engine, mirrored_params, with_hedge, as_of)
    saving = given.run_b.liquidity.h_s - given.run_a.liquidity.h_s
    cost = flipped.run_a.liquidity.h_s - flipped.run_b.liquidity.h_s
    margin = given.run_b.liquidity.margin.get("scenario")
    if margin:
        test = MarginTest(passed=margin["shortfall_date"] is None, lowest_margin_eligible_balance_usd_m=usd_m(margin["lowest"]))
    else:  # no swap: no margin calls; the lowest margin-eligible balance is the lowest T4
        test = MarginTest(passed=True, lowest_margin_eligible_balance_usd_m=usd_m(min(given.run_b.liquidity.scen.t4.values())))
    metrics = HedgeMetrics(
        saving_adverse_usd_m=usd_m(saving), cost_favourable_usd_m=usd_m(cost),
        settlement_cash=_dated(given.run_b, hedge, LineCode.HEDGE_SETTLEMENT),
        margin_cash=_dated(given.run_b, hedge, LineCode.MARGIN_CALL), margin_test=test,
    )
    result = given.result.model_copy(update={"hedge_metrics": metrics})
    engine.store.append_audit("engine", "hedge_tested", given.result.pair_id,
                              {"hedge": hedge.model_dump(mode="json"), "mirror_pair": flipped.result.pair_id,
                               "mirror_notes": notes})
    return PairRuns(result, given.run_a, given.run_b, notes)


test_hedge.__test__ = False  # not a pytest test
