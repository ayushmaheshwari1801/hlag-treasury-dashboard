from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import CashLine, VolumeLine, round_to_cents


def cash(**overrides):
    data = {
        "sub_id": "ASIA_EUROPE_SPOT",
        "date": date(2026, 12, 31),
        "currency": "USD",
        "amount": Decimal("1000000.00"),
        "owner_spec": "freight",
        "line_code": "FREIGHT_REVENUE",
        "label": "ASSUMPTION",
    }
    data.update(overrides)
    return CashLine(**data)


def test_valid_cash_line_and_line_key():
    line = cash()
    assert line.line_key == (line.owner_spec, line.line_code, line.date, line.currency, line.sub_id)
    assert line.fx_rate_base is None and line.pass_type is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", "JPY"),
        ("owner_spec", "engine"),
        ("line_code", "FREIGHT"),
        ("label", "ESTIMATE"),
        ("pass_type", "stress"),
        ("sub_id", ""),
    ],
)
def test_cash_line_rejects_values_outside_enums(field, value):
    with pytest.raises(ValidationError):
        cash(**{field: value})


def test_cash_line_rejects_unknown_field():
    with pytest.raises(ValidationError):
        cash(net_amount=1)


def test_owner_must_match_line_code():
    with pytest.raises(ValidationError, match="may not emit"):
        cash(owner_spec="bunker")  # FREIGHT_REVENUE belongs to freight


@pytest.mark.parametrize("code", ["FX_POSITION", "FX_CONVERSION_EFFECT"])
def test_memo_and_aggregation_codes_are_never_cash_lines(code):
    with pytest.raises(ValidationError, match="never emitted"):
        cash(line_code=code, owner_spec="fx")


def test_debt_principal_currency_by_owner():
    cash(line_code="DEBT_PRINCIPAL", owner_spec="rates", currency="USD")
    cash(line_code="DEBT_PRINCIPAL", owner_spec="fx", currency="EUR")
    with pytest.raises(ValidationError):
        cash(line_code="DEBT_PRINCIPAL", owner_spec="rates", currency="EUR")
    with pytest.raises(ValidationError):
        cash(line_code="DEBT_DRAWDOWN", owner_spec="rates", currency="EUR")


def test_acquisition_payment_is_rates_usd_only():
    cash(line_code="ACQUISITION_PAYMENT", owner_spec="rates", currency="USD")
    with pytest.raises(ValidationError, match="must be in USD"):
        cash(line_code="ACQUISITION_PAYMENT", owner_spec="rates", currency="EUR")
    with pytest.raises(ValidationError, match="may not emit"):
        cash(line_code="ACQUISITION_PAYMENT", owner_spec="fx", currency="USD")


def test_usd_line_fx_rate_must_be_one():
    cash(fx_rate_base=Decimal("1.0"), fx_rate_scenario=Decimal("1"))
    with pytest.raises(ValidationError, match="must be 1.0"):
        cash(fx_rate_base=Decimal("1.1"))
    cash(currency="EUR", fx_rate_base=Decimal("1.08"))  # non-USD lines carry any positive rate
    with pytest.raises(ValidationError):
        cash(currency="EUR", fx_rate_base=Decimal("0"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1234.5, "1234.50"),
        (0.1 + 0.2, "0.30"),  # binary noise does not leak in
        (-1_000_000.004, "-1000000.00"),
        (2.675, "2.68"),  # repr is "2.675", so it rounds like the decimal value
        (0.125, "0.12"),  # half-even
        (0.135, "0.14"),
        (1e9 / 3, "333333333.33"),
        (12, "12.00"),
        (Decimal("7.5"), "7.50"),
    ],
)
def test_round_to_cents(value, expected):
    result = round_to_cents(value)
    assert isinstance(result, Decimal) and result == Decimal(expected)
    assert str(result) == expected


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True])
def test_round_to_cents_rejects(bad):
    with pytest.raises((ValueError, TypeError)):
        round_to_cents(bad)


def test_float_amounts_are_rounded_to_cents():
    line = cash(
        currency="EUR",
        amount=-1_234_567.891,
        fx_rate_base=1.0834,
        amount_usd_base=-1_337_530.854,
        amount_usd_scenario=0.1 + 0.2,
    )
    assert line.amount == Decimal("-1234567.89")
    assert line.amount_usd_base == Decimal("-1337530.85")
    assert line.amount_usd_scenario == Decimal("0.30")
    assert line.fx_rate_base == Decimal("1.0834")  # rates are not rounded
    assert all(v.as_tuple().exponent == -2 for v in (line.amount, line.amount_usd_base))


def test_float_round_trip_matches_round_to_cents():
    x = 98_765.4321 * 3.7
    assert cash(amount=x).amount == round_to_cents(x)


def test_exact_amounts_must_already_be_cents():
    assert cash(amount=Decimal("10.5")).amount == Decimal("10.50")
    assert cash(amount="-3.25").amount == Decimal("-3.25")
    assert cash(amount=5).amount == Decimal("5.00")
    with pytest.raises(ValidationError, match="whole cents"):
        cash(amount=Decimal("10.005"))
    with pytest.raises(ValidationError, match="whole cents"):
        cash(amount="1.001")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), Decimal("NaN"), "abc", True, None])
def test_invalid_amounts_rejected(bad):
    with pytest.raises(ValidationError):
        cash(amount=bad)


def test_amount_assignment_is_rounded_too():
    line = cash()
    line.amount = 1.239
    assert line.amount == Decimal("1.24")


def test_cash_line_json_round_trip():
    line = cash(pass_type="base", event_ids=["evt_1"], amount=Decimal("-12.34"))
    assert CashLine.model_validate_json(line.model_dump_json()) == line


def volume(**overrides):
    data = {
        "trade": "ASIA_EUROPE",
        "month": date(2026, 11, 1),
        "teu": Decimal("250000"),
        "pass_type": "scenario",
        "label": "DERIVED",
    }
    data.update(overrides)
    return VolumeLine(**data)


def test_valid_volume_line():
    assert volume().teu == Decimal("250000")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trade", "TRANSPACIFIC"),
        ("month", date(2026, 11, 15)),
        ("teu", Decimal("-1")),
        ("pass_type", "both"),
        ("label", "GUESS"),
    ],
)
def test_volume_line_rejects(field, value):
    with pytest.raises(ValidationError):
        volume(**{field: value})
