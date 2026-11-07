"""Rates module: worked example 8.14 to four decimals and the acceptance tests of 8.15."""

from datetime import date
from decimal import Decimal

import pytest
import yaml

from hlag_engine import PACKAGE_DIR
from hlag_engine.company_profile.resolve import RunContext
from hlag_engine.company_profile.schema import profile_from_dict
from hlag_engine.contracts.enums import Currency, LineCode, PassType, PresetId, ProvenanceLabel
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.contracts.scenario import RatesParams, RateStep, ScenarioParams
from hlag_engine.generator.generate import generate
from hlag_engine.modules.interface import ModuleInputError, ModuleInputs, ModuleRunContext, ProfileContext, SnapshotEntry
from hlag_engine.modules.rates import RatesModule
from hlag_engine.modules.rates.checks import calibration_check, scope_problems, static_sensitivity
from hlag_engine.modules.rates.presets import build
from tests.conftest import T0, complete_raw
from tests.modules.helpers import FIXTURES, cumulative, deltas_by_date, gross_adverse, load_fixture, run_both, usd_m

RATES = RatesModule()
H_END = date(2027, 10, 31)


@pytest.fixture(scope="module")
def fx():
    return load_fixture("rates_example.yaml")


@pytest.fixture(scope="module")
def passes(fx):
    return run_both(RATES, fx)


def line_amount(output, sub_id, when, code=None):
    return sum((l.amount for l in output.cash_lines
                if l.sub_id == sub_id and l.date == when and (code is None or l.line_code is code)), Decimal(0))


# ------------------------------------------------------------------ worked example 8.14


def test_every_row(fx, passes):
    base, scen = passes
    for sub_id, pay, start, days, d_rate, delta in fx.expected["rows"]:
        got = line_amount(scen, sub_id, pay) - line_amount(base, sub_id, pay)
        assert usd_m(got, delta), (sub_id, pay, got)
        assert (pay - start).days == days
        notional = 300_000_000 if "float" in sub_id else 500_000_000
        sign = -1 if "float" in sub_id else 1  # a lower rate saves on debt, loses on deposits
        assert usd_m(sign * notional * float(d_rate) / 100 * days / 360, delta)


def test_only_listed_rows_change(fx, passes):
    changed = {(l.sub_id, l.date) for l in passes[1].cash_lines} ^ {(l.sub_id, l.date) for l in passes[0].cash_lines}
    assert not changed  # same keys in both passes
    deltas = {(sub, d) for sub, d, *_ in fx.expected["rows"]}
    for b in passes[0].cash_lines:
        s = line_amount(passes[1], b.sub_id, b.date)
        assert (s != b.amount) == ((b.sub_id, b.date) in deltas), (b.sub_id, b.date)


def test_totals(fx, passes):
    debt = sum(deltas_by_date(*passes, codes=RATES.debt_side_codes).values())
    cash = sum(deltas_by_date(*passes, codes=RATES.cash_side_codes).values())
    assert usd_m(debt, fx.expected["debt_side"]) and usd_m(cash, fx.expected["cash_side"])
    assert usd_m(debt + cash, fx.expected["net"])
    assert usd_m(Decimal(1439.25) / 360 * 1_000_000, fx.expected["debt_side"])
    assert usd_m(Decimal(-2286.25) / 360 * 1_000_000, fx.expected["cash_side"])
    assert usd_m(Decimal(-847) / 360 * 1_000_000, fx.expected["net"])


def test_first_cash_effect_and_unchanged_first_months(fx, passes):
    deltas = deltas_by_date(*passes)
    nonzero = sorted(d for d, v in deltas.items() if v)
    assert nonzero[0] == fx.expected["first_cash_effect"]
    assert all(v == 0 for d, v in deltas.items() if d <= fx.expected["unchanged_through"])


def test_gross_adverse_cash_side(fx, passes):
    cash_worst, cash_when = gross_adverse(cumulative(deltas_by_date(*passes, codes=RATES.cash_side_codes)))
    debt_worst, _ = gross_adverse(cumulative(deltas_by_date(*passes, codes=RATES.debt_side_codes)))
    assert usd_m(cash_worst, fx.expected["gross_adverse"]["value"]) and cash_when == fx.expected["gross_adverse"]["date"]
    assert debt_worst == 0  # falling rates only help the debt side


