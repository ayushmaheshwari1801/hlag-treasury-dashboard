"""AutoBuildRequest (spec 4.5): emitted by a severe Tier A alert whose rule has on_severe = request_auto_build.
The scenario builder agent (step 7) turns it into an auto-built scenario. At most one request per rule per day."""

from datetime import date

from pydantic import Field

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import AlertSeverity


class AutoBuildRequest(ContractModel):
    rule_id: str = Field(min_length=1)
    series: list[str] = Field(min_length=1)
    move: float  # observed value in the rule's unit
    unit: str = Field(min_length=1)
    severity: AlertSeverity = AlertSeverity.SEVERE
    observed_on: date
    alert_id: str = Field(min_length=1)
    event_ids: list[str] = Field(default_factory=list)
