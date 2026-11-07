"""Run summary returned by run_scenario (spec 4.8, D65).

Units: amounts are USD millions (``*_usd_m``); ``*_pct`` fields are whole percents.
"""

import datetime as dt
from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import OwnerSpec, PresetId, ScenarioSource, ScenarioStatus, ShortfallTier


class ScenarioRef(ContractModel):
    name: str = Field(min_length=1)
    source: ScenarioSource
    preset_id: PresetId | None = None
    event_ids: list[str] = Field(default_factory=list)


class RunFlags(ContractModel):
    """Run-level flags (4.6). synthetic_register is always on because exposures are generated."""

    synthetic_register: bool = True
    proxy_based: bool = False
    uses_unconfirmed_data: bool = False
    includes_unapproved_adjustments: bool = False
    incomplete_data: bool = False


class Headroom(ContractModel):
    baseline_min_usd_m: float
    baseline_min_date: date
    scenario_min_usd_m: float
    scenario_min_date: date
    consumed_pct: float | None  # c in %, None when H_b* <= 0
    first_breach_date: date | None = None
    shortfall_tier: ShortfallTier


class Attribution(ContractModel):
    """Full-horizon attribution bars (4.3), USD m."""

    freight: float
    bunker: float
    rates: float
    fx: float
    total: float
    direct: float = 0.0  # direct cash flows (deviation from spec 4.3; decisions_pending.md)


class DeskExceptionKind(StrEnum):
    """What fired the desk exception (4.6, P80): a driver's gross adverse delta, a margin-eligible shortfall,
    or a stale or missing material series."""

    DRIVER = "driver"
    MARGIN = "margin"
    DATA = "data"


class DeskException(ContractModel):
    """A desk exception (4.6). kind defaults to driver, so the original four-field form still validates (P80)."""

    kind: DeskExceptionKind = DeskExceptionKind.DRIVER
    driver: OwnerSpec | None = None  # required when kind is driver
    gross_adverse_usd_m: float | None = None  # G_d (driver) or the shortfall amount (margin)
    pct_of_headroom: float | None = None
    date: dt.date | None = None  # when G_d is reached, or the first shortfall date
    series: list[str] = Field(default_factory=list)  # data: the stale or missing material series
    detail: str | None = None

    @model_validator(mode="after")
    def _by_kind(self) -> "DeskException":
        if self.kind is DeskExceptionKind.DRIVER:
            missing = [f for f in ("driver", "gross_adverse_usd_m", "pct_of_headroom", "date") if getattr(self, f) is None]
            if missing:
                raise ValueError(f"a driver desk exception needs {missing}")
        if self.kind is DeskExceptionKind.DATA and not self.series:
            raise ValueError("a data desk exception names the stale or missing series")
        return self


class RunSummary(ContractModel):
    run_id: str = Field(min_length=1)
    as_of: date
    scenario: ScenarioRef
    status: ScenarioStatus
    opportunity: bool  # set by the engine; the threshold is configuration (thresholds.yaml)
    flags: RunFlags
    headroom: Headroom
    attribution_usd_m: Attribution
    desk_exceptions: list[DeskException] = Field(default_factory=list)
    alert_ids: list[str] = Field(default_factory=list)

