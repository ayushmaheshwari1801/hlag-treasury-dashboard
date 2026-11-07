"""Scenario validation against policy ranges and the profile (spec 2.7, 3.2, 4.1).

The ScenarioParams contract (step 1) enforces physical limits and the scope rules. This
module adds the configured policy ranges (thresholds.yaml, PROPOSED ILLUSTRATIVE) and checks
overrides against the profile: only keys with an allowed_range may be overridden (P21).
Every problem is collected; nothing runs if any is found.
"""

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import ProvenanceLabel
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.company_profile.schema import PROFILE_DIR, Profile

DEFAULT_THRESHOLDS_PATH = PROFILE_DIR / "thresholds.yaml"

Range = tuple[float, float]


class ScenarioRanges(ContractModel):
    label: ProvenanceLabel
    tag: str = Field(min_length=1)
    freight_rate_shock_pct: Range
    freight_volume_shock_pct: Range
    freight_transit_delay_days: Range
    bunker_fuel_shock_pct: Range
    bunker_consumption_uplift_pct: Range
    rates_step_bp: Range
    rates_cumulative_bp: Range
    rates_credit_spread_change_bp: Range
    fx_move_pct: Range
    duration_days: Range
    hedge_coverage_pct: Range
    hedge_tenor_months: Range
    # Direct cash flows (deviation from spec 2.7); defaults match thresholds.yaml.
    direct_cash_flow_usd_m: Range = (-2000.0, 2000.0)
    direct_cash_total_abs_usd_m: Range = (0.0, 5000.0)


class ScenarioValidationError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("scenario failed validation:\n- " + "\n- ".join(problems))
        self.problems = problems


def load_scenario_ranges(path: Path | str = DEFAULT_THRESHOLDS_PATH) -> ScenarioRanges:
    with open(path, encoding="utf-8") as handle:
        return ScenarioRanges.model_validate(yaml.safe_load(handle)["scenario_ranges"])


def scenario_problems(params: ScenarioParams, profile: Profile, ranges: ScenarioRanges) -> list[str]:
    """Every policy-range and override problem in a scenario (empty when valid)."""
    problems: list[str] = []

    def check(name: str, value: float, bounds: Range) -> None:
        low, high = bounds
        if not low <= value <= high:
            problems.append(f"{name} = {value} is outside [{low:g}, {high:g}]")

    f = params.freight
    for trade, v in f.rate_shock_pct.items():
        check(f"freight.rate_shock_pct.{trade.value}", v, ranges.freight_rate_shock_pct)
    for trade, points in f.rate_shock_path.items():
        for point in points:
            check(f"freight.rate_shock_path.{trade.value}[{point.date}]", point.pct, ranges.freight_rate_shock_pct)
    for trade, v in f.volume_shock_pct.items():
        check(f"freight.volume_shock_pct.{trade.value}", v, ranges.freight_volume_shock_pct)
    for trade, v in f.transit_delay_days.items():
        check(f"freight.transit_delay_days.{trade.value}", v, ranges.freight_transit_delay_days)

    b = params.bunker
    check("bunker.vlsfo_shock_pct", b.vlsfo_shock_pct, ranges.bunker_fuel_shock_pct)
    check("bunker.hsfo_shock_pct", b.hsfo_shock_pct, ranges.bunker_fuel_shock_pct)
    check("bunker.consumption_uplift_pct", b.consumption_uplift_pct, ranges.bunker_consumption_uplift_pct)

    r = params.rates
    for currency, steps in r.rate_path.items():
        by_date: dict[date, float] = defaultdict(float)
        for step in steps:
            check(f"rates.rate_path.{currency.value}[{step.date}]", step.step_bp, ranges.rates_step_bp)
            by_date[step.date] += step.step_bp
        running = 0.0
        for when in sorted(by_date):
            running += by_date[when]
            check(f"rates.rate_path.{currency.value} cumulative at {when}", running, ranges.rates_cumulative_bp)
    check("rates.credit_spread_change_bp", r.credit_spread_change_bp, ranges.rates_credit_spread_change_bp)

    for currency, v in params.fx.move_pct.items():
        check(f"fx.move_pct.{currency.value}", v, ranges.fx_move_pct)

    if params.duration_days is not None:
        check("duration_days", params.duration_days, ranges.duration_days)
    for i, hedge in enumerate(params.hedge_tests):
        check(f"hedge_tests[{i}].coverage_pct", hedge.coverage_pct, ranges.hedge_coverage_pct)
        check(f"hedge_tests[{i}].tenor_months", hedge.tenor_months, ranges.hedge_tenor_months)

    flows = params.direct.cash_flows
    for flow in flows:
        check(f"direct.cash_flows[{flow.effect_id} {flow.date}].amount_usd_m", flow.amount_usd_m,
              ranges.direct_cash_flow_usd_m)
        if flow.date < params.start_date:
            problems.append(f"direct.cash_flows[{flow.effect_id}] is dated {flow.date}, before the scenario start "
                            f"{params.start_date}")
    if flows:
        check("direct.cash_flows total absolute amount_usd_m", sum(abs(f.amount_usd_m) for f in flows),
              ranges.direct_cash_total_abs_usd_m)

    for key, value in params.overrides.items():
        problems.extend(_override_problems(key, value, profile))
    return problems


def validate_scenario(params: ScenarioParams, profile: Profile, ranges: ScenarioRanges) -> None:
    """Raise ScenarioValidationError listing every problem; return None when valid."""
    problems = scenario_problems(params, profile, ranges)
    if problems:
        raise ScenarioValidationError(problems)


def _override_problems(key: str, value: Any, profile: Profile) -> list[str]:
    entry = profile.parameters.get(key)
    if entry is None:
        return [f"override {key}: not a profile parameter"]
    if not entry.overridable:
        return [f"override {key}: only keys with an allowed_range, or state switches, may be overridden (P21, P66)"]
    if not entry.in_bounds(value):
        if entry.allowed_range is None:
            return [f"override {key} = {value!r} is not one of {entry.allowed_values}"]
        low, high = entry.allowed_range
        return [f"override {key} = {value!r} is outside its allowed range [{low:g}, {high:g}]"]
    catalogue = profile.catalogue.get(key)
    if catalogue is not None and catalogue.hard_limits is not None:
        low, high = catalogue.hard_limits
        if not low <= value <= high:
            return [f"override {key} = {value!r} is outside its hard limits [{low:g}, {high:g}]"]
    return []
