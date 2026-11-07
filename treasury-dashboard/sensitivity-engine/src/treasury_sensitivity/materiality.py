"""Deterministic status rules (spec s8). Text generation cannot reach this module.

Required tests: liquidity ratio (only when the impact is cash and comparable to liquidity) and,
when an approved limit is configured, limit utilisation. LOW needs every required test valid and
below warning level; a missing required test is NOT ASSESSABLE, never LOW. A confirmed trigger or
a breached test wins over missing data.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .config import Defaults
from .models import D, Status, StatusAssessment, StrictModel


@dataclass(frozen=True)
class Thresholds:
    version: str
    liquidity_high: Decimal
    liquidity_critical: Decimal
    limit_high: Decimal
    limit_critical: Decimal
    note: str

    @classmethod
    def from_defaults(cls, d: Defaults) -> "Thresholds":
        t = d["thresholds"]
        return cls(t["version"], D(t["liquidity_high"]), D(t["liquidity_critical"]),
                   D(t["limit_high"]), D(t["limit_critical"]), t["note"])


class ApprovedLimit(StrictModel):
    name: str
    amount: float
    currency: str = "USD"


class ConfirmedTrigger(StrictModel):
    """A human-confirmed critical condition. Needs a named confirmer and evidence to count."""
    kind: Literal["hard_limit_breach", "payment_shortfall"]
    confirmed_by: str
    evidence: str


def valid_triggers(triggers: list[ConfirmedTrigger]) -> list[ConfirmedTrigger]:
    return [t for t in triggers if t.confirmed_by.strip() and t.evidence.strip()]


def assess(*, th: Thresholds, adverse_usd_m: Decimal | None, impact_available: bool,
           comparable_to_liquidity: bool, liquidity_usd_m: Decimal | None,
           limit_usd_m: Decimal | None, triggers: list[ConfirmedTrigger],
           extra_missing: list[str] | None = None) -> StatusAssessment:
    trace: list[str] = []
    missing = list(extra_missing or [])
    if not impact_available or adverse_usd_m is None:
        missing.append("scenario impact could not be computed")
        adverse_usd_m = None

    ratio = util = None
    required: list[str] = []
    if comparable_to_liquidity:
        required.append("liquidity")
        if liquidity_usd_m is not None and liquidity_usd_m > 0 and adverse_usd_m is not None:
            ratio = adverse_usd_m / liquidity_usd_m
            trace.append(f"liquidity test: adverse impact / comparable liquidity = {float(ratio):.4%}")
        else:
            missing.append("comparable available liquidity")
    else:
        trace.append("liquidity test skipped: impact is not a cash amount comparable to liquidity")
    if limit_usd_m is not None:
        required.append("limit")
        if limit_usd_m > 0 and adverse_usd_m is not None:
            util = adverse_usd_m / limit_usd_m
            trace.append(f"limit test: compatible risk measure / approved limit = {float(util):.4%}")
        else:
            missing.append("valid approved limit")
    elif not comparable_to_liquidity:
        required.append("limit")
        missing.append("approved limit (earnings proxy has no comparable liquidity denominator)")

    def result(status, rule, extra=None):
        return StatusAssessment(status=status, rule_id=rule, rule_trace=trace + (extra or []),
                                threshold_version=th.version, thresholds_note=th.note,
                                liquidity_ratio=None if ratio is None else float(ratio),
                                limit_utilisation=None if util is None else float(util),
                                missing=missing)

    confirmed = valid_triggers(triggers)
    if confirmed:
        return result(Status.CRITICAL, "MAT-C1", [f"confirmed trigger: {t.kind} (by {t.confirmed_by})" for t in confirmed])
    if ratio is not None and ratio >= th.liquidity_critical:
        return result(Status.CRITICAL, "MAT-C2", [f"liquidity ratio >= {th.liquidity_critical:.0%}"])
    if util is not None and util >= th.limit_critical:
        return result(Status.CRITICAL, "MAT-C3", [f"limit utilisation >= {th.limit_critical:.0%} (computed; confirm before treating as a breach)"])
    if ratio is not None and ratio >= th.liquidity_high:
        return result(Status.HIGH, "MAT-H1", [f"liquidity ratio >= {th.liquidity_high:.0%} and < {th.liquidity_critical:.0%}"])
    if util is not None and util >= th.limit_high:
        return result(Status.HIGH, "MAT-H2", [f"limit utilisation >= {th.limit_high:.0%} and < {th.limit_critical:.0%}"])
    if missing or not required:
        return result(Status.NOT_ASSESSABLE, "MAT-N1", ["required input missing: no status inferred; request validation"])
    return result(Status.LOW, "MAT-L1", ["all required tests valid and below warning levels"])


def worst(statuses: list[Status]) -> Status:
    """Display order for a set of rows: CRITICAL > HIGH > NOT ASSESSABLE > LOW (never hide missing data as LOW)."""
    rank = {Status.CRITICAL: 3, Status.HIGH: 2, Status.NOT_ASSESSABLE: 1, Status.LOW: 0}
    return max(statuses, key=rank.__getitem__) if statuses else Status.NOT_ASSESSABLE
