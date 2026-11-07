"""RunRecord, BaselineChange, Recommendation, PlaybookEntry, WeeklyReport."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import BaselineChange, PlaybookEntry, Recommendation, RunRecord, WeeklyReport
from hlag_engine.contracts.enums import OwnerSpec, RecommendationStatus

NOW = datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)


# --- run record ----------------------------------------------------------------


def run_record(**overrides):
    data = {
        "run_id": "run_1",
        "created_at": NOW,
        "as_of": date(2026, 10, 31),
        "params": {"name": "Baseline", "start_date": date(2026, 10, 31), "source": "scheduled"},
        "profile_version": "2026.10",
        "profile_hash": "sha256:abc",
        "market_snapshot": {
            "WCI_SHA_RTM": {"value": "3150.0", "value_date": date(2026, 10, 30),
                            "days_since_print": 1, "confirmed": True},  # fmt: skip
        },
        "status": "LOW",
    }
    data.update(overrides)
    return RunRecord(**data)


def test_valid_run_record_defaults():
    r = run_record()
    assert r.flags.synthetic_register is True
    assert r.review is None and r.superseded_by is None


def test_run_record_rejects():
    with pytest.raises(ValidationError):
        run_record(status="GREEN")
    with pytest.raises(ValidationError):
        run_record(created_at=datetime(2026, 10, 31, 18, 0))  # needs a timezone
    with pytest.raises(ValidationError):
        run_record(market_snapshot={"X": {"value": 1, "value_date": date(2026, 1, 1),
                                          "days_since_print": -1, "confirmed": True}})  # fmt: skip
    with pytest.raises(ValidationError):
        run_record(params={"name": "x", "start_date": date(2026, 10, 31), "source": "preset",
                           "unapproved_adjustment_ids": ["adj_1"]})  # fmt: skip


def test_run_record_review():
    r = run_record(review={"reviewer": "treasurer", "decision": "escalated", "rationale": "r", "time": NOW})
    assert r.review.decision.value == "escalated"
    with pytest.raises(ValidationError):
        run_record(review={"reviewer": "t", "decision": "approved", "rationale": "r", "time": NOW})


def test_run_record_json_round_trip():
    r = run_record(alerts=[{
        "alert_id": "a1", "tier": "scenario", "status": "LOW", "formula": "c", "reason_code": "status",
    }])  # fmt: skip
    assert RunRecord.model_validate_json(r.model_dump_json()) == r


# --- baseline change --------------------------------------------------------------


def baseline_change(**overrides):
    data = {
        "date": date(2026, 11, 2),
        "previous_run_id": "run_prev",
        "current_run_id": "run_now",
        "total_usd_m": -10.0,
        "by_driver_usd_m": {"freight": 4.0, "bunker": -12.5, "rates": 0.3, "fx": -0.8},
        "other_usd_m": -1.0,
    }
    data.update(overrides)
    return BaselineChange(**data)


def test_baseline_change_parts_sum_to_total():
    assert baseline_change().total_usd_m == -10.0
    with pytest.raises(ValidationError, match="parts sum"):
        baseline_change(total_usd_m=-9.0)


def test_baseline_change_tolerance_is_one_cent():
    """USD 0.01 = 1e-8 in USD millions (4.3)."""
    baseline_change(total_usd_m=-10.0 + 0.9e-8)  # under a cent off: accepted
    with pytest.raises(ValidationError, match="parts sum"):
        baseline_change(total_usd_m=-10.0 + 2e-8)  # two cents off: rejected


def test_baseline_change_requires_all_drivers():
    with pytest.raises(ValidationError):
        baseline_change(by_driver_usd_m={"freight": 1.0, "bunker": 0, "rates": 0})


# --- recommendation --------------------------------------------------------------


def recommendation(**overrides):
    data = {
        "recommendation_id": "rec_1",
        "created_at": NOW,
        "trigger": {"kind": "alert", "id": "alert_desk_freight_001"},
        "lever": "hypothetical_hedge",
        "evidence_runs": {"pair_id": "pair_001", "run_ids": ["run_a", "run_b"]},
        "expected_effect": {"min_headroom_change_usd_m": 20.1, "driver": "bunker",
                            "driver_bar_change_usd_m": 20.1, "cost_favourable_usd_m": 18.4},  # fmt: skip
        "playbook_refs": ["bunker.oil_spike"],
        "risks_and_limits": {"not_covered": ["supplier credit risk"], "input_labels": ["MARKET", "ASSUMPTION"]},
    }
    data.update(overrides)
    return Recommendation(**data)


def test_valid_recommendation_defaults():
    r = recommendation()
    assert r.status is RecommendationStatus.PROPOSED
    assert r.verifier_result.value == "not_checked"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lever", "buy_oil"),
        ("trigger", {"kind": "email", "id": "x"}),
        ("evidence_runs", {"run_ids": []}),
        ("status", "executed"),
        ("verifier_result", "ok"),
        ("expected_effect", {"min_headroom_change_usd_m": 1, "driver": "liquidity", "driver_bar_change_usd_m": 1}),
    ],
)
def test_recommendation_rejects(field, value):
    with pytest.raises(ValidationError):
        recommendation(**{field: value})


@pytest.mark.parametrize("status", ["accepted", "rejected", "deferred"])
def test_decision_needs_decider_and_time(status):
    with pytest.raises(ValidationError, match="decider"):
        recommendation(status=status)
    recommendation(status=status, decider="treasurer", decided_at=NOW, rationale="ok")


def test_proposed_recommendation_has_no_decision():
    with pytest.raises(ValidationError):
        recommendation(decider="treasurer")


# --- playbook entry --------------------------------------------------------------


def playbook(**overrides):
    data = {
        "entry_id": "freight.sustained_fall",
        "trigger_type": "preset_type",
        "trigger": "demand_slump",
        "actions": ["Raise the liquidity buffer", "Press collections"],
        "review_owner": "Head of Treasury",
    }
    data.update(overrides)
    return PlaybookEntry(**data)


def test_playbook_entry():
    e = playbook()
    assert e.hedge_tool is None
    assert e.module is OwnerSpec.FREIGHT
    with pytest.raises(ValidationError):
        playbook(trigger_type="news")
    with pytest.raises(ValidationError):
        playbook(actions=[])


@pytest.mark.parametrize("entry_id", ["bunker.oil_spike", "rates.high", "fx.eur_rally_2"])
def test_playbook_entry_id_accepted(entry_id):
    assert playbook(entry_id=entry_id).entry_id == entry_id


@pytest.mark.parametrize(
    "entry_id",
    [
        "sustained_fall",  # no module
        "liquidity.buffer",  # not a module
        "freight.",  # no trigger
        "freight.Sustained_Fall",  # not snake_case
        "freight:sustained_fall",  # wrong separator
        "freight.sustained-fall",
        "freight.a.b",
        "",
    ],
)
def test_playbook_entry_id_rejected(entry_id):
    with pytest.raises(ValidationError):
        playbook(entry_id=entry_id)


def test_recommendation_playbook_refs_must_be_entry_ids():
    with pytest.raises(ValidationError):
        recommendation(playbook_refs=["bunker:oil_spike"])
    with pytest.raises(ValidationError):
        recommendation(playbook_refs=["Raise the liquidity buffer"])


# --- weekly report ----------------------------------------------------------------


def test_weekly_report_format():
    report = WeeklyReport.model_validate({
        "week_start": "2026-10-26",
        "week_end": "2026-11-01",
        "sections": [{"category": "regulatory", "items": [{"event_id": "evt_1", "text": "New rule"}]}],
    })  # fmt: skip
    assert report.sections[0].category.value == "regulatory"


def test_weekly_report_rejects():
    with pytest.raises(ValidationError):
        WeeklyReport.model_validate({"week_start": "2026-10-26", "week_end": "2026-11-01",
                                     "sections": [{"category": "sports", "items": []}]})  # fmt: skip
    with pytest.raises(ValidationError):
        WeeklyReport(week_start=date(2026, 11, 1), week_end=date(2026, 10, 26))
