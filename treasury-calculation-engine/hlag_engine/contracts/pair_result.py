"""Pair result returned by run_pair and test_hedge (spec 4.1, 4.8, 5.3).

Units: amounts are USD millions. difference = run_b minus run_a.
"""

from datetime import date
from typing import Literal

from pydantic import Field

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import LineCode
from hlag_engine.contracts.run_summary import Attribution, RunSummary


class PairDifference(ContractModel):
    min_headroom_usd_m: float
    attribution_usd_m: Attribution
    line_totals_usd_m: dict[LineCode, float] = Field(default_factory=dict)


class DatedAmount(ContractModel):
    date: date
    usd_m: float


class MarginTest(ContractModel):
    passed: bool
    lowest_margin_eligible_balance_usd_m: float


class HedgeMetrics(ContractModel):
    """Added by test_hedge (4.8)."""

    saving_adverse_usd_m: float  # improvement in minimum headroom under the given scenario
    cost_favourable_usd_m: float  # reduction in minimum headroom under the mirrored scenario
    settlement_cash: list[DatedAmount] = Field(default_factory=list)
    margin_cash: list[DatedAmount] = Field(default_factory=list)
    margin_test: MarginTest


class PairResult(ContractModel):
    pair_id: str = Field(min_length=1)
    run_a: RunSummary
    run_b: RunSummary  # with test_hedge, run_b holds the hedge
    difference: PairDifference
    label: Literal["state comparison"] = "state comparison"
    hedge_metrics: HedgeMetrics | None = None  # test_hedge only
