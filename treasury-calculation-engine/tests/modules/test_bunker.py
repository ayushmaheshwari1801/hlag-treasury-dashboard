"""Bunker module: worked example 7.14 and the acceptance tests of 7.15."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import yaml

from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.enums import LineCode, PassType, ProvenanceLabel, Trade
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.contracts.scenario import BunkerParams, FreightParams
from hlag_engine.modules.bunker import BunkerModule
from hlag_engine.modules.bunker.brent import brent_fill, estimate_beta
from hlag_engine.modules.bunker.calc import Blend
from hlag_engine.modules.bunker.checks import bt2_cost_level, bt3_bridge
from hlag_engine.modules.bunker.presets import DEMAND_SLUMP as BUNKER_SLUMP
from hlag_engine.modules.bunker.presets import build_bunker_reversal
from hlag_engine.modules.freight import FreightModule
from hlag_engine.modules.freight.presets import DEMAND_SLUMP as FREIGHT_SLUMP
from hlag_engine.modules.interface import ModuleInputError, ModuleInputs
from tests.modules.helpers import (
    FIXTURES,
    context,
    cumulative,
    deltas_by_date,
    gross_adverse,
    load_fixture,
    run_both,
    run_pass,
    usd_m,
)

BUNKER, FREIGHT = BunkerModule(), FreightModule()
DEC = [date(2026, 12, d) for d in range(1, 31)]


def run_joint(fx, params=None, **kwargs):
    """Freight first, then bunker reading freight's volume lines of the same pass."""
    params = params or fx.scenario
    fb, fs = run_both(FREIGHT, fx, params, **kwargs)
    b = run_pass(BUNKER, fx, params, PassType.BASE, inputs=ModuleInputs(tuple(fb.volume_lines)), **kwargs)
    s = run_pass(BUNKER, fx, params, PassType.SCENARIO, inputs=ModuleInputs(tuple(fs.volume_lines)), **kwargs)
    return b, s


def with_bunker(params, **fields):
    return params.model_copy(update={"bunker": params.bunker.model_copy(update=fields)})


@pytest.fixture(scope="module")
def fx():
    return load_fixture("bunker_example.yaml")


@pytest.fixture(scope="module")
def passes(fx):
    return run_joint(fx)


def by_date(output, code, prefix=""):
    out = {}
    for l in output.cash_lines:
        if l.line_code is code and l.sub_id.startswith(prefix):
            out[l.date] = out.get(l.date, Decimal(0)) + l.amount
    return out


# ------------------------------------------------------------------ worked example 7.14


def test_blended_prices(fx, passes):
    exp = fx.expected
    for name, output in zip(("base", "scenario"), passes):
        assert usd_m(output.diagnostics["monthly_price_usd_per_t"][date(2026, 11, 1)] * 1e6,
                     exp["price_paid_usd_per_t"][name])
        consumption = output.diagnostics["monthly_consumption_t"][date(2026, 11, 1)]
        assert consumption == pytest.approx(exp["consumption_t"][name], abs=1e-6)
        for hub, w, key in (("SIN", 0.6, "blended_sin_usd_per_t"), ("RTM", 0.4, "blended_rtm_usd_per_t")):
            paid = -by_date(output, LineCode.FUEL_PURCHASE, f"bunker:purchase:{hub}")[date(2026, 12, 1)]
            tonnes = Decimal(str(consumption * w / 30))
            assert round(paid / tonnes, 2) == round(Decimal(exp[key][name]), 2)


def test_november_purchases_and_daily_payments(fx, passes):
    exp = fx.expected
    for name, output in zip(("base", "scenario"), passes):
        assert usd_m(-output.diagnostics["purchases_usd"][date(2026, 11, 1)], exp["november_purchases"][name])
        daily = by_date(output, LineCode.FUEL_PURCHASE)
        assert sorted(daily) == DEC and DEC[0] == exp["daily_payment"]["first"] and DEC[-1] == exp["daily_payment"]["last"]
        assert all(usd_m(v, exp["daily_payment"][name]) for v in daily.values())


def test_fuel_purchase_delta_and_parts(fx, passes):
    base, scen = passes
    exp = fx.expected
    delta = sum(deltas_by_date(base, scen, {LineCode.FUEL_PURCHASE}).values())
    assert delta == Decimal("-59692416.00") and usd_m(delta, exp["fuel_purchase_delta"])
    nov = date(2026, 11, 1)
    p_b, p_s = (o.diagnostics["monthly_price_usd_per_t"][nov] for o in passes)
    c_b, c_s = (o.diagnostics["monthly_consumption_t"][nov] for o in passes)
    assert usd_m(-c_b * (p_s - p_b), exp["fuel_purchase_delta_parts"]["price"])
    assert usd_m(-(c_s - c_b) * p_s, exp["fuel_purchase_delta_parts"]["uplift"])


