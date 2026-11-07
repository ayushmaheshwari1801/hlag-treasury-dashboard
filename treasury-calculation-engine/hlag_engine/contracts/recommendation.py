"""Recommendation record (spec 10.4). A recommendation never executes anything."""

from pydantic import AwareDatetime, Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import (
    OwnerSpec,
    ProvenanceLabel,
    RecommendationLever,
    RecommendationStatus,
    RecommendationTriggerKind,
    VerifierResult,
)
from hlag_engine.contracts.playbook import PlaybookEntryId


class RecommendationTrigger(ContractModel):
    kind: RecommendationTriggerKind  # alert, run or event
    id: str = Field(min_length=1)


class EvidenceRuns(ContractModel):
    """The run_pair or test_hedge comparison showing the effect."""

    pair_id: str | None = None
    run_ids: list[str] = Field(min_length=1)


class ExpectedEffect(ContractModel):
    """Taken from the evidence runs. Amounts in USD millions."""

    min_headroom_change_usd_m: float
    driver: OwnerSpec
    driver_bar_change_usd_m: float
    cost_favourable_usd_m: float | None = None


class RisksAndLimits(ContractModel):
    not_covered: list[str] = Field(default_factory=list)
    input_labels: list[ProvenanceLabel] = Field(default_factory=list)


class Recommendation(ContractModel):
    recommendation_id: str = Field(min_length=1)
    created_at: AwareDatetime
    trigger: RecommendationTrigger
    lever: RecommendationLever
    evidence_runs: EvidenceRuns
    expected_effect: ExpectedEffect
    playbook_refs: list[PlaybookEntryId] = Field(default_factory=list)  # PlaybookEntry.entry_id
    risks_and_limits: RisksAndLimits = Field(default_factory=RisksAndLimits)
    verifier_result: VerifierResult = VerifierResult.NOT_CHECKED
    verifier_note: str | None = None
    status: RecommendationStatus = RecommendationStatus.PROPOSED
    decider: str | None = None
    decided_at: AwareDatetime | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def _decision_consistent(self) -> "Recommendation":
        decided = self.decider is not None or self.decided_at is not None
        if self.status is RecommendationStatus.PROPOSED and decided:
            raise ValueError("a proposed recommendation has no decider or decided_at yet")
        if self.status is not RecommendationStatus.PROPOSED and (
            self.decider is None or self.decided_at is None
        ):
            raise ValueError(f"a {self.status.value} recommendation needs decider and decided_at")
        return self
