"""Alert and its explanation object (spec 4.5-4.7)."""

from datetime import date

from pydantic import AwareDatetime, Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import AlertSeverity, AlertState, AlertTier, ProvenanceLabel, ScenarioStatus
from hlag_engine.contracts.playbook import PlaybookEntryId


class AlertInput(ContractModel):
    """One input behind an alert, with its label and as-of date."""

    name: str = Field(min_length=1)
    value: float | str | bool | None
    label: ProvenanceLabel
    as_of: date
    unit: str | None = None


class AlertExplanation(ContractModel):
    """The explanation object every alert carries (4.7)."""

    alert_id: str = Field(min_length=1)
    tier: AlertTier
    rule_id: str | None = None  # Tier A rule, or the desk exception rule
    status: ScenarioStatus | None = None  # Tier B status
    severity: AlertSeverity | None = None  # Tier A threshold level
    inputs: list[AlertInput] = Field(default_factory=list)
    formula: str
    threshold: float | str | None = None
    observed_value: float | str | None = None
    reason_code: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    playbook_refs: list[PlaybookEntryId] = Field(default_factory=list)  # PlaybookEntry.entry_id
    event_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    unconfirmed: bool = False  # fired on unconfirmed retrieved data (4.5)

    @model_validator(mode="after")
    def _rule_or_status(self) -> "AlertExplanation":
        if self.tier is AlertTier.MARKET and self.rule_id is None:
            raise ValueError("a market (Tier A) alert needs a rule_id")
        if self.tier is AlertTier.SCENARIO and self.status is None:
            raise ValueError("a scenario (Tier B) alert needs a status")
        if self.rule_id is None and self.status is None:
            raise ValueError("an alert needs a rule_id or a status")
        return self


class Alert(ContractModel):
    """An alert with its lifecycle state (4.7)."""

    explanation: AlertExplanation
    state: AlertState = AlertState.OPEN
    opened_at: AwareDatetime
    acknowledged_by: str | None = None
    acknowledged_at: AwareDatetime | None = None
    cleared_at: AwareDatetime | None = None

    @property
    def alert_id(self) -> str:
        return self.explanation.alert_id

    @model_validator(mode="after")
    def _state_consistent(self) -> "Alert":
        acked = self.acknowledged_by is not None or self.acknowledged_at is not None
        if acked and (self.acknowledged_by is None or self.acknowledged_at is None):
            raise ValueError("acknowledged_by and acknowledged_at go together")
        if self.state is AlertState.OPEN and (acked or self.cleared_at is not None):
            raise ValueError("an open alert has no acknowledgement or clearing time")
        if self.state is AlertState.ACKNOWLEDGED:
            if not acked:
                raise ValueError("an acknowledged alert needs acknowledged_by (a named user)")
            if self.cleared_at is not None:
                raise ValueError("an acknowledged alert has no cleared_at")
        if self.state is AlertState.CLEARED and self.cleared_at is None:
            raise ValueError("a cleared alert needs cleared_at")
        for name in ("acknowledged_at", "cleared_at"):
            moment = getattr(self, name)
            if moment is not None and moment < self.opened_at:
                raise ValueError(f"{name} cannot be before opened_at")
        return self
