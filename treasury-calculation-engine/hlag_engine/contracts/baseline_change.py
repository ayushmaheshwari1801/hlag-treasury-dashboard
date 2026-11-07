"""Baseline change record (spec 4.10, D71). Amounts in USD millions."""

import math
from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from hlag_engine.contracts._base import ContractModel

# Same tolerance as attribution (4.3): USD 0.01, written in USD millions.
SUM_TOLERANCE_USD_M = 1e-8


class DriverChange(ContractModel):
    freight: float
    bunker: float
    rates: float
    fx: float


class BaselineChangeWindow(StrEnum):
    """daily: against the previous scheduled baseline; weekly: against the latest scheduled baseline at least
    7 days earlier, the one the brief and the dashboard headline use (D67, P86)."""

    DAILY = "daily"
    WEEKLY = "weekly"


class BaselineChange(ContractModel):
    """Change in 12-month minimum headroom since a previous scheduled baseline, by driver."""

    window: BaselineChangeWindow = BaselineChangeWindow.DAILY
    date: date
    previous_run_id: str = Field(min_length=1)
    current_run_id: str = Field(min_length=1)
    total_usd_m: float
    by_driver_usd_m: DriverChange
    other_usd_m: float  # time roll, new or expired adjustments, profile updates

    @model_validator(mode="after")
    def _parts_sum_to_total(self) -> "BaselineChange":
        d = self.by_driver_usd_m
        parts = d.freight + d.bunker + d.rates + d.fx + self.other_usd_m
        if not math.isclose(parts, self.total_usd_m, rel_tol=0, abs_tol=SUM_TOLERANCE_USD_M):
            raise ValueError(f"parts sum to {parts}, not total_usd_m {self.total_usd_m}")
        return self
