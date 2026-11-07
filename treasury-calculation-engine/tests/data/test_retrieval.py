"""submit_market_value (4.8) and the human-only confirm-queue functions."""

from datetime import date, timedelta

import pytest

from hlag_engine.data import retrieval
from hlag_engine.data.retrieval import (
    SubmitStatus,
    confirm_queued_value,
    confirm_stored_value,
    correct_queued_value,
    reject_queued_value,
    submit_market_value,
)
from hlag_engine.data.store import StoreError
from tests.data.conftest import TODAY, market_row

URL = "https://example.com/drewry"
DAY = TODAY - timedelta(days=4)
PREV = DAY - timedelta(days=7)


def submit(store, series_id="WCI_SHA_RTM", value=2000.0, day=DAY, unit="USD/FEU", url=URL):
    return submit_market_value(store, series_id, value, day, unit, url)


def with_previous(store, series_id="WCI_SHA_RTM", value=2000.0):
    store.upsert_market_rows([market_row(series_id, PREV, value)])


def test_passing_value_stored_unconfirmed(store):
    with_previous(store)
    result = submit(store, value=2100.0, day=DAY.isoformat())
    assert result.status is SubmitStatus.STORED_UNCONFIRMED and result.days_since_print == 4 and not result.stale
    row = store.get_market_row("WCI_SHA_RTM", DAY)
    assert row.value == 2100.0 and not row.confirmed and row.label.value == "MARKET" and row.source_ref == URL
    assert store.list_audit(action="market_value_stored")
    result.model_dump_json()  # JSON-serialisable for the agent tool


@pytest.mark.parametrize(
    ("kwargs", "check"),
    [
        ({"value": None}, "missing_value"),
        ({"value": -5.0}, "non_positive_value"),
        ({"unit": "USD/TEU"}, "wrong_unit"),
        ({"series_id": "VLSFO_SIN", "unit": "USD/bbl", "value": 80.0}, "wrong_unit"),
    ],
)
def test_failed_check_goes_to_queue(store, kwargs, check):
    result = submit(store, **kwargs)
    assert result.status is SubmitStatus.QUEUED and check in result.failed_checks
    assert not result.major_move_alert
    item = store.get_queue_item(result.queue_id)
    assert item.state == "pending" and item.failed_checks == result.failed_checks
    assert store.get_market_row(kwargs.get("series_id", "WCI_SHA_RTM"), DAY) is None


def test_band_breach_queued_with_major_move_alert(store):
    with_previous(store, value=2000.0)
    result = submit(store, value=3700.0)  # +85% > WCI 80%
    assert result.status is SubmitStatus.QUEUED and result.failed_checks == ["outside_plausibility_band"]
    assert result.major_move_alert and store.get_queue_item(result.queue_id).major_move_alert


def test_within_band_stored(store):
    with_previous(store, value=2000.0)
    assert submit(store, value=3580.0).status is SubmitStatus.STORED_UNCONFIRMED  # +79%


def test_fuel_band_40(store):
    with_previous(store, "HSFO_SIN", 400.0)
    assert submit(store, "HSFO_SIN", 565.0, unit="USD/t").status is SubmitStatus.QUEUED  # +41.25%
    assert submit(store, "HSFO_SIN", 236.0, unit="USD/t").status is SubmitStatus.QUEUED  # -41%
    assert submit(store, "HSFO_SIN", 555.0, unit="USD/t").status is SubmitStatus.STORED_UNCONFIRMED  # +38.75%


def test_no_previous_print_skips_band(store):
    assert submit(store, value=99999.0).status is SubmitStatus.STORED_UNCONFIRMED


def test_conflict_and_unchanged(store):
    submit(store, value=2000.0)
    assert submit(store, value=2000.0).status is SubmitStatus.UNCHANGED
    result = submit(store, value=2050.0)
    assert result.status is SubmitStatus.QUEUED and result.failed_checks == ["conflicts_with_stored"]
    assert store.get_market_row("WCI_SHA_RTM", DAY).value == 2000.0


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"series_id": "WCI_XXX"}, "unknown_series"),
        ({"series_id": "SOFR", "unit": "%", "value": 3.9}, "not_agent_series"),
        ({"day": "2026/10/29"}, "invalid_date"),
        ({"day": TODAY + timedelta(days=1)}, "future_date"),
        ({"value": True}, "invalid_value"),
        ({"value": "2000"}, "invalid_value"),
        ({"value": float("nan")}, "invalid_value"),
        ({"url": ""}, "missing_source_url"),
    ],
)
def test_malformed_input_rejected(store, kwargs, error):
    result = submit(store, **kwargs)
    assert result.status is SubmitStatus.REJECTED and any(e.startswith(error) for e in result.errors)
    assert store.list_queue(state=None) == [] and store.get_series(kwargs.get("series_id", "WCI_SHA_RTM")) == []
    assert store.list_audit(action="market_value_rejected")


def test_stale_value_stored_and_flagged(store):
    result = submit(store, day=TODAY - timedelta(days=15))
    assert result.status is SubmitStatus.STORED_UNCONFIRMED and result.stale and result.days_since_print == 15


def test_confirm_queued_band_value(store):
    with_previous(store)
    qid = submit(store, value=3700.0).queue_id
    confirm_queued_value(store, qid, "analyst", "Red Sea move confirmed in two sources")
    row = store.get_market_row("WCI_SHA_RTM", DAY)
    assert row.value == 3700.0 and row.confirmed
    assert store.get_queue_item(qid).state == "confirmed" and store.get_queue_item(qid).resolved_by == "analyst"
    assert store.list_audit(action="market_value_confirmed")
    with pytest.raises(StoreError, match="already confirmed"):
        confirm_queued_value(store, qid, "analyst")


def test_wrong_unit_must_be_corrected(store):
    qid = submit(store, unit="USD/TEU", value=1000.0).queue_id
    with pytest.raises(StoreError, match="correct_queued_value"):
        confirm_queued_value(store, qid, "analyst")
    with pytest.raises(StoreError, match="fails"):
        correct_queued_value(store, qid, "analyst", -1.0)
    correct_queued_value(store, qid, "analyst", 2000.0, "converted TEU to FEU")
    row = store.get_market_row("WCI_SHA_RTM", DAY)
    assert row.value == 2000.0 and row.unit == "USD/FEU" and row.confirmed
    item = store.get_queue_item(qid)
    assert item.state == "corrected" and item.resolved_value == 2000.0


def test_reject_queued_value(store):
    qid = submit(store, value=None).queue_id
    reject_queued_value(store, qid, "analyst", "no such print")
    assert store.get_queue_item(qid).state == "rejected"
    assert store.get_market_row("WCI_SHA_RTM", DAY) is None
    with pytest.raises(StoreError):
        reject_queued_value(store, 999, "analyst", "x")


def test_confirm_stored_value(store):
    submit(store)
    confirm_stored_value(store, "WCI_SHA_RTM", DAY, "analyst")
    assert store.get_market_row("WCI_SHA_RTM", DAY).confirmed
    with pytest.raises(StoreError):
        confirm_stored_value(store, "WCI_SHA_RTM", DAY, "analyst")
    with pytest.raises(StoreError):
        confirm_stored_value(store, "WCI_SHA_RTM", PREV, "analyst")


def test_human_only_functions_listed():
    for name in retrieval.HUMAN_ONLY_FUNCTIONS:
        assert callable(getattr(retrieval, name))
    assert "submit_market_value" not in retrieval.HUMAN_ONLY_FUNCTIONS
