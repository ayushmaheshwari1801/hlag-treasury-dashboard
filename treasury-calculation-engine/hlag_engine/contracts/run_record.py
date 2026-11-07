"""Run record (spec 4.7). One JSON document per run, enough to replay it.

``lines`` and ``results`` stay loosely typed: their inner shape is defined by the engine
(step 5). Cash lines themselves live in the cash_lines table; ``lines`` references them.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, Field

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.alert import AlertExplanation
from hlag_engine.contracts.enums import ProvenanceLabel, ReviewDecision, ScenarioStatus
from hlag_engine.contracts.run_summary import RunFlags
from hlag_engine.contracts.scenario import ScenarioParams


class MarketSnapshotEntry(ContractModel):
    """One series at T0, as used by the run (replay reads these, never the live store)."""

    value: Decimal
    value_date: date
    days_since_print: int = Field(ge=0)
    confirmed: bool
    unit: str | None = None  # P71: enough for replay to rebuild the snapshot exactly
    label: ProvenanceLabel = ProvenanceLabel.MARKET
    filled: bool = False
    stale: bool = False


class RunReview(ContractModel):
    reviewer: str = Field(min_length=1)
    decision: ReviewDecision
    rationale: str
    time: AwareDatetime


class RunRecord(ContractModel):
    run_id: str = Field(min_length=1)
    created_at: AwareDatetime
    as_of: date
    params: ScenarioParams  # includes source, preset_id, event_ids and overrides
    profile_version: str = Field(min_length=1)
    profile_hash: str = Field(min_length=1)
    adjustment_ids: list[str] = Field(default_factory=list)  # approved, plus any unapproved
    adjustments: list[Adjustment] = Field(default_factory=list)  # the full records as used (P71)
    market_snapshot: dict[str, MarketSnapshotEntry] = Field(default_factory=dict)
    # Values the run derived from market history (Brent beta and fill inputs, pre-T0 policy hedge contract
    # rates, volatility and change figures); replay uses these instead of history (P71).
    derived_inputs: dict[str, Any] = Field(default_factory=dict)
    registers_hash: str | None = None
    lines: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, Any] = Field(default_factory=dict)
    status: ScenarioStatus
    flags: RunFlags = Field(default_factory=RunFlags)
    alerts: list[AlertExplanation] = Field(default_factory=list)
    pair_id: str | None = None
    review: RunReview | None = None
    superseded_by: str | None = None
