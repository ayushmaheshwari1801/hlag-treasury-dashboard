"""Snapping (2.5) and pairing (2.1, D17)."""

from datetime import date
from decimal import Decimal

import pytest

from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import PassType
from hlag_engine.engine.pairing import pair_lines
from hlag_engine.engine.timeline import snap, snap_date

T0 = date(2026, 10, 31)


def line(d, amount, sub_id="freight:spot:AE:arr", label="ASSUMPTION", ccy="USD"):
    return CashLine(sub_id=sub_id, date=d, currency=ccy, amount=Decimal(amount), owner_spec="freight",
                    line_code="FREIGHT_REVENUE", label=label)


def test_days_1_to_91_keep_their_date_then_month_end():
    assert snap_date(date(2027, 1, 30), T0) == date(2027, 1, 30)  # day 91
    assert snap_date(date(2027, 1, 31), T0) == date(2027, 1, 31)  # day 92 is already a month end
    assert snap_date(date(2027, 2, 1), T0) == date(2027, 2, 28)
    assert snap_date(date(2027, 3, 15), T0) == date(2027, 3, 31)


def test_snap_merges_same_key_with_weakest_label_and_sets_pass():
    lines = [line(date(2027, 3, 2), "10.00", label="MARKET"), line(date(2027, 3, 20), "5.50"),
             line(date(2026, 12, 1), "1.00")]
    out = snap(lines, T0, PassType.SCENARIO)
    assert [(l.date, l.amount, l.label.value) for l in out] == [
        (date(2026, 12, 1), Decimal("1.00"), "ASSUMPTION"), (date(2027, 3, 31), Decimal("15.50"), "ASSUMPTION")]
    assert {l.pass_type for l in out} == {PassType.SCENARIO}


def test_full_outer_join_missing_side_is_zero():
    base = [line(date(2026, 11, 30), "100.00"), line(date(2026, 12, 31), "50.00", sub_id="freight:spot:AE:dep")]
    scen = [line(date(2026, 11, 30), "110.00"), line(date(2027, 1, 12), "20.00")]
    pairs = pair_lines(base, scen)
    assert len(pairs) == 3
    by = {(p.ref.sub_id, p.ref.date): p for p in pairs}
    assert by[("freight:spot:AE:arr", date(2026, 11, 30))].delta_native == Decimal("10.00")
    only_base = by[("freight:spot:AE:dep", date(2026, 12, 31))]
    assert only_base.scen is None and only_base.b == 0 and only_base.delta_native == Decimal("-50.00")
    only_scen = by[("freight:spot:AE:arr", date(2027, 1, 12))]
    assert only_scen.base is None and only_scen.delta_native == Decimal("20.00")


def test_duplicate_key_is_an_error():
    with pytest.raises(ValueError, match="duplicate"):
        pair_lines([line(date(2026, 11, 30), "1.00"), line(date(2026, 11, 30), "2.00")], [])
