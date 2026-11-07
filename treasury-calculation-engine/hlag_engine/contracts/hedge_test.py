"""Hedge test contract (spec 2.7, 5.3). A state field: applied identically in both passes (D20)."""

from datetime import date

from pydantic import Field

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import HedgeInstrument, HedgeModule


class HedgeTest(ContractModel):
    """A hypothetical hedge to test.

    Units: coverage_pct is a whole percent (75 = 75% of the target). Each module decides
    which targets and instruments it accepts (checked in the module, not here).
    """

    module: HedgeModule
    instrument: HedgeInstrument
    target: str = Field(min_length=1)  # e.g. fuel_all, floating_debt_usd, eur_operating_costs
    coverage_pct: float = Field(ge=0, le=100)
    tenor_months: int = Field(ge=1)
    strike_rule: str = Field(default="spot_at_T0", min_length=1)
    start_date: date
