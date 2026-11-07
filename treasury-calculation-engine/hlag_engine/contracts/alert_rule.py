"""Tier A alert rule (spec 4.5). Rules live in thresholds.yaml and in module alert_rules() (5.1).

Thresholds written "X or Y" in the spec (for example "10% or 2.5x vol") are one level with a
value and an optional vol_multiple; the level fires when either condition is met. Threshold
settings are ASSUMPTION values with the display tag PROPOSED ILLUSTRATIVE SETTINGS (2.4).
"""

from enum import StrEnum

from pydantic import Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import OwnerSpec


class AlertRuleType(StrEnum):
    PCT_MOVE = "pct_move"
    BP_MOVE = "bp_move"
    VOL_MULTIPLE = "vol_multiple"
    SPREAD_CHANGE = "spread_change"
    DIVERGENCE = "divergence"
    VOL_REGIME = "vol_regime"


class OnSevere(StrEnum):
    NONE = "none"
    REQUEST_AUTO_BUILD = "request_auto_build"


class ThresholdLevel(ContractModel):
    value: float | None = Field(default=None, gt=0)  # in the rule's unit; None if only a vol multiple
    vol_multiple: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one_condition(self) -> "ThresholdLevel":
        if self.value is None and self.vol_multiple is None:
            raise ValueError("a threshold level needs a value or a vol_multiple")
        return self


class AlertThresholds(ContractModel):
    warning: ThresholdLevel
    severe: ThresholdLevel


class AlertRule(ContractModel):
    rule_id: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
    module: OwnerSpec
    series: list[str] = Field(min_length=1, max_length=2)  # two for spreads and divergences
    type: AlertRuleType
    window: str = Field(min_length=1)  # "1d", "1w", "4w" or "<N> obs"
    unit: str = Field(min_length=1)  # pct, bp, usd_per_t, pp
    thresholds: AlertThresholds
    lookback: int | None = Field(default=None, ge=2)  # volatility lookback in observations
    on_severe: OnSevere = OnSevere.NONE
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> "AlertRule":
        uses_vol = any(t.vol_multiple is not None for t in (self.thresholds.warning, self.thresholds.severe))
        if uses_vol and self.lookback is None:
            raise ValueError("a rule with a vol_multiple needs a lookback")
        if self.type in (AlertRuleType.SPREAD_CHANGE, AlertRuleType.DIVERGENCE) and len(self.series) != 2:
            raise ValueError(f"a {self.type.value} rule needs two series")
        w, s = self.thresholds.warning, self.thresholds.severe
        if w.value is not None and s.value is not None and s.value < w.value:
            raise ValueError("severe threshold must not be below the warning threshold")
        return self