def test_last_periods_pay_after_horizon(passes):
    _, scen = passes
    late = {(w.sub_id, w.date) for w in scen.watch_list}
    assert ("rates:float:USD:A", date(2027, 11, 15)) in late and ("rates:deposit:USD:3", date(2027, 11, 1)) in late
    assert all(l.date <= H_END for l in scen.cash_lines)


# ------------------------------------------------------------------ the full test profile


MARKET = {
    "SOFR": SnapshotEntry(4.0, date(2026, 10, 30), "%"),
    "ESTR": SnapshotEntry(2.0, date(2026, 10, 30), "%"),
    "BB_OAS": SnapshotEntry(2.0, date(2026, 10, 30), "%"),
    "UST_2Y": SnapshotEntry(3.8, date(2026, 10, 30), "%"),
    "EA_2Y": SnapshotEntry(2.2, date(2026, 10, 30), "%"),
    "FX_USD_PER_EUR": SnapshotEntry(1.15, date(2026, 10, 30), "USD per unit"),
}


V2 = PACKAGE_DIR / "company_profile" / "company_profile_2026H1-v2.yaml"
V2_KEYS = ("rates.bond_coupon_month_day", "rates.bond_maturity_date", "rates.zim_amount_usd",
           "rates.zim_consideration_usd", "rates.zim_draw_date")


def complete_with_v2_bond() -> dict:
    """The complete test profile with the bond and ZIM terms of profile version 2026H1-v2."""
    raw = complete_raw()
    v2 = yaml.safe_load(V2.read_text())["parameters"]
    for key in V2_KEYS:
        raw["parameters"][key] = v2[key]
    return raw


@pytest.fixture(scope="module")
def full():
    profile = profile_from_dict(complete_with_v2_bond())
    register = generate(profile, T0)
    return profile, register.items


def run_full(full, params, pass_type, market=None):
    profile, exposures = full
    ctx = ModuleRunContext(ProfileContext(RunContext.for_scenario(profile, T0, params)), pass_type, market or MARKET)
    return RATES.run(ctx, params.zero_shock() if pass_type is PassType.BASE else params, exposures, ModuleInputs())


def both_full(full, params, market=None):
    return run_full(full, params, PassType.BASE, market), run_full(full, params, PassType.SCENARIO, market)


def scenario(**rates):
    return ScenarioParams(name="test", start_date=date(2026, 11, 1), source="custom", rates=RatesParams(**rates))


def step(bp, when=T0):
    return {Currency.USD: [RateStep(date=when, step_bp=bp)], Currency.EUR: [RateStep(date=when, step_bp=bp)]}


def by_sub(output, prefix):
    return [(l.sub_id, l.date, l.currency, l.amount) for l in output.cash_lines if l.sub_id.startswith(prefix)]


def test_full_profile_runs_without_missing(full):
    base, scen = both_full(full, scenario())
    assert base.diagnostics["missing"] == [] and base.cash_lines == scen.cash_lines
    assert not scope_problems(full[1], base.cash_lines, H_END)
    kinds = {l.sub_id.split(":")[1] for l in base.cash_lines}
    assert kinds >= {"float", "deposit", "fixed", "lease", "maturity", "drawdown", "refi"}


def test_shock_after_last_reset_changes_nothing(fx):
    late = scenario(rate_path={Currency.USD: [RateStep(date=date(2027, 10, 20), step_bp=-100)]})
    base, scen = run_both(RATES, fx, late)
    assert base.cash_lines == scen.cash_lines


def test_fixed_debt_and_leases_show_no_delta(full):
    base, scen = both_full(full, scenario(rate_path=step(100)))
    for prefix in ("rates:fixed", "rates:lease"):
        assert by_sub(base, prefix) == by_sub(scen, prefix) and by_sub(base, prefix)
    lease = [l for l in base.cash_lines if "LEASE" in l.sub_id]
    assert {l.line_code for l in lease} == {LineCode.INTEREST_EXPENSE} and {l.sub_id for l in lease} == {"rates:lease:USD:LEASE"}
    assert len({l.amount for l in lease}) == 1 and lease[0].label is ProvenanceLabel.DERIVED
    assert not [l for l in base.cash_lines if l.line_code is LineCode.REFINANCING_COST and "LEASE" in l.sub_id]


