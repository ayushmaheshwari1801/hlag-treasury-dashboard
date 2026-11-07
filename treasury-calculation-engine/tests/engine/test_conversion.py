"""FX conversion layer functions (9A) on small hand-made inputs."""

from datetime import date
from decimal import Decimal

import pytest

from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import Currency, LineCode, PassType
from hlag_engine.contracts.policy_hedge import PolicyHedgeSpec
from hlag_engine.contracts.scenario import FxParams, ScenarioParams
from hlag_engine.engine.conversion import (
    FxConversionError,
    apply_policy_hedges,
    convert_lines,
    convert_net,
    display_in_eur,
    fx_paths,
    net_positions,
    rate_on,
    to_usd,
    translation_memo,
)
from hlag_engine.modules.interface import SnapshotEntry

T0 = date(2026, 10, 31)
MARKET = {"FX_USD_PER_EUR": SnapshotEntry(1.15, date(2026, 10, 30), "USD per unit"),
          "FX_USD_PER_INR": SnapshotEntry(0.0113, date(2026, 10, 30), "USD per unit")}


def params(**moves):
    return ScenarioParams(name="t", start_date=date(2026, 11, 1), source="custom",
                          fx=FxParams(move_pct={Currency(k): v for k, v in moves.items()}))


def line(sub_id, when, ccy, amount, owner="fx", code="FX_OPERATING_COST"):
    return CashLine(sub_id=sub_id, date=when, currency=ccy, amount=Decimal(amount), owner_spec=owner,
                    line_code=code, label="ASSUMPTION")


def test_to_usd_is_a_multiplication_and_usd_is_one():
    paths = fx_paths(MARKET, params(EUR=10), {Currency.EUR})
    assert to_usd(Decimal("-100.00"), Currency.EUR, date(2026, 12, 1), paths, PassType.BASE) == (Decimal("1.15"), Decimal("-115.00"))
    assert to_usd(Decimal("-100.00"), Currency.EUR, date(2026, 12, 1), paths, PassType.SCENARIO)[1] == Decimal("-126.50")
    assert to_usd(Decimal("7.00"), Currency.USD, date(2026, 12, 1), paths, PassType.SCENARIO) == (Decimal(1), Decimal("7.00"))
    assert to_usd(Decimal("-100.00"), Currency.EUR, date(2026, 10, 31), paths, PassType.SCENARIO)[0] == Decimal("1.15")  # before start


def test_convert_lines_fills_all_four_fields():
    paths = fx_paths(MARKET, params(EUR=-5), {Currency.EUR})
    [eur, usd] = convert_lines([line("fx:opex:EUR:1", date(2026, 12, 4), "EUR", "-1000.00"),
                                line("rates:drawdown:USD:1", date(2026, 12, 4), "USD", "500.00", "rates", "DEBT_DRAWDOWN")], paths)
    assert (eur.fx_rate_base, eur.fx_rate_scenario, eur.amount_usd_base, eur.amount_usd_scenario) == (
        Decimal("1.15"), Decimal("1.0925"), Decimal("-1150.00"), Decimal("-1092.50"))
    assert (usd.fx_rate_base, usd.fx_rate_scenario, usd.amount_usd_base) == (1, 1, Decimal("500.00"))


def test_missing_currency_raises():
    with pytest.raises(FxConversionError):
        fx_paths(MARKET, params(), {Currency.BRL})
    paths = fx_paths(MARKET, params(), {Currency.EUR})
    with pytest.raises(FxConversionError):
        convert_lines([line("fx:x:BRL:1", date(2026, 12, 4), "BRL", "-1.00", code="FX_TRANSACTION")], paths)