def test_fuel_mix_effect(fx, passes):
    mix = fx.expected["fuel_mix"]
    scen = passes[1]
    assert round(scen.metrics["fuel_mix_blended_change_pct"].value, 2) == float(mix["blended_change_pct"])
    assert round(scen.metrics["fuel_mix_vlsfo_only_change_pct"].value, 2) == float(mix["vlsfo_only_change_pct"])
    s0 = load_fixture("bunker_example.yaml", profile_values={"bunker.scrubber_share": 0.0})
    b0, s0_scen = run_joint(s0)
    nov = date(2026, 11, 1)
    assert round(b0.diagnostics["monthly_price_usd_per_t"][nov], 2) == float(mix["s0_base"])
    assert round(s0_scen.diagnostics["monthly_price_usd_per_t"][nov], 2) == float(mix["s0_scenario"])


def test_mfr_change_and_cash(fx, passes):
    base, scen = passes
    exp = fx.expected
    nov = date(2026, 11, 1)
    assert base.diagnostics["mfr_usd"][nov] == 0
    assert usd_m(scen.diagnostics["mfr_usd"][nov], exp["mfr_change_november"])
    mfr = by_date(scen, LineCode.MFR_RECOVERY)
    assert min(mfr) == exp["mfr_cash"]["first"] and max(mfr) == exp["mfr_cash"]["last"] and len(mfr) == 30
    assert all(usd_m(v, exp["mfr_cash"]["per_day"]) for v in mfr.values())
    assert not by_date(base, LineCode.MFR_RECOVERY)


def test_net_bunker_delta_exact(fx, passes):
    # The spec writes -53.519 = -59.692 + 6.173, a sum of parts each rounded to 3 decimals; the rules give
    # exactly -53,519,827.20 (P44; joint Red Sea knock-on in "Spec corrections").
    net = sum(deltas_by_date(*passes).values())
    assert net == Decimal(fx.expected["net_bunker_delta_usd"])


def test_gross_adverse_and_recovery_share(fx, passes):
    worst, when = gross_adverse(cumulative(deltas_by_date(*passes, codes=BUNKER.gross_codes)))
    assert usd_m(worst, fx.expected["gross_adverse"]["value"]) and when == fx.expected["gross_adverse"]["date"]
    mfr = passes[1].diagnostics["mfr_usd"][date(2026, 11, 1)]
    assert round(mfr / float(worst) * 100, 1) == float(fx.expected["mfr_share_of_gross_pct"])


def test_labels_and_notes(passes):
    base, scen = passes
    assert {l.label for l in scen.cash_lines} == {ProvenanceLabel.ASSUMPTION}
    assert any("USD 112m" in n for n in scen.notes) and "Unhedged: 100% (no fuel hedges disclosed)" in scen.notes


# ------------------------------------------------------------------ anchor differs from T0 (D35)


def test_anchor_differs_base_mfr_nonzero_delta_unchanged(fx, passes):
    anchor = {"VLSFO_SIN": 560.0, "VLSFO_RTM": 540.0, "HSFO_SIN": 420.0, "HSFO_RTM": 400.0}
    moved = load_fixture("bunker_example.yaml", anchor=anchor)
    base, scen = run_joint(moved)
    nov = date(2026, 11, 1)
    anchor_blend = 0.6 * (0.8 * 560 + 0.2 * 420) + 0.4 * (0.8 * 540 + 0.2 * 400)  # 524.0
    expected_base = 0.7 * (562.0 - anchor_blend) * 0.36 * 300_000
    assert base.diagnostics["mfr_usd"][nov] == pytest.approx(expected_base) and expected_base > 0
    delta = scen.diagnostics["mfr_usd"][nov] - base.diagnostics["mfr_usd"][nov]
    original = passes[1].diagnostics["mfr_usd"][nov] - passes[0].diagnostics["mfr_usd"][nov]
    assert delta == pytest.approx(original, abs=1e-6)
    assert sum(deltas_by_date(base, scen).values()) == sum(deltas_by_date(*passes).values())
    assert base.metrics["delivered_premium_usd_per_t"].value == pytest.approx(592 - anchor_blend)


# ------------------------------------------------------------------ acceptance 7.15