def test_bond_coupon_unchanged_by_maturity_schedule(full):
    profile, exposures = full
    with_maturities = run_full(full, scenario(), PassType.BASE)
    without = run_full((profile, [e for e in exposures if e.kind != "maturity"]), scenario(), PassType.BASE)
    coupon = [l for l in with_maturities.cash_lines if l.sub_id == "rates:fixed:EUR:bond"]
    # semi-annual: half the annual 2.5% on EUR 300m on each coupon date in the horizon (P60)
    assert [(l.date, l.currency, l.amount) for l in coupon] == [
        (date(2027, 4, 15), Currency.EUR, Decimal("-3750000.00")),
        (date(2027, 10, 15), Currency.EUR, Decimal("-3750000.00")),
    ]
    assert coupon == [l for l in without.cash_lines if l.sub_id == "rates:fixed:EUR:bond"]
    # the fixed part of each slice comes from the USD fixed debt only
    fixed_with = sum(l.amount for l in with_maturities.cash_lines if l.sub_id == "rates:fixed:USD:1")
    fixed_without = sum(l.amount for l in without.cash_lines if l.sub_id == "rates:fixed:USD:1")
    assert fixed_with > fixed_without  # less interest once slices mature


def test_rollover_nets_to_zero_and_unavailable(full):
    base = run_full(full, scenario(), PassType.BASE)
    principal = {l.date: l.amount for l in base.cash_lines if l.line_code is LineCode.DEBT_PRINCIPAL}
    usd_draw = {l.date: l.amount for l in base.cash_lines if l.sub_id.startswith("rates:drawdown:USD")}
    assert principal and all(principal[d] + usd_draw[d] == 0 for d in principal)
    eur_items = {e.dates["maturity_date"]: float(e.quantities["amount"]) for e in full[1]
                 if e.owner_spec.value == "fx" and e.kind == "maturity"}
    for l in base.cash_lines:
        if l.sub_id.startswith("rates:drawdown:EUR"):
            assert l.currency is Currency.USD and float(l.amount) == pytest.approx(eur_items[l.date] * 1.15, abs=0.01)
    _, unavailable = both_full(full, scenario(refinancing_mode="unavailable"))
    codes = {l.line_code for l in unavailable.cash_lines}
    assert LineCode.DEBT_DRAWDOWN not in codes and LineCode.REFINANCING_COST not in codes
    assert LineCode.DEBT_PRINCIPAL in codes


def test_spread_widening_only_hits_new_borrowing(full):
    params = scenario(credit_spread_change_bp=150)
    base, scen = both_full(full, params)
    for prefix in ("rates:float", "rates:deposit", "rates:fixed", "rates:lease", "rates:maturity", "rates:drawdown"):
        assert by_sub(base, prefix) == by_sub(scen, prefix)
    assert by_sub(base, "rates:refi") != by_sub(scen, "rates:refi")
    zim = params.model_copy(update={"rates": params.rates.model_copy(update={"zim_tranche_on": True})})
    zb, zs = both_full(full, zim)
    assert sum(a for *_, a in by_sub(zs, "rates:zim")) < sum(a for *_, a in by_sub(zb, "rates:zim"))  # drawn after T0


def test_zim_is_state_never_a_delta(full):
    """P63: on the draw date a USD 2.5bn drawdown and a USD 4.2bn acquisition payment; USD 1.7bn of cash leaves."""
    on = scenario(zim_tranche_on=True, rate_path=step(50))
    base, scen = both_full(full, on)
    draw = date(2026, 12, 31)
    lines = {(l.line_code, l.date): l.amount for l in base.cash_lines if l.sub_id == "rates:zim:USD:1"}
    assert lines[(LineCode.DEBT_DRAWDOWN, draw)] == Decimal("2500000000.00")
    assert lines[(LineCode.ACQUISITION_PAYMENT, draw)] == Decimal("-4200000000.00")
    interest = sorted(d for c, d in lines if c is LineCode.INTEREST_EXPENSE)
    assert interest[0] == date(2027, 3, 31)  # quarterly from the draw date
    off_base, _ = both_full(full, scenario(rate_path=step(50)))
    assert not by_sub(off_base, "rates:zim")
    nfp_on = base.metrics["net_floating_position_usd"].value
    nfp_off = off_base.metrics["net_floating_position_usd"].value
    assert nfp_on[date(2026, 11, 30)] == nfp_off[date(2026, 11, 30)]
    # 2.5bn more floating debt and 1.7bn less interest-bearing cash: the position falls by 4.2bn
    assert nfp_off[draw] - nfp_on[draw] == pytest.approx(4_200_000_000)
    # deposit income falls once the cash part has left the USD deposits
    income = lambda out: sum(l.amount for l in out.cash_lines if l.sub_id.startswith("rates:deposit:USD"))
    assert income(base) < income(off_base)
    assert sum(l.amount for l in base.cash_lines if l.sub_id.startswith("rates:deposit:EUR")) == \
        sum(l.amount for l in off_base.cash_lines if l.sub_id.startswith("rates:deposit:EUR"))
    # the switch and its cash flows are identical in both passes: only the rate move changes ZIM interest
    flat_on_b, flat_on_s = both_full(full, scenario(zim_tranche_on=True))
    assert by_sub(flat_on_b, "rates:zim") == by_sub(flat_on_s, "rates:zim")
    zim_s = {(l.line_code, l.date): l.amount for l in scen.cash_lines if l.sub_id == "rates:zim:USD:1"}
    assert zim_s[(LineCode.ACQUISITION_PAYMENT, draw)] == lines[(LineCode.ACQUISITION_PAYMENT, draw)]


