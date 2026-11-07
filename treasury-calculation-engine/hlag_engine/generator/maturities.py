"""The single debt maturity schedule (spec 3.5, 3.7, 8.3, D1, D29, D44).

Built once; the generator assigns the USD share to rates and the EUR share to FX, so the
two modules cannot drift apart.

- 12-month amount = current financial debt and leases (USD 1,852m) - current lease
  liabilities (D1). If current lease liabilities are not entered, the working default is the
  annual lease repayments, labelled ASSUMPTION (3.7, 13.2).
- 1/12 of it on the last day of each month from the month after the disclosure date for 12
  months; later horizon months continue at the same monthly amount (D29, P18). Slices on or
  before T0 are absorbed by the roll-forward and dropped.
- Each slice is split pro rata into floating and fixed debt (D44) and into USD and EUR, from
  the EUR-denominated disclosures: EUR share = floating share x EUR share of floating. The
  EUR 300m sustainability-linked bond is a bullet bond maturing after the horizon, so it is
  excluded from the slices: the fixed part of each slice comes from the other (USD) fixed
  debt only (P49).
"""

from dataclasses import dataclass
from datetime import date

from hlag_engine.contracts.enums import ProvenanceLabel
from hlag_engine.generator.calendar import add_months, month_end
from hlag_engine.generator.inputs import Inputs
from hlag_engine.company_profile.schema import MissingProfileValue

CURRENT_DEBT_KEY = "debt.current_financial_debt_and_leases_usd"


@dataclass(frozen=True)
class MaturitySlice:
    date: date
    total_usd: float
    usd_part_usd: float  # USD-denominated debt maturing (rates)
    eur_part_usd: float  # EUR-denominated debt maturing, valued in USD at disclosure (fx)
    floating_share: float  # share of the slice taken from floating debt (D44)


@dataclass(frozen=True)
class MaturitySchedule:
    annual_usd: float
    monthly_usd: float
    floating_share: float
    eur_share: float
    label: ProvenanceLabel
    lease_basis: str
    slices: tuple[MaturitySlice, ...]

    @property
    def total_in_horizon_usd(self) -> float:
        return sum(s.total_usd for s in self.slices)


def build_maturity_schedule(inputs: Inputs, horizon_end: date) -> MaturitySchedule:
    current = inputs.get(CURRENT_DEBT_KEY)
    disclosed_on = inputs.ctx.profile.entry(CURRENT_DEBT_KEY).as_of
    assert disclosed_on is not None, "current debt needs its as_of date"
    try:
        lease = inputs.get("debt.current_lease_liabilities_usd")
        label, basis = ProvenanceLabel.DERIVED, "current lease liabilities"
    except MissingProfileValue:
        lease = inputs.get("liquidity.annual_lease_repayments_usd")
        label, basis = ProvenanceLabel.ASSUMPTION, "annual lease repayments (working default, 13.2)"

    annual = current - lease
    monthly = annual / 12

    variable = inputs.get("rates.variable_rate_debt_eur")
    total = inputs.get("rates.financial_debt_total_eur")
    usd_split = inputs.get("rates.floating_split_usd")
    floating_share = variable / total
    eur_share = floating_share * (1 - usd_split)  # the bullet bond is not in the slices (P49)

    slices = []
    month = 1
    while True:
        when = month_end(add_months(disclosed_on, month))
        if when > horizon_end:
            break
        if when > inputs.as_of:
            slices.append(
                MaturitySlice(
                    date=when,
                    total_usd=monthly,
                    usd_part_usd=monthly * (1 - eur_share),
                    eur_part_usd=monthly * eur_share,
                    floating_share=floating_share,
                )
            )
        month += 1
    return MaturitySchedule(
        annual_usd=annual,
        monthly_usd=monthly,
        floating_share=floating_share,
        eur_share=eur_share,
        label=ProvenanceLabel.weakest(label, ProvenanceLabel.ASSUMPTION),  # split is ASSUMPTION
        lease_basis=basis,
        slices=tuple(slices),
    )
