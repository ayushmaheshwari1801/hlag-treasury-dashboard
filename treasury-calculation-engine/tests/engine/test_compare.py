"""run_pair (4.1, D28), test_hedge (5.3) and the mirror."""

from datetime import date
from decimal import Decimal

import pytest

from hlag_engine.company_profile.schema import profile_from_dict
from hlag_engine.contracts.enums import LineCode
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.engine.compare import mirror, run_pair, test_hedge as hedge_tool
from hlag_engine.engine.run import Engine
from tests.engine.conftest import NOW, T0, loaded_store
from tests.engine.test_run import RED_SEA, anchored_raw


@pytest.fixture(scope="module")
def engine():
    return Engine(loaded_store(), profile=profile_from_dict(anchored_raw()), clock=lambda: NOW)


def test_zim_pair(engine):
    params = ScenarioParams(**RED_SEA)
    pair = run_pair(engine, params, {"zim_tranche_on": True}, T0)
    r = pair.result
    assert r.label == "state comparison" and r.run_a.run_id != r.run_b.run_id
    rec_a, rec_b = (engine.store.get_run_record(x) for x in (r.run_a.run_id, r.run_b.run_id))
    assert rec_a.pair_id == rec_b.pair_id == r.pair_id
    assert rec_a.params.rates.zim_tranche_on is False and rec_b.params.rates.zim_tranche_on is True
    totals = r.difference.line_totals_usd_m
    assert totals[LineCode.ACQUISITION_PAYMENT] == -4200.0 and totals[LineCode.DEBT_DRAWDOWN] >= 2500.0
    assert r.difference.min_headroom_usd_m < 0
    # the state change never appears as a scenario delta inside run B
    for p in pair.run_b.pairs:
        if p.ref.line_code is LineCode.ACQUISITION_PAYMENT:
            assert p.delta_usd == 0
    assert engine.replay_differences(r.run_b.run_id) == []  # replay keeps the explicit switch (no refill)


def test_inr_brl_switch_pair(engine):
    pair = run_pair(engine, ScenarioParams(**RED_SEA), {"inr_brl_obligations_on": False}, T0)
    tx_a = [l for l in pair.run_a.scen_lines if l.line_code is LineCode.FX_TRANSACTION]
    tx_b = [l for l in pair.run_b.scen_lines if l.line_code is LineCode.FX_TRANSACTION]
    assert tx_a and not tx_b
    assert pair.result.difference.line_totals_usd_m[LineCode.FX_TRANSACTION] > 0  # fewer outflows in B


def test_unknown_state_change_is_rejected(engine):
    with pytest.raises(ValueError, match="not a state change"):
        run_pair(engine, ScenarioParams(**RED_SEA), {"freight": {}}, T0)


def test_hedge_tool_swap_saving_and_cost(engine):
    spike = ScenarioParams(name="Oil spike", start_date=date(2026, 11, 1), source="custom", default_shape="spike_revert",
                           ramp_or_revert_weeks=26, bunker={"vlsfo_shock_pct": 30, "hsfo_shock_pct": 20})
    swap = HedgeTest(module="bunker", instrument="swap", target="fuel_all", coverage_pct=50, tenor_months=6,
                     start_date=date(2026, 11, 1))
    out = hedge_tool(engine, swap, spike, T0)
    m = out.result.hedge_metrics
    assert m.saving_adverse_usd_m > 0 and m.cost_favourable_usd_m > 0
    assert m.settlement_cash and all(a.usd_m > 0 for a in m.settlement_cash)  # prices up: the swap pays
    assert isinstance(m.margin_test.passed, bool)
    assert out.result.run_b.run_id == out.run_b.run_id and swap in out.run_b.record.params.hedge_tests


def test_mirror_negates_and_clamps():
    params = ScenarioParams(name="x", start_date=date(2026, 11, 1), source="custom",
                            freight={"rate_shock_pct": {"ASIA_EUROPE": 40}, "transit_delay_days": {"ASIA_EUROPE": 12}},
                            fx={"move_pct": {"EUR": 100}}, rates={"credit_spread_change_bp": 150,
                                                                   "rate_path": {"USD": [{"date": "2026-12-09", "step_bp": -25}]}})
    m, notes = mirror(params)
    assert m.freight.rate_shock_pct == {next(iter(m.freight.rate_shock_pct)): -40.0} and m.freight.transit_delay_days == {}
    assert m.rates.credit_spread_change_bp == -150 and m.rates.rate_path[next(iter(m.rates.rate_path))][0].step_bp == 25
    assert list(m.fx.move_pct.values()) == [-50.0]  # -100 clamped to the policy range
    assert any("clamped" in n for n in notes) and any("transit" in n for n in notes)
    assert m.name.endswith("(mirrored)")