def test_netting_agrees_with_line_by_line_to_the_cent():
    lines = [line(f"fx:opex:EUR:{i}", date(2026, 12, 4 + i % 3), "EUR", f"-{1000 + 37 * i}.{i % 100:02d}") for i in range(60)]
    lines += [line(f"freight:spot:X:{i}", date(2026, 12, 4 + i % 3), "EUR", f"{555 + 11 * i}.{(7 * i) % 100:02d}",
                   "freight", "FREIGHT_REVENUE") for i in range(40)]
    paths = fx_paths(MARKET, params(EUR=7), {Currency.EUR})
    for pass_type, attr in ((PassType.BASE, "amount_usd_base"), (PassType.SCENARIO, "amount_usd_scenario")):
        exact = sum(l.amount * rate_on(paths, Currency.EUR, l.date, pass_type) for l in lines)
        assert convert_net(net_positions(lines), paths, pass_type) == exact
        rounded = sum(getattr(l, attr) for l in convert_lines(lines, paths))
        assert abs(rounded - exact) <= Decimal("0.005") * len(lines)  # per-line cent rounding only
        assert convert_net(net_positions(lines), paths, pass_type).quantize(Decimal("0.01")) == exact.quantize(Decimal("0.01"))


def spec(policy, sub_id, ccy, code, ratio, start, locked=None):
    return PolicyHedgeSpec(policy=policy, currency=ccy, hedged_sub_id=sub_id, hedged_line_code=code, ratio=ratio,
                           start_date=start, locked_rate=locked)


def test_contract_rate_rules():
    paths = fx_paths(MARKET, params(EUR=10, INR=-15), {Currency.EUR, Currency.INR})
    lines = [line("fx:opex:EUR:a", date(2027, 1, 8), "EUR", "-100.00"),
             line("fx:opex:EUR:b", date(2027, 1, 15), "EUR", "-100.00"),
             line("fx:obligation:INR:1", date(2026, 11, 30), "INR", "-1000.00", code="FX_TRANSACTION")]
    specs = [spec("eur_operating_costs", "fx:opex:EUR:a", "EUR", "FX_OPERATING_COST", 1.0, date(2026, 9, 1)),
             spec("eur_operating_costs", "fx:opex:EUR:b", "EUR", "FX_OPERATING_COST", 0.5, date(2026, 12, 1)),
             spec("inr_brl_obligations", "fx:obligation:INR:1", "INR", "FX_TRANSACTION", 1.0, date(2025, 12, 31), 0.012),
             spec("eur_operating_costs", "fx:opex:EUR:missing", "EUR", "FX_OPERATING_COST", 1.0, T0)]
    history = {"FX_USD_PER_EUR": [(date(2026, 8, 28), 1.10)]}
    res = apply_policy_hedges(specs, lines, paths, PassType.SCENARIO, T0, history)
    by = {c.spec.hedged_sub_id: c for c in res.contracts}
    assert by["fx:opex:EUR:a"].contract_rate == 1.10  # from history (latest on or before the start)
    assert by["fx:opex:EUR:b"].contract_rate == pytest.approx(1.265)  # after T0: the pass's path
    assert by["fx:obligation:INR:1"].contract_rate == 0.012  # locked
    assert by["fx:opex:EUR:a"].settlement_usd == pytest.approx(100 * (1.265 - 1.10))
    assert by["fx:obligation:INR:1"].settlement_usd == pytest.approx(1000 * (0.0113 * 0.85 - 0.012))
    assert "fx:opex:EUR:missing" not in by  # no line, no contract
    none = apply_policy_hedges(specs[:1], lines, paths, PassType.BASE, T0, {})
    assert none.contracts == [] and none.missing[0].status == "no_history_on_start_date"


def test_translation_memo_and_display():
    paths = fx_paths({**MARKET, "FX_USD_PER_CNY": SnapshotEntry(0.14, date(2026, 10, 30), "USD per unit")},
                     params(CNY=-10), {Currency.CNY})
    assert translation_memo({Currency.CNY: 85.0}, paths, date(2027, 10, 31))[Currency.CNY] == pytest.approx(-8.5)
    assert display_in_eur(Decimal("115.00"), 1.15) == Decimal(100)
