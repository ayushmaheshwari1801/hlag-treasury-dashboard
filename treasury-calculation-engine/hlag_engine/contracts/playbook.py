"""Playbook entry (spec 5.1, module sections 6.12, 7.12, 8.12, 9B.11)."""

from typing import Annotated

from pydantic import Field, StringConstraints

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import OwnerSpec, PlaybookTriggerType

# Stable ID "<module>.<trigger>", e.g. "freight.sustained_fall". Alerts and recommendations
# refer to playbook entries by this ID (playbook_refs).
PLAYBOOK_ENTRY_ID_PATTERN = r"^(freight|bunker|rates|fx)\.[a-z0-9]+(_[a-z0-9]+)*$"
PlaybookEntryId = Annotated[str, StringConstraints(pattern=PLAYBOOK_ENTRY_ID_PATTERN)]


class PlaybookEntry(ContractModel):
    """Actions a module recommends for one trigger."""

    entry_id: PlaybookEntryId
    trigger_type: PlaybookTriggerType  # alert rule, status or preset type
    trigger: str = Field(min_length=1)  # e.g. a rule_id, "HIGH", or a preset ID
    actions: list[str] = Field(min_length=1)  # ordered short texts
    hedge_tool: str | None = None  # optional hedge target the actions link to
    review_owner: str = Field(min_length=1)

    @property
    def module(self) -> OwnerSpec:
        """The owning module, taken from entry_id."""
        return OwnerSpec(self.entry_id.split(".", 1)[0])