def test_bt2_and_bt3():
    d = yaml.safe_load((FIXTURES / "module_backtests.yaml").read_text())["bunker"]
    bt2 = bt2_cost_level(d["consumption_mt"], d["price"], d["expense_m"], d["co2_m"], d["tolerance_m"])
    assert round(bt2.modelled, 1) == 1420.8 and bt2.reported == 1428 and round(bt2.difference, 1) == -7.2
    assert bt2.passed
    bt3 = bt3_bridge(d["consumption_mt"], d["consumption_change"], d["price"], d["prior_price"], d["expense_m"],
                     d["co2_m"], d["prior_expense_m"], d["prior_co2_m"])
    assert round(bt3.prior_consumption_mt, 3) == 2.487
    assert round(bt3.price_effect, 1) == 120.0 and round(bt3.volume_effect, 1) == -47.2
    assert bt3.reported_change == 81 and round(bt3.residual, 1) == 8.2 and abs(bt3.residual) <= 15
    assert bt3.price_effect > 0 and bt3.volume_effect < 0


def test_zero_shock(fx):
    base, scen = run_joint(fx, fx.scenario.zero_shock())
    assert base.cash_lines == scen.cash_lines
    assert scen.diagnostics["mfr_usd"][date(2026, 11, 1)] == 0


def test_s_zero_price_change_equals_vlsfo_shock():
    s0 = load_fixture("bunker_example.yaml", profile_values={"bunker.scrubber_share": 0.0})
    base, scen = run_joint(s0)
    nov = date(2026, 11, 1)
    change = scen.diagnostics["monthly_price_usd_per_t"][nov] / base.diagnostics["monthly_price_usd_per_t"][nov] - 1
    assert change == pytest.approx(0.15, abs=1e-12)
    assert not [l for l in scen.cash_lines if "HSFO" in l.sub_id]


def test_usd_per_bbl_rejected_and_grade_check():
    bbl = load_fixture("bunker_example.yaml", market={"VLSFO_SIN": {"value": 80.0, "unit": "USD/bbl",
                                                                     "value_date": date(2026, 10, 29)}})
    with pytest.raises(ModuleInputError, match="USD/t"):
        run_joint(bbl)
    swapped = load_fixture("bunker_example.yaml", market={
        "VLSFO_SIN": {"value": 450.0, "unit": "USD/t", "value_date": date(2026, 10, 29)},
        "HSFO_SIN": {"value": 600.0, "unit": "USD/t", "value_date": date(2026, 10, 29)}})
    with pytest.raises(ModuleInputError, match="grade check"):
        run_joint(swapped)


def test_demand_slump_mfr(fx):
    nov = date(2026, 11, 1)
    no_price = fx.scenario.model_copy(update={
        "freight": FreightParams(volume_shock_pct={Trade.ASIA_EUROPE: -5.0}),
        "bunker": BunkerParams(consumption_uplift_pct=-3.0, uplift_affected_share=1.0)})
    base, scen = run_joint(fx, no_price)
    assert scen.diagnostics["mfr_usd"][nov] - base.diagnostics["mfr_usd"][nov] == 0
    priced = with_bunker(no_price, vlsfo_shock_pct=-15.0, hsfo_shock_pct=-15.0)
    flat_teu = priced.model_copy(update={"freight": FreightParams()})
    b1, s1 = run_joint(fx, priced)
    b2, s2 = run_joint(fx, flat_teu)
    with_cut = s1.diagnostics["mfr_usd"][nov] - b1.diagnostics["mfr_usd"][nov]
    without = s2.diagnostics["mfr_usd"][nov] - b2.diagnostics["mfr_usd"][nov]
    assert with_cut < 0 and without < with_cut  # lower TEU, smaller recovery change


def hedge(instrument, coverage=100.0, target="fuel_all"):
    return HedgeTest(module="bunker", instrument=instrument, target=target, coverage_pct=coverage, tenor_months=1,
                     start_date=date(2026, 11, 1))


def test_swap_rise_settlement_equals_purchase_increase(fx):
    params = with_bunker(fx.scenario, consumption_uplift_pct=0.0, uplift_affected_share=0.0)
    params = params.model_copy(update={"hedge_tests": [hedge("swap")]})
    base, scen = run_joint(fx, params)
    settle_s = by_date(scen, LineCode.HEDGE_SETTLEMENT)
    assert list(settle_s) == [date(2026, 11, 30)]
    increase = -sum(deltas_by_date(base, scen, {LineCode.FUEL_PURCHASE}).values())
    assert settle_s[date(2026, 11, 30)] == pytest.approx(increase, abs=Decimal("0.05"))
    assert settle_s[date(2026, 11, 30)] > 0
    assert sum(by_date(base, LineCode.HEDGE_SETTLEMENT).values()) == 0  # K = T0 price, flat base
    assert not by_date(scen, LineCode.MARGIN_CALL)  # in the money: no margin posted
    assert "Unhedged: 100% (no fuel hedges disclosed)" not in scen.notes


