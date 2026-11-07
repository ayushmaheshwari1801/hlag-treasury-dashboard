from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import Alert, AlertExplanation
from hlag_engine.contracts.enums import AlertState

OPENED = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)


def explanation(**overrides):
    data = {
        "alert_id": "alert_mkt_wci_001",
        "tier": "market",
        "rule_id": "freight.route_move",
        "severity": "severe",
        "inputs": [
            {"name": "WCI_SHA_RTM", "value": 3150.0, "label": "MARKET", "as_of": date(2026, 10, 30)}
        ],
        "formula": "pct change over 1 week",
        "threshold": 20.0,
        "observed_value": 23.4,
        "reason_code": "pct_move_severe",
    }
    data.update(overrides)
    return AlertExplanation(**data)


def alert(**overrides):
    data = {"explanation": explanation(), "opened_at": OPENED}
    data.update(overrides)
    return Alert(**data)


def test_valid_open_alert():
    a = alert()
    assert a.state is AlertState.OPEN and a.alert_id == "alert_mkt_wci_001"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tier", "tier_a"),
        ("severity", "critical"),
        ("status", "SEVERE"),
        ("reason_code", ""),
    ],
)
def test_explanation_rejects(field, value):
    with pytest.raises(ValidationError):
        explanation(**{field: value})


def test_input_label_must_be_valid():
    with pytest.raises(ValidationError):
        explanation(inputs=[{"name": "x", "value": 1, "label": "GUESS", "as_of": date(2026, 1, 1)}])


def test_tier_rules():
    with pytest.raises(ValidationError, match="rule_id"):
        explanation(rule_id=None)
    with pytest.raises(ValidationError, match="status"):
        explanation(tier="scenario", rule_id=None)
    explanation(tier="scenario", rule_id=None, status="HIGH")
    explanation(tier="desk_exception", rule_id="desk.gross_adverse", severity=None)
    with pytest.raises(ValidationError):
        explanation(tier="desk_exception", rule_id=None)


def test_acknowledged_needs_named_user():
    with pytest.raises(ValidationError, match="named user"):
        alert(state="acknowledged")
    a = alert(state="acknowledged", acknowledged_by="treasurer", acknowledged_at=OPENED + timedelta(hours=1))
    assert a.acknowledged_by == "treasurer"


def test_cleared_needs_time_and_may_follow_acknowledgement():
    with pytest.raises(ValidationError, match="cleared_at"):
        alert(state="cleared")
    alert(state="cleared", cleared_at=OPENED + timedelta(days=2))
    alert(
        state="cleared",
        acknowledged_by="treasurer",
        acknowledged_at=OPENED + timedelta(hours=1),
        cleared_at=OPENED + timedelta(days=2),
    )


def test_open_alert_cannot_carry_later_times():
    with pytest.raises(ValidationError):
        alert(cleared_at=OPENED + timedelta(days=1))


def test_times_cannot_precede_opening():
    with pytest.raises(ValidationError, match="before opened_at"):
        alert(state="cleared", cleared_at=OPENED - timedelta(minutes=1))


def test_playbook_refs_are_entry_ids():
    e = explanation(playbook_refs=["freight.spike", "fx.eur_rally"])
    assert e.playbook_refs == ["freight.spike", "fx.eur_rally"]
    with pytest.raises(ValidationError):
        explanation(playbook_refs=["Plan deployment of the surplus"])
    with pytest.raises(ValidationError):
        explanation(playbook_refs=["engine.spike"])


def test_unknown_state_rejected():
    with pytest.raises(ValidationError):
        alert(state="closed")


def test_alert_json_round_trip():
    a = alert(state="acknowledged", acknowledged_by="t", acknowledged_at=OPENED)
    assert Alert.model_validate_json(a.model_dump_json()) == a
