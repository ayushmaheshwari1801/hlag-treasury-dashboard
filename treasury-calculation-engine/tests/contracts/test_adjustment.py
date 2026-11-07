from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import Adjustment
from hlag_engine.contracts.enums import AdjustmentStatus

DECIDED = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)


def adjustment(**overrides):
    data = {
        "adjustment_id": "adj_001",
        "param_key": "bunker.scrubber_share",
        "old_value": 0.15,
        "new_value": 0.05,
        "step": "large",
        "effective_date": date(2027, 3, 1),
        "review_date": date(2027, 8, 28),
        "event_ids": ["evt_reg_1"],
        "justification": "Regulation restricts HSFO use with scrubbers; fleet register checked.",
        "confidence": 0.8,
        "proposed_by": "adjustment_agent/claude-sonnet-5-5",
    }
    data.update(overrides)
    return Adjustment(**data)


def test_valid_proposal_defaults():
    a = adjustment()
    assert a.status is AdjustmentStatus.PROPOSED
    assert a.verifier_result.value == "not_checked"
    assert a.review_flag is False


def test_direct_value_has_no_step():
    assert adjustment(step=None).step is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("step", "huge"),
        ("status", "pending"),
        ("verifier_result", "maybe"),
        ("confidence", 1.01),
        ("confidence", -0.5),
        ("proposed_by", ""),
    ],
)
def test_rejected_values(field, value):
    with pytest.raises(ValidationError):
        adjustment(**{field: value})


def test_review_date_must_follow_effective_date():
    with pytest.raises(ValidationError, match="review_date"):
        adjustment(review_date=date(2027, 3, 1))


def test_approved_needs_approver_and_time():
    with pytest.raises(ValidationError, match="approver"):
        adjustment(status="approved")
    a = adjustment(status="approved", approver="treasurer@example.com", decided_at=DECIDED)
    assert a.approver == "treasurer@example.com"


def test_proposed_cannot_carry_decision():
    with pytest.raises(ValidationError):
        adjustment(approver="someone", decided_at=DECIDED)


def test_review_flag_only_on_approved():
    with pytest.raises(ValidationError, match="review_flag"):
        adjustment(review_flag=True)
    adjustment(status="approved", approver="a", decided_at=DECIDED, review_flag=True)


def test_withdrawn_status_allowed():
    assert adjustment(status="withdrawn").status is AdjustmentStatus.WITHDRAWN


def test_is_active_on_window():
    a = adjustment(status="approved", approver="a", decided_at=DECIDED)
    assert not a.is_active_on(date(2027, 2, 28))
    assert a.is_active_on(date(2027, 3, 1))
    assert a.is_active_on(date(2027, 8, 27))
    assert not a.is_active_on(date(2027, 8, 28))
    assert not adjustment().is_active_on(date(2027, 4, 1))  # proposed never applies


def test_adjustment_json_round_trip():
    a = adjustment(status="approved", approver="a", decided_at=DECIDED)
    assert Adjustment.model_validate_json(a.model_dump_json()) == a
