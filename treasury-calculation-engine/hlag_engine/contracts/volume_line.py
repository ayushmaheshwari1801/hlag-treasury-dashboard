"""Volume line contract (spec 2.6, D15). Carries TEU from freight to bunker; never on the path."""

from datetime import date
from decimal import Decimal

from pydantic import Field, field_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import PassType, ProvenanceLabel, Trade


class VolumeLine(ContractModel):
    """TEU shipped on one trade in one month (all volume classes)."""

    trade: Trade
    month: date  # first day of the month shipped
    teu: Decimal = Field(ge=0)
    pass_type: PassType
    label: ProvenanceLabel

    @field_validator("month")
    @classmethod
    def _first_of_month(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("month must be the first day of a month")
        return value
