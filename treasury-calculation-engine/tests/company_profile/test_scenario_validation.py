"""Scenario policy ranges and override checks (2.7, 3.2, P2, P15, P21)."""

from datetime import date

import pytest

from hlag_engine.contracts import ScenarioParams
from hlag_engine.company_profile.scenario_validation import (
    ScenarioValidationError,
    load_scenario_ranges,
    scenario_problems,
    validate_scenario,
)
from tests.conftest import T0


@pytest.fixture(scope="module")
def ranges():
    return load_scenario_ranges()


def params(**overrides):
    data = {"name": "t", "start_date": T0, "source": "custom"}
    data.update(overrides)
    return ScenarioParams(**data)


def hedge(**overrides):
    data = {"module": "bunker", "instrument": "swap", "target": "fuel_all", "coverage_pct": 50,
            "tenor_months": 12, "start_date": T0}  # fmt: skip
    data.update(overrides)
    return data


def steps(*bps, currency="USD"):
    return {"rate_path": {currency: [{"date": date(2026, 12, i + 1), "step_bp": bp} for i, bp in enumerate(bps)]}}


def test_ranges_file_is_labelled(ranges):
    assert ranges.label.value == "ASSUMPTION"
    assert ranges.tag == "PROPOSED ILLUSTRATIVE SETTINGS"
    assert ranges.bunker_consumption_uplift_pct == (-50, 100)  # P15


EDGES = [
    # (field builder, value inside at the edge, value just outside)
    (lambda v: {"freight": {"rate_shock_pct": {"ASIA_EUROPE": v}}}, [-80, 300], [-80.01, 300.01]),
    (lambda v: {"freight": {"rate_shock_path": {"ASIA_EUROPE": [{"date": T0, "pct": v}]}}}, [-80, 300], [-81, 301]),
    (lambda v: {"freight": {"volume_shock_pct": {"ASIA_AMERICA": v}}}, [-50, 50], [-50.5, 50.5]),
    (lambda v: {"freight": {"transit_delay_days": {"ASIA_EUROPE": v}}}, [0, 60], [61]),
    (lambda v: {"bunker": {"vlsfo_shock_pct": v}}, [-70, 200], [-70.1, 200.1]),
    (lambda v: {"bunker": {"hsfo_shock_pct": v}}, [-70, 200], [-71, 201]),
    (lambda v: {"bunker": {"consumption_uplift_pct": v}}, [-50, -3, 100], [-50.1, 100.1]),
    (lambda v: {"rates": steps(v)}, [-200, 200], [-201, 201]),
    (lambda v: {"rates": {"credit_spread_change_bp": v}}, [-200, 1000], [-201, 1001]),
    (lambda v: {"fx": {"move_pct": {"EUR": v}}}, [-50, 100], [-50.1, 100.1]),
    (lambda v: {"duration_days": v}, [1, 730], [731]),
    (lambda v: {"hedge_tests": [hedge(tenor_months=v)]}, [1, 36], [37]),
]


@pytest.mark.parametrize(("build", "inside", "outside"), EDGES)
def test_range_edges(build, inside, outside, ranges, profile):
    for v in inside:
        assert scenario_problems(params(**build(v)), profile, ranges) == [], v
    for v in outside:
        problems = scenario_problems(params(**build(v)), profile, ranges)
        assert len(problems) == 1 and "outside" in problems[0], v


def test_hedge_coverage_range(ranges, profile):
    assert scenario_problems(params(hedge_tests=[hedge(coverage_pct=0)]), profile, ranges) == []
    assert scenario_problems(params(hedge_tests=[hedge(coverage_pct=100)]), profile, ranges) == []


def test_persistent_duration_allowed(ranges, profile):
    assert scenario_problems(params(duration_days=None), profile, ranges) == []


def test_cumulative_rate_steps(ranges, profile):
    assert scenario_problems(params(rates=steps(200, 200, 100)), profile, ranges) == []  # +500
    problems = scenario_problems(params(rates=steps(200, 200, 150)), profile, ranges)
    assert len(problems) == 1 and "cumulative" in problems[0]
    assert scenario_problems(params(rates=steps(-200, -200, -100, 150, -150)), profile, ranges) == []


def test_cumulative_sums_same_date_steps(ranges, profile):
    same_day = {"rate_path": {"EUR": [{"date": T0, "step_bp": 200}] * 3}}
    problems = scenario_problems(params(rates=same_day), profile, ranges)
    assert any("cumulative" in p for p in problems)


def test_cumulative_is_per_currency(ranges, profile):
    both = {"rate_path": {
        "USD": [{"date": date(2026, 12, 1), "step_bp": 200}, {"date": date(2027, 1, 1), "step_bp": 200}],
        "EUR": [{"date": date(2026, 12, 1), "step_bp": 200}, {"date": date(2027, 1, 1), "step_bp": 200}],
    }}  # fmt: skip
    assert scenario_problems(params(rates=both), profile, ranges) == []


def test_presets_from_spec_pass(ranges, profile):
    demand_slump = params(
        freight={"rate_shock_pct": {t: -20 for t in ("ASIA_EUROPE", "ASIA_AMERICA", "EUROPE_AMERICA", "AFRICA_INTRA")},
                 "volume_shock_pct": {"ASIA_EUROPE": -5}},
        bunker={"vlsfo_shock_pct": -15, "hsfo_shock_pct": -15, "consumption_uplift_pct": -3, "uplift_affected_share": 1},
    )  # fmt: skip
    assert scenario_problems(demand_slump, profile, ranges) == []
    credit_squeeze = params(rates={"credit_spread_change_bp": 200, "refinancing_mode": "unavailable"})
    assert scenario_problems(credit_squeeze, profile, ranges) == []
    fed = params(rates=steps(-25, -25, -25, -25))
    assert scenario_problems(fed, profile, ranges) == []


# --- overrides ---------------------------------------------------------------------------------


def test_override_within_allowed_range(ranges, profile):
    assert scenario_problems(params(overrides={"bunker.mfr_share": 0.5}), profile, ranges) == []


@pytest.mark.parametrize(
    ("key", "value", "fragment"),
    [
        ("bunker.mfr_share", 1.2, "allowed range"),
        ("freight.contract_share", 0.2, "allowed range"),
        ("fx.hedge_ratio.cad", 0.9, "allowed range"),
        ("liquidity.cash_and_equivalents_usd", 1e9, "only keys with an allowed_range"),
        ("liquidity.interest_in_ocf", True, "only keys with an allowed_range"),
        ("bunker.no_such_key", 1, "not a profile parameter"),
    ],
)
def test_override_rejected(ranges, profile, key, value, fragment):
    problems = scenario_problems(params(overrides={key: value}), profile, ranges)
    assert len(problems) == 1 and fragment in problems[0]


def test_validate_lists_every_problem(ranges, profile):
    bad = params(
        bunker={"vlsfo_shock_pct": 250},
        fx={"move_pct": {"BRL": -60}},
        duration_days=800,
        overrides={"bunker.mfr_share": 2},
    )
    with pytest.raises(ScenarioValidationError) as info:
        validate_scenario(bad, profile, ranges)
    assert len(info.value.problems) == 4
    validate_scenario(params(), profile, ranges)  # valid: no exception
