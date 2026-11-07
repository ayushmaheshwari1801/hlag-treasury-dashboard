"""Agent-retrieved market values (spec 3.6, 4.8, D8, D31, D32), P25, P26.

submit_market_value is the data retrieval agent's only way to write market data; the agent
itself is step 7. It runs the shared checks, then:

- stored_unconfirmed: every check passes; stored at once with confirmed = false (D8).
- queued: a check failed (missing, non-positive, wrong unit, outside the plausibility band,
  or a different value already stored for that date); the value goes to confirm_queue and is
  not used until a person confirms or corrects it. A band failure sets major_move_alert, which
  the dashboard shows as "possible major move, pending confirmation" (D31).
- rejected: malformed input that cannot be queued (unknown or non-agent series, bad or future
  date, a value that is not a number, no source URL). Nothing is stored.
- unchanged: the same value is already stored for that date.

Staleness is reported (days_since_print, stale) but is not a reason to queue: an older value
is still a valid historical print (P26).

The confirm/correct/reject functions below are dashboard functions for people only; they MUST
NOT be exposed as agent tools (4.8).
"""

import math
import datetime as dt
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hlag_engine.contracts.enums import ProvenanceLabel
from hlag_engine.data.checks import (
    CONFLICTS_WITH_STORED,
    MISSING_VALUE,
    NON_POSITIVE_VALUE,
    OUTSIDE_PLAUSIBILITY_BAND,
    WRONG_UNIT,
    days_since_print,
    is_stale,
    outside_band,
    value_checks,
)
from hlag_engine.data.series import SERIES, MarketDataSettings, SourceKind, load_market_settings, spec_for
from hlag_engine.data.store import MarketRow, QueueItem, Store, StoreError

AGENT_SOURCE = "agent retrieval"
HUMAN_ONLY_FUNCTIONS = ("confirm_queued_value", "correct_queued_value", "reject_queued_value", "confirm_stored_value")


class SubmitStatus(StrEnum):
    STORED_UNCONFIRMED = "stored_unconfirmed"
    QUEUED = "queued"
    REJECTED = "rejected"
    UNCHANGED = "unchanged"


class SubmitMarketValueResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SubmitStatus
    series_id: str
    date: dt.date | None = None
    errors: list[str] = Field(default_factory=list)  # named errors when rejected
    failed_checks: list[str] = Field(default_factory=list)  # when queued
    queue_id: int | None = None
    major_move_alert: bool = False
    days_since_print: int | None = None
    stale: bool | None = None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value) if len(value) == 10 else None
        except ValueError:
            return None
    return None


def submit_market_value(
    store: Store,
    series_id: str,
    value: float | None,
    date: Any,
    unit: str,
    source_url: str,
    *,
    submitted_by: str = "data_retrieval_agent",
    today: Any = None,
    settings: MarketDataSettings | None = None,
) -> SubmitMarketValueResult:
    settings = settings or load_market_settings()
    today = today or store.now().date()
    errors: list[str] = []
    spec = SERIES.get(series_id)
    if spec is None:
        errors.append(f"unknown_series: {series_id!r}")
    elif spec.source_kind is not SourceKind.AGENT:
        errors.append(f"not_agent_series: {series_id} is loaded from an API, not by retrieval")
    day = _parse_date(date)
    if day is None:
        errors.append(f"invalid_date: {date!r} (expected YYYY-MM-DD)")
    elif day > today:
        errors.append(f"future_date: {day} is after {today}")
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value)
    ):
        errors.append(f"invalid_value: {value!r} is not a number")
    if not isinstance(source_url, str) or not source_url.startswith(("http://", "https://")):
        errors.append("missing_source_url: every retrieved value must cite an http(s) source_url")
    if errors:
        result = SubmitMarketValueResult(status=SubmitStatus.REJECTED, series_id=str(series_id), date=day, errors=errors)
        store.append_audit(submitted_by, "market_value_rejected", f"{series_id}:{date}", result.model_dump(mode="json"))
        return result

    assert spec is not None and day is not None
    numeric = None if value is None else float(value)
    failed = value_checks(spec, numeric, unit)
    if numeric is not None and numeric > 0:
        previous = store.latest_print_before(series_id, day)
        if outside_band(spec, settings, numeric, previous.value if previous else None):
            failed.append(OUTSIDE_PLAUSIBILITY_BAND)
    stored = store.get_market_row(series_id, day)
    if stored is not None and not stored.filled and numeric is not None:
        if stored.value != numeric:
            failed.append(CONFLICTS_WITH_STORED)
        elif not failed:
            return SubmitMarketValueResult(status=SubmitStatus.UNCHANGED, series_id=series_id, date=day)

    dsp = days_since_print(day, today)
    common = {"series_id": series_id, "date": day, "days_since_print": dsp, "stale": is_stale(spec, settings, dsp)}
    if failed:
        major = OUTSIDE_PLAUSIBILITY_BAND in failed
        queue_id = store.add_queue_item(
            series_id=series_id, on=day, value=numeric, unit=unit, source_url=source_url,
            submitted_by=submitted_by, failed_checks=failed, major_move_alert=major,
        )
        result = SubmitMarketValueResult(
            status=SubmitStatus.QUEUED, failed_checks=failed, queue_id=queue_id, major_move_alert=major, **common
        )
        store.append_audit(submitted_by, "market_value_queued", f"queue:{queue_id}", result.model_dump(mode="json"))
        return result

    store.upsert_market_rows([_row(series_id, day, numeric, source_url, store, confirmed=False)])  # type: ignore[arg-type]
    result = SubmitMarketValueResult(status=SubmitStatus.STORED_UNCONFIRMED, **common)
    store.append_audit(submitted_by, "market_value_stored", f"{series_id}:{day}", result.model_dump(mode="json"))
    return result