def test_zim_cash_part_reduces_usd_deposits_exactly(full):
    on = run_full(full, scenario(zim_tranche_on=True), PassType.BASE)
    off = run_full(full, scenario(), PassType.BASE)
    profile, exposures = full
    usd = [e for e in exposures if e.kind == "deposit" and e.currency.value == "USD"]
    total = sum(float(e.quantities["balance_usd"]) for e in usd)
    first = usd[0]
    rolls = sorted(first.dates["rolls"])
    # the period spanning 31 Dec 2026 loses (1.7bn x share) x yield for the days from the draw date
    start = max(r for r in [*rolls] if r <= date(2026, 12, 31))
    end = min(r for r in rolls if r > date(2026, 12, 31))
    cut = 1_700_000_000 * float(first.quantities["balance_usd"]) / total
    days_after = (end - date(2026, 12, 31)).days
    lost = cut * (4.0 - 0.10) / 100 * days_after / 360
    got = line_amount(off, first.sub_id, end) - line_amount(on, first.sub_id, end)
    assert float(got) == pytest.approx(lost, abs=0.02)
    assert start < date(2026, 12, 31) < end


def test_net_floating_position_falls_under_rollover(full):
    nfp = run_full(full, scenario(), PassType.BASE).metrics["net_floating_position_usd"].value
    values = [nfp[d] for d in sorted(nfp)]
    assert all(b < a for a, b in zip(values, values[1:]))  # fixed maturities roll to floating each month


def swap(target, coverage=100.0):
    return HedgeTest(module="rates", instrument="swap", target=target, coverage_pct=coverage, tenor_months=12,
                     start_date=date(2026, 11, 1))


def swap_delta(output_b, output_s):
    return sum(deltas_by_date(output_b, output_s, {LineCode.HEDGE_SETTLEMENT}).values())


def test_pay_fixed_saves_when_rates_rise_costs_when_they_fall(full):
    up = scenario(rate_path=step(100)).model_copy(update={"hedge_tests": [swap("floating_debt_usd")]})
    down = scenario(rate_path=step(-100)).model_copy(update={"hedge_tests": [swap("floating_debt_usd")]})
    ub, us = both_full(full, up)
    db, ds = both_full(full, down)
    assert swap_delta(ub, us) > 0 and swap_delta(db, ds) < 0
    margin = [l for l in ds.cash_lines if l.line_code is LineCode.MARGIN_CALL]
    assert margin and margin[0].amount < 0 and ds.metrics["swap_peak_posted_margin"].value > 0
    assert us.metrics["swap_fixed_rate_pct"].value == {"floating_debt_usd:1": 3.8}
    plain = run_full(full, scenario(), PassType.BASE).metrics["fixed_share_horizon_adjusted"].value
    assert ub.metrics["fixed_share_horizon_adjusted"].value > plain


def test_receive_fixed_on_deposits(full):
    down = scenario(rate_path=step(-100)).model_copy(update={"hedge_tests": [swap("deposits_eur")]})
    b, s = both_full(full, down)
    assert swap_delta(b, s) > 0
    assert {l.currency for l in s.cash_lines if l.line_code is LineCode.HEDGE_SETTLEMENT} == {Currency.EUR}


