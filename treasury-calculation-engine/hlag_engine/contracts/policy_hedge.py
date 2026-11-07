"""Policy hedge specification (spec 9A.3, 9B.4): passed from the FX module to the conversion layer.

One spec per hedged FX line. The conversion layer (9A) sizes the contract on that line's native amount
after date snapping (notional = ratio x |amount|, D18), takes the value date from the line, and sets the
contract rate K: the locked rate if given; otherwise spot on the start date (from history when the start
is on or before T0, from the pass's own path after T0, D45). A spec is state: identical in both passes.
"""

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import Currency, LineCode, ProvenanceLabel

CAD_RATIO_CAP = 0.80  # disclosed cap of the CAD programme (AR p. 215)


class HedgePolicy(StrEnum):
    CAD_OPERATING_COSTS = "cad_operating_costs"
    EUR_DEBT_PRINCIPAL = "eur_debt_principal"
    INR_BRL_OBLIGATIONS = "inr_brl_obligations"
    EUR_OPERATING_COSTS = "eur_operating_costs"
    EUR_DIVIDEND = "eur_dividend"


class PolicyHedgeSpec(ContractModel):
    policy: HedgePolicy
    currency: Currency
    hedged_sub_id: str = Field(min_length=1)
    hedged_line_code: LineCode
    ratio: float = Field(gt=0, le=1)
    start_date: date
    locked_rate: float | None = Field(default=None, gt=0)  # USD per unit, e.g. the 31 Dec 2025 INR/BRL rate
    label: ProvenanceLabel = ProvenanceLabel.ASSUMPTION

    @model_validator(mode="after")
    def _check(self) -> "PolicyHedgeSpec":
        if self.currency is Currency.USD:
            raise ValueError("a policy hedge needs a foreign currency")
        if self.policy is HedgePolicy.CAD_OPERATING_COSTS and self.ratio > CAD_RATIO_CAP:
            raise ValueError(f"CAD hedge ratio {self.ratio} is above the {CAD_RATIO_CAP:.0%} cap")
        return self
