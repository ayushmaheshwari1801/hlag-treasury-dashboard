"""Direct cash flows (deviation from spec 2.1/2.2/2.7/4.3; decisions_pending.md, "Direct cash flows")."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hlag_engine.company_profile.scenario_validation import ScenarioValidationError
from hlag_engine.contracts.enums import LineCode, OwnerSpec
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.engine.run import Engine
from tests.engine.conftest import NOW, T0, loaded_store
from tests.engine.test_run import profile_c  # noqa: F401  (module-scoped fixture)

FINE = {"effect_id": "fine-eu", "overlap_key": "eu-competition-fine", "label": "EU fine", "rationale": "Stated fine",
        "event_ids": ["E1"]}


def scenario(*flows, **extra):
    return ScenarioParams(name="Direct cash", start_date=T0, source="custom", direct={"cash_flows": list(flows)}, **extra)


@pytest.fixture
def engine(profile_c):  # noqa: F811
    return Engine(loaded_store(), profile=profile_c, clock=lambda: NOW)


def test_direct_block_is_a_shock_cleared_by_zero_shock():
    params = scenario({**FINE, "date": "2026-12-15", "amount_usd_m": -150})
    assert params.zero_shock().direct.cash_flows == []
    assert OwnerSpec.DIRECT in params.drivers_set()
    assert ScenarioParams.field_categories()["direct.cash_flows"] is not None


def test_contract_rejects_double_counting_and_zero_amounts():
    with pytest.raises(ValidationError, match="two entries"):
        scenario({**FINE, "date": "2026-12-15", "amount_usd_m": -1}, {**FINE, "date": "2026-12-15", "amount_usd_m": -2})
    with pytest.raises(ValidationError, match="already counted"):
        scenario({**FINE, "date": "2026-12-15", "amount_usd_m": -1},
                 {**FINE, "effect_id": "other", "date": "2026-12-16", "amount_usd_m": -2})
    with pytest.raises(ValidationError, match="more than one overlap_key"):
        scenario({**FINE, "date": "2026-12-15", "amount_usd_m": -1},
                 {**FINE, "overlap_key": "other", "date": "2026-12-16", "amount_usd_m": -2})
    with pytest.raises(ValidationError, match="nonzero"):
        scenario({**FINE, "date": "2026-12-15", "amount_usd_m": 0})
    # one effect may be paid in several dated instalments
    assert len(scenario({**FINE, "date": "2026-12-15", "amount_usd_m": -1},
                        {**FINE, "date": "2027-01-15", "amount_usd_m": -1}).direct.cash_flows) == 2


def test_direct_cash_moves_scenario_path_only_and_is_its_own_attribution_bar(engine):
    plain = engine.run_scenario(ScenarioParams(name="Plain", start_date=T0, source="custom"), as_of=T0, write=False)
    res = engine.run_scenario(scenario({**FINE, "date": "2026-12-15", "amount_usd_m": -150}), as_of=T0, write=False)
    lines = [l for l in res.scen_lines if l.owner_spec is OwnerSpec.DIRECT]
    assert [(l.line_code, l.amount_usd_scenario) for l in lines] == [(LineCode.EVENT_CASH_FLOW, Decimal("-150000000.00"))]
    assert not [l for l in res.base_lines if l.owner_spec is OwnerSpec.DIRECT]
    assert res.attribution.direct == Decimal("-150000000.00")
    assert res.attribution.total == plain.attribution.total + Decimal("-150000000")
    assert res.liquidity.h_b == plain.liquidity.h_b                      # baseline unchanged
    assert res.liquidity.h_s == plain.liquidity.h_s - Decimal("150000000")  # the low point falls by the full amount
    assert res.summary.attribution_usd_m.direct == pytest.approx(-150.0)
    assert Decimal(str(res.record.results["direct_cash"]["total_usd"])) == Decimal("-150000000")


def test_direct_cash_after_horizon_goes_to_watch_list_and_before_t0_is_rejected(engine):
    res = engine.run_scenario(scenario({**FINE, "date": "2028-06-30", "amount_usd_m": -150}), as_of=T0, write=False)
    assert res.attribution.direct == 0
    assert [w["effect_id"] for w in res.record.results["direct_cash"]["watch_list"]] == ["fine-eu"]
    with pytest.raises(ScenarioValidationError, match="before the scenario start"):
        engine.run_scenario(scenario({**FINE, "date": "2026-10-01", "amount_usd_m": -150}), as_of=T0, write=False)
    with pytest.raises(ValueError, match="must be after T0"):  # the path starts the day after T0
        engine.run_scenario(scenario({**FINE, "date": T0.isoformat(), "amount_usd_m": -150}), as_of=T0, write=False)


def test_direct_cash_policy_limits(engine):
    with pytest.raises(ScenarioValidationError, match="amount_usd_m"):
        engine.run_scenario(scenario({**FINE, "date": "2026-12-15", "amount_usd_m": -2500}), as_of=T0, write=False)
    flows = [{**FINE, "date": date(2026, 11 + i // 30, 1 + i % 28).isoformat(), "amount_usd_m": -1800} for i in range(3)]
    with pytest.raises(ScenarioValidationError, match="total absolute"):
        engine.run_scenario(scenario(*flows), as_of=T0, write=False)


def test_direct_cash_run_replays_exactly(engine):
    res = engine.run_scenario(scenario({**FINE, "date": "2026-12-15", "amount_usd_m": -150}), as_of=T0)
    assert engine.replay_differences(res.run_id) == []