def test_swap_validation(full):
    for bad in (HedgeTest(module="rates", instrument="forward", target="floating_debt_usd", coverage_pct=50,
                          tenor_months=12, start_date=date(2026, 11, 1)), swap("zim_debt")):
        with pytest.raises(ModuleInputError):
            run_full(full, scenario().model_copy(update={"hedge_tests": [bad]}), PassType.BASE)


def test_eur_floor_binds_and_is_flagged(full):
    negative = {**MARKET, "ESTR": SnapshotEntry(-0.5, date(2026, 10, 30), "%")}
    base = run_full(full, scenario(), PassType.BASE, negative)
    assert base.metrics["binding_floor"].value is True
    first = [l for l in base.cash_lines if l.sub_id == "rates:float:EUR:1" and l.date == date(2026, 11, 1)][0]
    # period 1 Aug - 1 Nov (92 days) fixed at the 0% floor + 2.25% on EUR 199.91m
    assert first.currency is Currency.EUR and first.amount == Decimal("-1149482.50")
    assert base.diagnostics["floors"][0]["benchmark_pct"] == -0.5
    assert not [l for l in base.cash_lines if l.sub_id.startswith("rates:deposit:EUR")]  # yield floored at 0
    normal = run_full(full, scenario(), PassType.BASE)
    assert normal.metrics["binding_floor"].value is False


def test_eur_lines_stay_in_eur(full):
    base = run_full(full, scenario(), PassType.BASE)
    for l in base.cash_lines:
        if ":EUR:" in l.sub_id and not l.sub_id.startswith("rates:drawdown"):
            assert l.currency is Currency.EUR and l.amount_usd_base is None and l.fx_rate_base is None


def test_watch_list_and_metrics(full):
    base, scen = both_full(full, scenario(credit_spread_change_bp=150))
    items = scen.metrics["maturity_watch_list"].value
    assert len(items) == 12 and items[0]["month"] == date(2027, 11, 1)
    assert items[0]["stressed_new_issue_rate_pct"] == pytest.approx(items[0]["base_new_issue_rate_pct"] + 1.5)
    assert not [l for l in scen.cash_lines if l.date > H_END]
    path = base.metrics["interest_path_usd"].value
    assert all(v["expense"] < 0 < v["income"] for v in path.values())
    assert 0 < base.metrics["fixed_share_horizon_adjusted"].value < 1
    assert base.metrics["lease_liabilities_usd"].value == pytest.approx(3_637_100_000 * 1.2)
    assert base.metrics["benchmark_pct.SOFR"].value == 4.0


# ------------------------------------------------------------------ checks (8.13)


def test_static_sensitivity():
    data = yaml.safe_load((FIXTURES / "module_backtests.yaml").read_text())["rates"]
    check = static_sensitivity(data["variable_debt_eur_m"], data["published_up_m"], data["published_down_m"])
    assert check.up_effect == pytest.approx(-19.991) and check.down_effect == pytest.approx(19.991)
    assert round(check.up_diff * 100, 1) == -3.9 and round(check.down_diff * 100, 1) == -7.0
    assert check.signs_match and check.passed
    floored = static_sensitivity(1999.1, -20.8, 21.5, benchmark_pct=0.5)
    assert floored.down_effect == pytest.approx(1999.1 * 0.005) and not floored.passed


def test_calibration_is_reported(full):
    profile, exposures = full
    result = calibration_check(exposures, 4.0, 2.0, 1.15, 236_000_000, 69_000_000)
    assert result.expense_target_usd == 472_000_000 and result.income_target_usd == 138_000_000
    assert result.expense_annual_usd > 0 and result.income_annual_usd > 0
    assert isinstance(result.expense_within, bool) and isinstance(result.income_within, bool)
    assert result.adjustment_order["deposit_income"][0] == "rates.deposit_spread_bp"


# ------------------------------------------------------------------ placeholders and presets


def test_active_profile_builds_presets_on_disclosed_meeting_dates(real_profile):
    ctx = ProfileContext(RunContext(real_profile, T0))
    cycle = build(ctx, PresetId.FED_CUTTING_CYCLE).params
    assert [s.date for s in cycle.rates.rate_path[Currency.USD]] == [
        date(2026, 12, 9), date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28)]  # decision days
    items, missing = RATES.exposures_and_missing(ctx)
    assert missing == []