def test_swap_fall_settlement_and_margin(fx):
    params = fx.scenario.model_copy(update={"bunker": BunkerParams(vlsfo_shock_pct=-20.0, hsfo_shock_pct=-20.0),
                                            "hedge_tests": [hedge("swap")]})
    base, scen = run_joint(fx, params)
    settle = by_date(scen, LineCode.HEDGE_SETTLEMENT)[date(2026, 11, 30)]
    assert settle == pytest.approx(Decimal(-0.2 * 562 * 400_000), abs=Decimal("0.05"))
    margin = by_date(scen, LineCode.MARGIN_CALL)
    posted = Decimal(str(0.2 * 562 * 400_000))
    assert margin[date(2026, 11, 1)] == pytest.approx(-posted, abs=Decimal("0.05"))  # margin called
    assert margin[date(2026, 11, 30)] == pytest.approx(posted, abs=Decimal("0.05"))  # returned at settlement
    assert scen.metrics["hedge_peak_posted_margin_usd"].value == pytest.approx(float(posted))
    assert not by_date(base, LineCode.MARGIN_CALL)


def test_fixed_price_supply_same_settlements_no_margin(fx):
    params = fx.scenario.model_copy(update={"bunker": BunkerParams(vlsfo_shock_pct=-20.0, hsfo_shock_pct=-20.0)})
    _, swap = run_joint(fx, params.model_copy(update={"hedge_tests": [hedge("swap")]}))
    _, fps = run_joint(fx, params.model_copy(update={"hedge_tests": [hedge("fixed_price_supply")]}))
    s_fps = by_date(fps, LineCode.HEDGE_SETTLEMENT)
    assert sorted(s_fps) == DEC  # on the purchase payment dates
    assert sum(s_fps.values()) == pytest.approx(sum(by_date(swap, LineCode.HEDGE_SETTLEMENT).values()),
                                                abs=Decimal("0.10"))
    assert not by_date(fps, LineCode.MARGIN_CALL)


def test_hedge_test_validation(fx):
    for bad in (hedge("forward"), hedge("swap", target="jet_fuel"),
                hedge("swap").model_copy(update={"strike_rule": "forward_curve"})):
        with pytest.raises(ModuleInputError):
            run_joint(fx, fx.scenario.model_copy(update={"hedge_tests": [bad]}))


def test_beta_fallback_and_estimate():
    brent = [(date(2026, 1, 1) + timedelta(days=i), 70.0 + (i % 17)) for i in range(300)]
    short = [(date(2026, 9, 1) + timedelta(weeks=w), 500.0 + w) for w in range(10)]
    est = estimate_beta(short, brent, 1.0, date(2026, 10, 31))
    assert est.value == 1.0 and est.label is ProvenanceLabel.ASSUMPTION
    # fuel moves exactly 1.5x Brent each week
    weeks = [date(2026, 4, 2) + timedelta(weeks=w) for w in range(27)]
    b = [(d, 70.0 * (1 + 0.01 * ((i * 7) % 5 - 2))) for i, d in enumerate(weeks)]
    fuel, level = [(weeks[0], 500.0)], 500.0
    for (d0, b0), (d1, b1) in zip(b, b[1:]):
        level *= 1 + 1.5 * (b1 / b0 - 1)
        fuel.append((d1, level))
    est = estimate_beta(fuel, b, 1.0, date(2026, 10, 31))
    assert est.value == pytest.approx(1.5) and est.label is ProvenanceLabel.MARKET and est.observations == 26
    assert brent_fill(600.0, 80.0, 88.0, 1.0) == pytest.approx(660.0)


def test_stale_print_uses_brent_fill_and_sets_proxy_flag():
    stale = load_fixture("bunker_example.yaml", market={
        "VLSFO_SIN": {"value": 600.0, "unit": "USD/t", "value_date": date(2026, 10, 8), "days_since_print": 23,
                      "stale": True}})
    stale.history = {"BRENT": [(date(2026, 10, 8), 80.0), (date(2026, 10, 31), 88.0)]}
    base, scen = run_joint(stale)
    m = base.metrics["hub_price_usd_per_t.VLSFO_SIN"]
    assert m.value == pytest.approx(660.0) and m.label is ProvenanceLabel.PROXY
    assert base.diagnostics["proxy_based"] and base.diagnostics["proxy_series"] == ["VLSFO_SIN"]
    assert base.metrics["brent_beta.VLSFO"].label is ProvenanceLabel.ASSUMPTION
    fresh_base, _ = run_joint(load_fixture("bunker_example.yaml"))
    assert not fresh_base.diagnostics["proxy_based"]