def _row(series_id: str, day: date, value: float, source_ref: str, store: Store, confirmed: bool) -> MarketRow:
    return MarketRow(
        series_id=series_id, date=day, value=value, unit=spec_for(series_id).unit, source=AGENT_SOURCE,
        label=ProvenanceLabel.MARKET, retrieved_at=store.now(), filled=False, days_since_print=0,
        confirmed=confirmed, source_ref=source_ref,
    )


def _pending(store: Store, queue_id: int) -> QueueItem:
    item = store.get_queue_item(queue_id)
    if item is None:
        raise StoreError(f"unknown queue item {queue_id}")
    if item.state != "pending":
        raise StoreError(f"queue item {queue_id} is already {item.state}")
    return item


# ---------------------------------------------------------------- human-only (dashboard), never agent tools


def confirm_queued_value(store: Store, queue_id: int, reviewer: str, note: str | None = None) -> None:
    """Accept a queued value as submitted (for a band or conflict failure that is a real move)."""
    item = _pending(store, queue_id)
    blocking = {MISSING_VALUE, WRONG_UNIT, NON_POSITIVE_VALUE} & set(item.failed_checks)
    if blocking:
        raise StoreError(f"queue item {queue_id} failed {sorted(blocking)}; use correct_queued_value instead")
    with store.transaction():
        store.upsert_market_rows([_row(item.series_id, item.date, item.value, item.source_url, store, confirmed=True)])  # type: ignore[arg-type]
        store.resolve_queue_item(queue_id, state="confirmed", resolved_by=reviewer, resolved_value=item.value, note=note)
        store.append_audit(reviewer, "market_value_confirmed", f"queue:{queue_id}",
                           {"series_id": item.series_id, "date": item.date, "value": item.value, "note": note})


def correct_queued_value(store: Store, queue_id: int, reviewer: str, value: float, note: str | None = None) -> None:
    """Store a corrected value, in the series' own unit, instead of the queued one."""
    item = _pending(store, queue_id)
    spec = spec_for(item.series_id)
    failed = value_checks(spec, value, spec.unit)
    if failed:
        raise StoreError(f"corrected value {value} fails {failed}")
    with store.transaction():
        store.upsert_market_rows([_row(item.series_id, item.date, float(value), item.source_url, store, confirmed=True)])
        store.resolve_queue_item(queue_id, state="corrected", resolved_by=reviewer, resolved_value=float(value), note=note)
        store.append_audit(reviewer, "market_value_corrected", f"queue:{queue_id}",
                           {"series_id": item.series_id, "date": item.date, "submitted": item.value,
                            "corrected": value, "note": note})


def reject_queued_value(store: Store, queue_id: int, reviewer: str, note: str) -> None:
    item = _pending(store, queue_id)
    with store.transaction():
        store.resolve_queue_item(queue_id, state="rejected", resolved_by=reviewer, resolved_value=None, note=note)
        store.append_audit(reviewer, "market_value_queue_rejected", f"queue:{queue_id}",
                           {"series_id": item.series_id, "date": item.date, "value": item.value, "note": note})


def confirm_stored_value(store: Store, series_id: str, on: date, reviewer: str) -> None:
    """Mark an unconfirmed retrieved value as confirmed."""
    row = store.get_market_row(series_id, on)
    if row is None or row.filled:
        raise StoreError(f"no retrieved value for {series_id} on {on}")
    if row.confirmed:
        raise StoreError(f"{series_id} on {on} is already confirmed")
    with store.transaction():
        store.set_market_confirmed(series_id, on)
        store.append_audit(reviewer, "market_value_confirmed", f"{series_id}:{on}", {"value": row.value})