def test_real_profile_reports_missing_inputs(v1_profile):
    real_profile = v1_profile
    ctx = ProfileContext(RunContext(real_profile, T0))
    items, missing = RATES.exposures_and_missing(ctx)
    keys = {m.key for m in missing}
    assert "fx.usd_per_eur_close_2025_12_31" in keys
    register = generate(real_profile, T0)
    run = RATES.run(ModuleRunContext(ctx, PassType.BASE, MARKET), scenario(), register.items, ModuleInputs())
    assert run.cash_lines  # EUR floating, bond, deposits still run
    assert any(m.key == "fx.usd_per_eur_close_2025_12_31" for m in run.diagnostics["missing"])  # fixed share metric
    no_fx = {k: v for k, v in MARKET.items() if k != "FX_USD_PER_EUR"}
    partial = RATES.run(ModuleRunContext(ctx, PassType.BASE, no_fx), scenario(), register.items, ModuleInputs())
    assert any(m.key == "FX_USD_PER_EUR" and m.status == "missing_market_data" for m in partial.diagnostics["missing"])
    built = build(ctx, PresetId.FED_CUTTING_CYCLE)
    assert built.params is None and built.missing[0].key == "rates.fomc_meeting_dates"


def test_presets(full):
    profile, _ = full
    ctx = ProfileContext(RunContext(profile, T0))
    cuts = build(ctx, PresetId.FED_CUTS_ECB_HOLDS).params
    assert [(s.date, s.step_bp) for s in cuts.rates.rate_path[Currency.USD]] == [(date(2026, 12, 9), -25), (date(2027, 1, 27), -25)]
    hiking = build(ctx, PresetId.BOTH_HIKING).params
    assert len(hiking.rates.rate_path[Currency.EUR]) == 3 and hiking.rates.rate_path[Currency.EUR][0].step_bp == 25
    cycle = build(ctx, PresetId.FED_CUTTING_CYCLE)  # the fixture profile lists only three FOMC meetings
    assert cycle.params is None and "3 meetings" in cycle.missing[0].status
    squeeze = build(ctx, PresetId.CREDIT_SQUEEZE).params
    assert squeeze.rates.credit_spread_change_bp == 200 and squeeze.rates.refinancing_mode.value == "unavailable"
    up = build(ctx, PresetId.RATES_UP_100BP).params
    assert up.rates.rate_path[Currency.USD][0].date == T0 and up.drivers_set() == [up.drivers_set()[0]]
    assert build(ctx, PresetId.SPREAD_WIDENING).params.rates.credit_spread_change_bp == 150


def test_step_on_t0_does_not_change_the_running_period(full):
    base, scen = both_full(full, scenario(rate_path=step(100)))
    first = min(l.date for l in base.cash_lines if l.sub_id == "rates:float:USD:1")
    assert line_amount(base, "rates:float:USD:1", first) == line_amount(scen, "rates:float:USD:1", first)


def test_annual_coupon_profile_still_pays_once():
    """Profile version 2026H1-v1 (one coupon date) pays the full annual coupon once."""
    from hlag_engine.company_profile.schema import DEFAULT_PROFILE_PATH

    profile = profile_from_dict(complete_raw(DEFAULT_PROFILE_PATH))
    items = generate(profile, T0).items
    bond = [e for e in items if e.sub_id == "rates:fixed:EUR:bond"][0]
    assert bond.quantities["coupons_per_year"] == 1
    ctx = ModuleRunContext(ProfileContext(RunContext(profile, T0)), PassType.BASE, MARKET)
    out = RATES.run(ctx, scenario(), items, ModuleInputs())
    assert [(l.date, l.amount) for l in out.cash_lines if l.sub_id == "rates:fixed:EUR:bond"] == [
        (date(2027, 10, 15), Decimal("-7500000.00"))]


def test_bond_coupon_after_maturity_is_dropped(full):
    profile, exposures = full
    raw = complete_with_v2_bond()
    raw["parameters"]["rates.bond_maturity_date"]["value"] = date(2027, 6, 30)
    early = profile_from_dict(raw)
    ctx = ModuleRunContext(ProfileContext(RunContext(early, T0)), PassType.BASE, MARKET)
    out = RATES.run(ctx, scenario(), exposures, ModuleInputs())
    assert [l.date for l in out.cash_lines if l.sub_id == "rates:fixed:EUR:bond"] == [date(2027, 4, 15)]
