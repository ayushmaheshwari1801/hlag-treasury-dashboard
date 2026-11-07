from decimal import Decimal as D

import pytest

from treasury_sensitivity.config import Defaults
from treasury_sensitivity.materiality import ConfirmedTrigger, Thresholds, assess, worst
from treasury_sensitivity.models import Status

TH = Thresholds.from_defaults(Defaults.load())


def run(adverse, liquidity=D(1000), limit=None, comparable=True, triggers=(), available=True):
    return assess(th=TH, adverse_usd_m=None if adverse is None else D(adverse), impact_available=available,
                  comparable_to_liquidity=comparable, liquidity_usd_m=liquidity, limit_usd_m=limit, triggers=list(triggers))


@pytest.mark.parametrize("adverse,status,rule", [
    (0, Status.LOW, "MAT-L1"), (9.99, Status.LOW, "MAT-L1"), (10, Status.HIGH, "MAT-H1"), (29.99, Status.HIGH, "MAT-H1"),
    (30, Status.CRITICAL, "MAT-C2"), (500, Status.CRITICAL, "MAT-C2")])
def test_liquidity_boundaries(adverse, status, rule):
    a = run(adverse)
    assert (a.status, a.rule_id) == (status, rule)


def test_missing_liquidity_is_not_low():
    a = run(5, liquidity=None)
    assert a.status == Status.NOT_ASSESSABLE and "comparable available liquidity" in a.missing
    assert run(5, liquidity=D(0)).status == Status.NOT_ASSESSABLE


def test_missing_impact_is_not_low():
    assert run(None, available=False).status == Status.NOT_ASSESSABLE


def test_limit_utilisation_bands():
    big = D(10**9)                                    # isolate the limit test from the liquidity test
    assert run(0, liquidity=big, limit=D(100)).status == Status.LOW
    assert run(79, liquidity=big, limit=D(100)).rule_id == "MAT-L1"
    a = run(80, liquidity=big, limit=D(100))
    assert (a.status, a.rule_id) == (Status.HIGH, "MAT-H2")
    assert run(99, liquidity=big, limit=D(100)).status == Status.HIGH
    a = run(100, liquidity=big, limit=D(100))
    assert (a.status, a.rule_id) == (Status.CRITICAL, "MAT-C3")


def test_breached_test_beats_missing_data():
    assert run(50, liquidity=None, limit=D(60)).status == Status.HIGH          # 83% of limit, liquidity missing
    assert run(None, liquidity=None, available=False, triggers=[ConfirmedTrigger(kind="payment_shortfall", confirmed_by="A", evidence="x")]).status == Status.CRITICAL


def test_confirmed_trigger_needs_a_person_and_evidence():
    ok = ConfirmedTrigger(kind="hard_limit_breach", confirmed_by="Head of Treasury", evidence="Bank notice 12/10")
    assert run(0, triggers=[ok]).rule_id == "MAT-C1"
    assert run(0, triggers=[ConfirmedTrigger(kind="hard_limit_breach", confirmed_by=" ", evidence="x")]).status == Status.LOW
    assert run(0, triggers=[ConfirmedTrigger(kind="hard_limit_breach", confirmed_by="A", evidence="")]).status == Status.LOW


def test_non_cash_impact_needs_a_limit():
    a = run(5, comparable=False, limit=None)
    assert a.status == Status.NOT_ASSESSABLE and a.liquidity_ratio is None
    assert run(5, comparable=False, limit=D(100)).status == Status.LOW


def test_favourable_impact_has_zero_adverse():
    assert run(0).liquidity_ratio == 0.0


def test_worst_never_hides_missing_data():
    assert worst([Status.LOW, Status.NOT_ASSESSABLE]) == Status.NOT_ASSESSABLE
    assert worst([Status.LOW, Status.HIGH, Status.NOT_ASSESSABLE]) == Status.HIGH
    assert worst([Status.CRITICAL, Status.HIGH]) == Status.CRITICAL


def test_threshold_note_always_present():
    assert "NOT company policy" in run(0).thresholds_note
