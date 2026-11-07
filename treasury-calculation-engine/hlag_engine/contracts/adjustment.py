"""Adjustment record (spec 2.9, 3.2, 3.3, 10.3).

Catalogue checks (key allowed, event type listed, step vs direct value, hard limits,
max_review_days, event_ids given) belong to validate_adjustment in step 2, not here.
"""

from datetime import date
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import AdjustmentStatus, AdjustmentStep, VerifierResult

_DECIDED = {AdjustmentStatus.APPROVED, AdjustmentStatus.REJECTED}


class Adjustment(ContractModel):
    """An approved, expiring, news-driven change to what the model believes is true today."""

    adjustment_id: str = Field(min_length=1)
    param_key: str = Field(min_length=1)
    old_value: Any
    new_value: Any
    step: AdjustmentStep | None = None  # None for a direct value (quantitative news)
    effective_date: date
    review_date: date
    event_ids: list[str] = Field(default_factory=list)
    justification: str
    confidence: float = Field(ge=0, le=1)
    verifier_result: VerifierResult = VerifierResult.NOT_CHECKED
    verifier_note: str | None = None
    status: AdjustmentStatus = AdjustmentStatus.PROPOSED
    review_flag: bool = False  # set on approved adjustments whose event was updated (10.6)
    approver: str | None = None
    decided_at: AwareDatetime | None = None
    proposed_by: str = Field(min_length=1)  # agent name and model version

    @model_validator(mode="after")
    def _check(self) -> "Adjustment":
        if self.review_date <= self.effective_date:
            raise ValueError("review_date must be after effective_date")
        if self.review_flag and self.status is not AdjustmentStatus.APPROVED:
            raise ValueError("review_flag can be set only on approved adjustments")
        if self.status in _DECIDED and (self.approver is None or self.decided_at is None):
            raise ValueError(f"a {self.status.value} adjustment needs approver and decided_at")
        if self.status is AdjustmentStatus.PROPOSED and (
            self.approver is not None or self.decided_at is not None
        ):
            raise ValueError("a proposed adjustment has no approver or decided_at yet")
        return self

    def is_active_on(self, on: date) -> bool:
        """True when this adjustment changes values on that date in a normal run (2.9)."""
        return (
            self.status is AdjustmentStatus.APPROVED
            and self.effective_date <= on < self.review_date
        )