def test_future_scrubber_adjustment_changes_blend_only_from_its_date(fx):
    adj = Adjustment(adjustment_id="adj_scrubber", param_key="bunker.scrubber_share", old_value=0.2, new_value=0.1,
                     step="large", effective_date=date(2026, 11, 16), review_date=date(2027, 5, 1),
                     event_ids=["evt_reg"], justification="HSFO restricted with scrubbers", confidence=0.8,
                     status="approved", approver="treasurer", decided_at=datetime(2026, 11, 2, tzinfo=UTC),
                     proposed_by="adjustment_agent")
    ctx = context(fx, fx.scenario, PassType.BASE, adjustments=[adj])
    from hlag_engine.modules.bunker.calc import build_prices
    from hlag_engine.modules.paths import ShapeSpec

    prices = build_prices(ctx, fx.scenario.zero_shock(), ShapeSpec.for_driver(fx.scenario, None))
    blend = Blend(ctx)
    assert blend.price(prices, date(2026, 11, 15)) == pytest.approx(562.0)
    assert blend.price(prices, date(2026, 11, 16)) == pytest.approx(0.6 * (0.9 * 600 + 0.1 * 450)
                                                                       + 0.4 * (0.9 * 580 + 0.1 * 430))
    base = run_pass(BUNKER, fx, fx.scenario, PassType.BASE, adjustments=[adj])
    hsfo = by_date(base, LineCode.FUEL_PURCHASE, "bunker:purchase:SIN:HSFO")
    assert hsfo[date(2026, 12, 16)] == pytest.approx(hsfo[date(2026, 12, 15)] / 2, abs=Decimal("0.01"))


def test_demand_slump_preset(fx):
    nov = date(2026, 11, 1)
    slump = fx.scenario.model_copy(update={"freight": FREIGHT_SLUMP, "bunker": BUNKER_SLUMP, "default_shape": "ramp",
                                           "ramp_or_revert_weeks": 8, "duration_days": None})
    base, scen = run_joint(fx, slump)
    assert scen.diagnostics["purchases_usd"][nov] > base.diagnostics["purchases_usd"][nov]  # less negative
    assert scen.diagnostics["monthly_price_usd_per_t"][nov] < base.diagnostics["monthly_price_usd_per_t"][nov]
    assert scen.diagnostics["monthly_consumption_t"][nov] < base.diagnostics["monthly_consumption_t"][nov]
    mfr_change = scen.diagnostics["mfr_usd"][nov] - base.diagnostics["mfr_usd"][nov]
    no_teu_fall = slump.model_copy(update={"freight": FREIGHT_SLUMP.model_copy(update={"volume_shock_pct": {}})})
    b2, s2 = run_joint(fx, no_teu_fall)
    assert mfr_change < 0 and abs(mfr_change) < abs(s2.diagnostics["mfr_usd"][nov] - b2.diagnostics["mfr_usd"][nov])


def test_bunker_reversal_preset_stores_numbers():
    weeks = [date(2026, 3, 5) + timedelta(weeks=w) for w in range(36)]
    history = {"VLSFO_SIN": [(d, 500.0 + 5 * i) for i, d in enumerate(weeks)],
               "VLSFO_RTM": [(d, 480.0 + 5 * i) for i, d in enumerate(weeks)],
               "HSFO_SIN": [(d, 400.0 - i) for i, d in enumerate(weeks)],
               "HSFO_RTM": [(d, 380.0 - i) for i, d in enumerate(weeks)]}
    params = build_bunker_reversal(history, date(2026, 10, 31), date(2026, 11, 1), 0.6)
    now, then = 0.6 * (500 + 170) + 0.4 * (480 + 170), 0.6 * (500 + 40) + 0.4 * (480 + 40)
    assert params.bunker.vlsfo_shock_pct == pytest.approx(-0.5 * (now - then) / now * 100)
    assert params.bunker.hsfo_shock_pct == 0.0  # HSFO fell: nothing to give back


def test_volume_lines_must_match_pass(fx):
    fb, _ = run_both(FREIGHT, fx)
    with pytest.raises(ModuleInputError, match="pass"):
        run_pass(BUNKER, fx, fx.scenario, PassType.SCENARIO, inputs=ModuleInputs(tuple(fb.volume_lines)))
