"""FX module and conversion layer: worked example 9B.13 to five decimals and the tests of 9B.14."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hlag_engine.company_profile.resolve import OverrideNotAllowed, RunContext
from hlag_engine.company_profile.schema import profile_from_dict
from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import Currency, LineCode, OwnerSpec, PassType, PresetId
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.contracts.policy_hedge import HedgePolicy, PolicyHedgeSpec
from hlag_engine.engine.conversion import (
    FxConversionError,
    apply_policy_hedges,
    convert_lines,
    display_in_eur,
    fx_paths,
    net_positions,
    translation_memo,
)
from hlag_engine.generator.generate import generate
from hlag_engine.modules.fx import FxModule
from hlag_engine.modules.fx.checks import cad_sizing, sign_check, storage_problems
from hlag_engine.modules.fx.presets import build as build_preset
from hlag_engine.modules.interface import ModuleInputError, ModuleInputs, ModuleRunContext, ProfileContext, SnapshotEntry
from tests.conftest import T0, complete_raw
from tests.modules.helpers import load_fixture, run_both, usd_m

FX = FxModule()
M = Decimal(1_000_000)


@pytest.fixture(scope="module")
def fx():
    return load_fixture("fx_example.yaml")


def run_layer(fixture, params=None, fx_out=None, extra_lines=()):
    """Run the FX module for both passes, then the 9A functions, as step 5 will."""
    params = params or fixture.scenario
    base_out, scen_out = fx_out or run_both(FX, fixture, params)
    other = [CashLine.model_validate(l) for l in fixture.raw.get("other_lines", [])] + list(extra_lines)
    lines_b, lines_s = base_out.cash_lines + other, scen_out.cash_lines + other
    memo = set(base_out.diagnostics.get("memo_positions_usd", {}))  # memo currencies need paths too (9A.5)
    paths = fx_paths(fixture.market, params, {l.currency for l in lines_b + lines_s} | memo)
    hb = apply_policy_hedges(base_out.policy_hedges, lines_b, paths, PassType.BASE, fixture.as_of, fixture.history)
    hs = apply_policy_hedges(scen_out.policy_hedges, lines_s, paths, PassType.SCENARIO, fixture.as_of, fixture.history)
    return {
        "paths": paths, "hb": hb, "hs": hs, "out": (base_out, scen_out),
        "base": convert_lines(lines_b + hb.settlement_lines, paths),
        "scen": convert_lines(lines_s + hs.settlement_lines, paths),
    }


def pair(base, scen):
    """Full outer join on the stable key (2.1)."""
    out = defaultdict(lambda: [None, None])
    for line in base:
        out[line.line_key][0] = line
    for line in scen:
        out[line.line_key][1] = line
    return out


def delta(layer):
    return sum(l.amount_usd_scenario for l in layer["scen"]) - sum(l.amount_usd_base for l in layer["base"])


def bars(layer):
    """Attribution with the 4.3 formula (test helper; the engine's attribution is step 5)."""
    bars = defaultdict(Decimal)
    for a, b in pair(layer["base"], layer["scen"]).values():
        ref = a or b
        a_amt, b_amt = (a.amount if a else Decimal(0)), (b.amount if b else Decimal(0))
        x = (a or b).fx_rate_base
        y = (b or a).fx_rate_scenario
        driver = (b_amt - a_amt) * x
        if ref.owner_spec is OwnerSpec.FX:
            bars["fx"] += driver
        else:
            bars[ref.owner_spec.value] += driver
        bars["fx"] += b_amt * (y - x)
    return bars


def usd_of(lines, sub_id, attr):
    return sum((getattr(l, attr) for l in lines if l.sub_id == sub_id), Decimal(0))


# ------------------------------------------------------------------ worked example 9B.13


def test_lines(fx):
    layer = run_layer(fx)
    for sub_id, code, base_usd, scen_usd, d in fx.expected["lines"]:
        b = [l for l in layer["base"] if l.sub_id == sub_id and l.line_code is LineCode(code)]
        s = [l for l in layer["scen"] if l.sub_id == sub_id and l.line_code is LineCode(code)]
        assert usd_m(b[0].amount_usd_base, base_usd) and usd_m(s[0].amount_usd_scenario, scen_usd), sub_id
        assert usd_m(s[0].amount_usd_scenario - b[0].amount_usd_base, d), sub_id


def test_hedges(fx):
    layer = run_layer(fx)
    by_b = {c.spec.hedged_sub_id: c for c in layer["hb"].contracts}
    by_s = {c.spec.hedged_sub_id: c for c in layer["hs"].contracts}
    for sub_id, notional, k_b, k_s, base_usd, scen_usd, d in fx.expected["hedges"]:
        cb, cs = by_b[sub_id], by_s[sub_id]
        assert cb.notional == pytest.approx(float(notional) * 1e6) == cs.notional
        assert cb.contract_rate == pytest.approx(float(k_b)) and cs.contract_rate == pytest.approx(float(k_s))
        hedge_id = f"fx:hedge:{cb.spec.policy.value}:{sub_id}"
        b, s = usd_of(layer["base"], hedge_id, "amount_usd_base"), usd_of(layer["scen"], hedge_id, "amount_usd_scenario")
        assert usd_m(b, base_usd) and usd_m(s, scen_usd) and usd_m(s - b, d), sub_id
    assert by_b["fx:opex:CAD:2026-11-30"].spec.start_date == date(2026, 8, 31)  # before T0: K from history
    assert by_b["fx:opex:CAD:2027-02-28"].spec.start_date == date(2026, 11, 29)  # after T0: K from the pass's path


def test_total_and_attribution(fx):
    layer = run_layer(fx)
    total = delta(layer)
    assert total == Decimal("-8415040.00") and usd_m(total, fx.expected["total_delta"])
    b = bars(layer)
    assert usd_m(b["fx"], fx.expected["fx_bar"])
    assert b["freight"] == 0 and b["rates"] == 0 and b.get("bunker", Decimal(0)) == 0
    assert b["fx"] + b["freight"] + b["rates"] == total


def test_net_eur_need_and_unhedged_window(fx):
    layer = run_layer(fx)
    positions = net_positions(layer["base"], layer["hb"].contracts)
    dec15 = positions[(Currency.EUR, date(2026, 12, 15))]
    assert usd_m(dec15.net, fx.expected["net_eur_need_2026_12_15"]) and dec15.hedged == 0
    assert dec15.gross_outflows == Decimal("-52000000.00") and dec15.gross_inflows == Decimal("12000000.00")
    unhedged = -sum(p.unhedged_net for (c, d), p in positions.items() if c is Currency.EUR)
    assert usd_m(unhedged, fx.expected["unhedged_eur_window"]["amount"])
    assert usd_m(unhedged * Decimal("-0.115"), fx.expected["unhedged_eur_window"]["delta"])
    four = ["fx:opex:EUR:2026-11-30", "fx:dividend:EUR:2026-12-15", "rates:float:EUR:1", "freight:spot:ASIA_EUROPE:arr"]
    four_delta = sum(usd_of(layer["scen"], s, "amount_usd_scenario") - usd_of(layer["base"], s, "amount_usd_base") for s in four)
    assert usd_m(four_delta, "-8.05")
    principal = positions[(Currency.EUR, date(2026, 12, 20))]
    assert principal.unhedged_net == 0  # fully hedged


def test_eur_principal_fully_hedged(fx):
    layer = run_layer(fx)
    sub = "fx:maturity:EUR:2026-12"
    principal = usd_of(layer["scen"], sub, "amount_usd_scenario") - usd_of(layer["base"], sub, "amount_usd_base")
    hedge = usd_of(layer["scen"], f"fx:hedge:eur_debt_principal:{sub}", "amount_usd_scenario")
    assert usd_m(principal + hedge, fx.expected["eur_principal_net"])


# ------------------------------------------------------------------ acceptance 9B.14


def test_sign_check():
    rates = {"EUR": 1.15, "CNY": 0.14, "GBP": 1.32}
    down, up = sign_check(rates, Decimal("-0.1")), sign_check(rates, Decimal("0.1"))
    assert down.effects == {"EUR": Decimal("4.0"), "CNY": Decimal("-8.5"), "GBP": Decimal("2.4")} and down.passed
    assert up.effects == {"EUR": Decimal("-4.0"), "CNY": Decimal("8.5"), "GBP": Decimal("-2.4")} and up.passed


def test_cad_ratio_above_80_fails(fx):
    with pytest.raises(ValidationError, match="80%"):
        PolicyHedgeSpec(policy="cad_operating_costs", currency="CAD", hedged_sub_id="x",
                        hedged_line_code="FX_OPERATING_COST", ratio=0.85, start_date=T0)
    over = HedgeTest(module="fx", instrument="forward", target="cad_operating_costs", coverage_pct=85,
                     tenor_months=12, start_date=date(2026, 11, 1))
    with pytest.raises(ModuleInputError, match="80%"):
        run_both(FX, fx, fx.scenario.model_copy(update={"hedge_tests": [over]}))
    with pytest.raises(OverrideNotAllowed):
        run_both(FX, fx, fx.scenario.model_copy(update={"overrides": {"fx.hedge_ratio.cad": 0.85}}))


def test_every_line_carries_both_rates(fx):
    layer = run_layer(fx)
    for line in layer["base"] + layer["scen"]:
        assert None not in (line.fx_rate_base, line.fx_rate_scenario, line.amount_usd_base, line.amount_usd_scenario)
        if line.currency is Currency.USD:
            assert line.fx_rate_base == line.fx_rate_scenario == 1
    eur = next(l for l in layer["scen"] if l.currency is Currency.EUR)
    assert eur.fx_rate_base == Decimal("1.15") and eur.fx_rate_scenario == Decimal("1.265")


def test_netting_changes_no_usd_total(fx):
    layer = run_layer(fx)
    for lines, pass_type, attr in ((layer["base"], PassType.BASE, "amount_usd_base"),
                                   (layer["scen"], PassType.SCENARIO, "amount_usd_scenario")):
        line_by_line = sum(getattr(l, attr) for l in lines if l.currency is not Currency.USD)
        from hlag_engine.engine.conversion import convert_net

        net = convert_net(net_positions(lines), layer["paths"], pass_type)
        assert line_by_line == net.quantize(Decimal("0.01"))


def test_memo_never_on_the_path(full):
    base, scen = full["out"]
    assert {l.line_code for l in base.cash_lines} & {LineCode.FX_POSITION} == set()
    assert not [l for l in base.cash_lines if ":memo:" in l.sub_id]
    memo = base.diagnostics["memo_positions_usd"]
    assert memo == {Currency.CNY: 85_000_000, Currency.GBP: -24_000_000}
    assert base.diagnostics["memo_positions_native"][Currency.CNY] == pytest.approx(85_000_000 / 0.14)
    result = translation_memo(memo, full["paths"], date(2027, 10, 31))
    assert result[Currency.CNY] == pytest.approx(85_000_000 * -0.10)  # CNY devaluation in the full run


def test_zero_shock_fx_bar_is_exactly_zero(fx):
    layer = run_layer(fx, fx.scenario.zero_shock())
    assert delta(layer) == 0 and bars(layer)["fx"] == 0


def test_display_in_eur_changes_no_result(fx):
    layer = run_layer(fx)
    b = sum(l.amount_usd_base for l in layer["base"])
    s = sum(l.amount_usd_scenario for l in layer["scen"])
    rate = 1.15
    cent = Decimal("0.01")
    assert (display_in_eur(s, rate) - display_in_eur(b, rate)).quantize(cent) == display_in_eur(s - b, rate).quantize(cent)
    assert (display_in_eur(s - b, rate) < 0) == (s - b < 0)


def test_eur_maturity_rollover_hedged_and_unhedged(fx):
    drawdown = CashLine(sub_id="rates:drawdown:EUR:2026-12", date=date(2026, 12, 20), currency="USD",
                        amount=Decimal("23000000.00"), owner_spec="rates", line_code="DEBT_DRAWDOWN", label="ASSUMPTION")
    keys = ("fx:maturity:EUR:2026-12", "rates:drawdown:EUR:2026-12", "fx:hedge:eur_debt_principal:fx:maturity:EUR:2026-12")

    def net(layer):
        return sum(usd_of(layer["scen"], k, "amount_usd_scenario") - usd_of(layer["base"], k, "amount_usd_base") for k in keys)

    assert net(run_layer(fx, extra_lines=[drawdown])) == 0
    unhedged = load_fixture("fx_example.yaml", profile_values={"fx.hedge_ratio.eur_debt": 0.0})
    assert net(run_layer(unhedged, extra_lines=[drawdown])) == Decimal(-0.10 * 20_000_000 * 1.15).quantize(Decimal("0.01"))


# ------------------------------------------------------------------ full test profile, switch and missing inputs


MARKET = {
    "FX_USD_PER_EUR": SnapshotEntry(1.15, date(2026, 10, 30), "USD per unit"),
    "FX_USD_PER_CAD": SnapshotEntry(0.72, date(2026, 10, 30), "USD per unit"),
    "FX_USD_PER_GBP": SnapshotEntry(1.32, date(2026, 10, 30), "USD per unit"),
    "FX_USD_PER_CNY": SnapshotEntry(0.14, date(2026, 10, 30), "USD per unit"),
    "FX_USD_PER_INR": SnapshotEntry(0.0113, date(2026, 10, 30), "USD per unit"),
    "FX_USD_PER_BRL": SnapshotEntry(0.185, date(2026, 10, 30), "USD per unit"),
}
HISTORY = {f"FX_USD_PER_{c}": [(date(2026, 7, 1), e.value)] for c, e in
           ((k.removeprefix("FX_USD_PER_"), v) for k, v in MARKET.items())}


def run_profile(profile, params, market=MARKET):
    exposures = generate(profile, T0).items
    outs = []
    for pass_type in (PassType.BASE, PassType.SCENARIO):
        ctx = ModuleRunContext(ProfileContext(RunContext.for_scenario(profile, T0, params)), pass_type, market, HISTORY)
        outs.append(FX.run(ctx, params.zero_shock() if pass_type is PassType.BASE else params, exposures, ModuleInputs()))
    return outs


class _Fixture:
    def __init__(self, market, history):
        self.market, self.history, self.as_of, self.raw = market, history, T0, {}


@pytest.fixture(scope="module")
def full():
    profile = profile_from_dict(complete_raw())
    params = build_preset(PresetId.CNY_DEVALUATION, T0)
    outs = run_profile(profile, params)
    layer = run_layer(_Fixture(MARKET, HISTORY), params, fx_out=outs)
    return layer


def test_full_profile_lines_and_hedges(full):
    base, scen = full["out"]
    codes = defaultdict(int)
    for l in base.cash_lines:
        codes[(l.line_code, l.currency)] += 1
    assert codes[(LineCode.FX_OPERATING_COST, Currency.CAD)] == 52 and codes[(LineCode.FX_OPERATING_COST, Currency.EUR)] == 52
    assert codes[(LineCode.FX_TRANSACTION, Currency.INR)] == 12 and codes[(LineCode.FX_TRANSACTION, Currency.BRL)] == 12
    assert codes[(LineCode.DEBT_PRINCIPAL, Currency.EUR)] == 12 and codes[(LineCode.DIVIDEND, Currency.EUR)] == 1
    policies = defaultdict(int)
    for spec in base.policy_hedges:
        policies[spec.policy] += 1
    assert policies == {HedgePolicy.CAD_OPERATING_COSTS: 52, HedgePolicy.EUR_DEBT_PRINCIPAL: 12,
                        HedgePolicy.INR_BRL_OBLIGATIONS: 24}  # EUR opex and dividend at 0%
    assert base.policy_hedges == scen.policy_hedges  # state, identical in both passes
    assert {s.locked_rate for s in base.policy_hedges if s.currency is Currency.INR} == {0.012}
    assert base.metrics["sign_check"].value is True and base.metrics["hedge_ratio.cad_operating_costs"].value == 0.7
    assert all(l.line_code is LineCode.HEDGE_SETTLEMENT and l.currency is Currency.USD
               for l in full["hb"].settlement_lines)


def test_inr_brl_switch_flips_per_run():
    """P66: the same scenario with the override true vs false: lines and hedges in one, none in the other."""
    profile = profile_from_dict(complete_raw())
    base_params = build_preset(PresetId.EM_STRESS, T0)
    results = {}
    for on in (True, False):
        params = base_params.model_copy(update={"overrides": {"fx.inr_brl_obligations_on": on}})
        b, s = run_profile(profile, params)
        layer = run_layer(_Fixture(MARKET, HISTORY), params, fx_out=(b, s))
        tx = [l for l in b.cash_lines if l.line_code is LineCode.FX_TRANSACTION]
        hedges = [h for h in b.policy_hedges if h.policy is HedgePolicy.INR_BRL_OBLIGATIONS]
        results[on] = (len(tx), len(hedges), len(layer["hb"].settlement_lines))
        # no delta between passes in the switch itself: same native lines and same hedge specs
        assert [l for l in b.cash_lines if l.line_code is LineCode.FX_TRANSACTION] == \
            [l for l in s.cash_lines if l.line_code is LineCode.FX_TRANSACTION]
        assert b.policy_hedges == s.policy_hedges
        assert b.diagnostics["inr_brl_obligations_on"] is on
    assert results[True][:2] == (24, 24) and results[False][:2] == (0, 0)
    assert results[True][2] > results[False][2]


def test_pending_lock_rates_skip_inr_brl_hedges(v2_profile):
    outs = run_profile(v2_profile, build_preset(PresetId.EM_STRESS, T0))
    base = outs[0]
    keys = {m.key for m in base.diagnostics["missing"]}
    assert keys == {"fx.lock_rate_usd_per_unit.INR", "fx.lock_rate_usd_per_unit.BRL"}
    assert [l for l in base.cash_lines if l.line_code is LineCode.FX_TRANSACTION]  # lines still emitted
    assert not [h for h in base.policy_hedges if h.policy is HedgePolicy.INR_BRL_OBLIGATIONS]
    assert [h for h in base.policy_hedges if h.policy is HedgePolicy.EUR_DEBT_PRINCIPAL]


def test_missing_currency_raises_stale_does_not(fx):
    no_cad = {k: v for k, v in fx.market.items() if k != "FX_USD_PER_CAD"}
    with pytest.raises(FxConversionError, match="FX_USD_PER_CAD"):
        fx_paths(no_cad, fx.scenario, {Currency.CAD})
    stale = {**fx.market, "FX_USD_PER_CAD": SnapshotEntry(0.72, date(2026, 10, 1), "USD per unit", 30, stale=True)}
    paths = fx_paths(stale, fx.scenario, {Currency.CAD, Currency.EUR})
    assert paths.base[Currency.CAD](date(2026, 12, 1)) == 0.72


def test_hedge_tool_raises_eur_opex_ratio(fx):
    test = HedgeTest(module="fx", instrument="forward", target="eur_operating_costs", coverage_pct=50,
                     tenor_months=3, start_date=date(2026, 11, 1))
    layer = run_layer(fx, fx.scenario.model_copy(update={"hedge_tests": [test]}))
    contract = next(c for c in layer["hs"].contracts if c.spec.policy is HedgePolicy.EUR_OPERATING_COSTS)
    assert contract.notional == pytest.approx(15_000_000) and contract.spec.start_date == date(2026, 11, 1)
    assert contract.contract_rate == pytest.approx(1.265)  # started after T0: the scenario path's spot
    with pytest.raises(ModuleInputError):
        run_both(FX, fx, fx.scenario.model_copy(update={"hedge_tests": [test.model_copy(update={"target": "gbp_costs"})]}))


def test_cad_sizing_and_storage_checks():
    share, ok = cad_sizing(57.5, 72)
    assert round(share * 100, 1) == 79.9 and ok
    assert storage_problems([("FX_USD_PER_EUR", 1.15, "USD per unit"), ("FX_USD_PER_INR", 0.0113, "USD per unit")]) == []
    problems = storage_problems([("FX_USD_PER_INR", 88.5, "USD per unit"), ("FX_USD_PER_EUR", 0.87, "EUR per USD")])
    assert len(problems) == 2 and "plausible band" in problems[0] and "not USD per unit" in problems[1]


def test_presets():
    rally = build_preset(PresetId.EUR_RALLY, T0)
    assert rally.fx.move_pct == {Currency.EUR: 10.0, Currency.GBP: 5.0, Currency.CAD: 3.0} and rally.duration_days is None
    broad = build_preset(PresetId.BROAD_USD_STRENGTHENING, T0)
    assert broad.fx.move_pct[Currency.CNY] == -4.0 and broad.fx.move_pct[Currency.INR] == -10.0
    up = build_preset(PresetId.FX_INR_UP_10, T0)
    assert up.fx.move_pct == {Currency.INR: 10.0} and up.drivers_set() == [OwnerSpec.FX]


def test_scenario_validation_accepts_the_switch_override(real_profile):
    from hlag_engine.company_profile.scenario_validation import load_scenario_ranges, scenario_problems

    ranges = load_scenario_ranges()
    base = build_preset(PresetId.EM_STRESS, T0)
    for value in (True, False):
        ok = base.model_copy(update={"overrides": {"fx.inr_brl_obligations_on": value}})
        assert scenario_problems(ok, real_profile, ranges) == []
    bad = base.model_copy(update={"overrides": {"fx.inr_brl_obligations_on": 1}})
    assert scenario_problems(bad, real_profile, ranges)


def test_active_profile_lock_rates_build_inr_brl_hedges(real_profile):
    """Profile v3 has the 31 Dec 2025 lock rates, so the INR/BRL obligation hedges are built at them (9B.4)."""
    outs = run_profile(real_profile, build_preset(PresetId.EM_STRESS, T0))
    base = outs[0]
    assert not {m.key for m in base.diagnostics["missing"]} & {"fx.lock_rate_usd_per_unit.INR", "fx.lock_rate_usd_per_unit.BRL"}
    hedges = [h for h in base.policy_hedges if h.policy is HedgePolicy.INR_BRL_OBLIGATIONS]
    assert {h.currency.value for h in hedges} == {"INR", "BRL"}
    locked = {h.currency.value: h.locked_rate for h in hedges}
    assert locked == {"INR": 0.01112726274, "BRL": 0.1825554658}
