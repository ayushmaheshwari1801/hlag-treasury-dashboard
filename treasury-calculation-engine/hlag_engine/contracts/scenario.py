"""Scenario parameter set (spec 2.7, D28).

Units used throughout this module:
- ``*_pct`` fields are whole percents: 40 means +40%, -15 means -15%.
- ``*_share`` fields are fractions from 0 to 1: 0.35 means 35%.
- ``*_bp`` fields are basis points.

Every field is tagged with a category (see contracts._base.FieldCategory):
- shock: reset to its default by zero_shock();
- state: identical in both passes, kept by zero_shock();
- meta: the common descriptive block, kept by zero_shock();
- block: a nested driver block whose own fields are tagged.

Only physical limits are enforced here (a price cannot fall by 100% or more, delays and
tenors are not negative, shares lie in 0-1). Policy ranges come from configuration (step 2).
"""

from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from hlag_engine.contracts._base import ContractModel, FieldCategory, field_category, tagged, tagged_factory
from hlag_engine.contracts.enums import (
    Currency,
    OwnerSpec,
    PresetId,
    RefinancingMode,
    ScenarioSource,
    Shape,
    Trade,
)
from hlag_engine.contracts.hedge_test import HedgeTest

SHOCK, STATE, META, BLOCK = (
    FieldCategory.SHOCK,
    FieldCategory.STATE,
    FieldCategory.META,
    FieldCategory.BLOCK,
)

PriceMovePct = Annotated[float, Field(gt=-100)]  # a price or rate index cannot fall 100% or more
VolumeMovePct = Annotated[float, Field(ge=-100)]  # volume can fall to zero, not below
DelayDays = Annotated[int, Field(ge=0)]

RATE_PATH_CURRENCIES = frozenset({Currency.USD, Currency.EUR})
SCOPE_CHECKED_SOURCES = frozenset({ScenarioSource.PRESET, ScenarioSource.SCHEDULED})


class HistoryReplay(ContractModel):
    """Past window to replay (6.6); the shocks are stored in rate_shock_path when built (D37)."""

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> "HistoryReplay":
        if self.end <= self.start:
            raise ValueError("history_replay end must be after start")
        return self


class WeeklyShock(ContractModel):
    """One point of an explicit freight rate_shock_path (D37). pct is a whole percent."""

    date: date
    pct: PriceMovePct


class RateStep(ContractModel):
    """One dated step in a rate path. Steps are cumulative (2.7)."""

    date: date
    step_bp: float


class FreightParams(ContractModel):
    """Freight block (2.7, 6.6). A trade missing from a dict means no shock on that trade."""

    rate_shock_pct: dict[Trade, PriceMovePct] = tagged_factory(SHOCK, dict)
    volume_shock_pct: dict[Trade, VolumeMovePct] = tagged_factory(SHOCK, dict)
    transit_delay_days: dict[Trade, DelayDays] = tagged_factory(SHOCK, dict)
    shape: Shape | None = tagged(META, None)
    history_replay: HistoryReplay | None = tagged(SHOCK, None)
    rate_shock_path: dict[Trade, list[WeeklyShock]] = tagged_factory(SHOCK, dict)


class BunkerParams(ContractModel):
    """Bunker block (2.7, 7.6)."""

    vlsfo_shock_pct: PriceMovePct = tagged(SHOCK, 0.0)
    hsfo_shock_pct: PriceMovePct = tagged(SHOCK, 0.0)
    consumption_uplift_pct: VolumeMovePct = tagged(SHOCK, 0.0)
    uplift_affected_share: float = tagged(SHOCK, 0.0, ge=0, le=1)
    shape: Shape | None = tagged(META, None)


class RatesParams(ContractModel):
    """Rates block (2.7, 8.6). zim_tranche_on is a state field."""

    rate_path: dict[Currency, list[RateStep]] = tagged_factory(SHOCK, dict)
    credit_spread_change_bp: float = tagged(SHOCK, 0.0)
    refinancing_mode: RefinancingMode = tagged(SHOCK, RefinancingMode.ROLLOVER)
    zim_tranche_on: bool = tagged(STATE, False)

    @field_validator("rate_path")
    @classmethod
    def _usd_eur_only(cls, value: dict[Currency, list[RateStep]]) -> dict[Currency, list[RateStep]]:
        bad = sorted(c.value for c in value if c not in RATE_PATH_CURRENCIES)
        if bad:
            raise ValueError(f"rate_path supports USD and EUR only, got {bad}")
        return value


class FxParams(ContractModel):
    """FX block (2.7, 9B.6). move_pct acts on USD per one foreign unit."""

    move_pct: dict[Currency, PriceMovePct] = tagged_factory(SHOCK, dict)
    shape: dict[Currency, Shape] = tagged_factory(META, dict)

    @field_validator("move_pct", "shape")
    @classmethod
    def _no_usd(cls, value: dict[Currency, Any]) -> dict[Currency, Any]:
        if Currency.USD in value:
            raise ValueError("USD cannot move against itself")
        return value


EFFECT_ID = r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,119}$"


class DirectCashFlow(ContractModel):
    """One dated USD cash amount stated directly in the scenario (deviation from spec 2.7; decisions_pending.md,
    "Direct cash flows"). amount_usd_m is signed USD million: cash in positive, cash out negative (2.3).

    effect_id names one economic effect (it may span several dated entries); overlap_key names what the effect
    represents, so the same effect is never counted twice (one overlap_key per effect_id and vice versa)."""

    effect_id: str = Field(pattern=EFFECT_ID)
    overlap_key: str = Field(pattern=EFFECT_ID)
    date: date
    amount_usd_m: float = Field(allow_inf_nan=False)
    label: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)
    event_ids: list[str] = Field(default_factory=list)

    @field_validator("amount_usd_m")
    @classmethod
    def _nonzero(cls, value: float) -> float:
        if value == 0:
            raise ValueError("a direct cash flow must be a nonzero amount")
        return value


class DirectParams(ContractModel):
    """Direct cash flows block. Shock field: the baseline pass carries none, so every amount is a scenario delta."""

    cash_flows: list[DirectCashFlow] = tagged_factory(SHOCK, list)

    @model_validator(mode="after")
    def _one_count_per_effect(self) -> "DirectParams":
        seen: set[tuple[str, date]] = set()
        overlap_of: dict[str, str] = {}
        effect_of: dict[str, str] = {}
        for flow in self.cash_flows:
            if (flow.effect_id, flow.date) in seen:
                raise ValueError(f"direct cash flow {flow.effect_id} has two entries on {flow.date}; combine them")
            seen.add((flow.effect_id, flow.date))
            if overlap_of.setdefault(flow.effect_id, flow.overlap_key) != flow.overlap_key:
                raise ValueError(f"effect {flow.effect_id} has more than one overlap_key")
            if effect_of.setdefault(flow.overlap_key, flow.effect_id) != flow.effect_id:
                raise ValueError(f"overlap_key {flow.overlap_key} is already counted by effect {effect_of[flow.overlap_key]}")
        return self


DRIVER_BLOCKS: dict[OwnerSpec, str] = {
    OwnerSpec.FREIGHT: "freight",
    OwnerSpec.BUNKER: "bunker",
    OwnerSpec.RATES: "rates",
    OwnerSpec.FX: "fx",
    OwnerSpec.DIRECT: "direct",
}


class ScenarioParams(ContractModel):
    """All drivers' parameters for one run, so a joint event is one object.

    See the module docstring for units and field categories.
    """

    # Common block (meta)
    name: str = tagged(META, min_length=1)
    start_date: date = tagged(META)
    duration_days: int | None = tagged(META, None, ge=1)  # None = persistent
    default_shape: Shape = tagged(META, Shape.STEP)
    ramp_or_revert_weeks: int | None = tagged(META, None, ge=0)
    source: ScenarioSource = tagged(META)
    event_ids: list[str] = tagged_factory(META, list)
    # With source custom or auto_built, preset_id means "based on this preset" (provenance only).
    preset_id: PresetId | None = tagged(META, None)

    # Driver blocks
    freight: FreightParams = tagged_factory(BLOCK, FreightParams)
    bunker: BunkerParams = tagged_factory(BLOCK, BunkerParams)
    rates: RatesParams = tagged_factory(BLOCK, RatesParams)
    fx: FxParams = tagged_factory(BLOCK, FxParams)
    direct: DirectParams = tagged_factory(BLOCK, DirectParams)

    # State
    hedge_tests: list[HedgeTest] = tagged_factory(STATE, list)
    overrides: dict[str, Any] = tagged_factory(STATE, dict)  # ranges checked against the profile
    unapproved_adjustment_ids: list[str] = tagged_factory(STATE, list)

    @model_validator(mode="after")
    def _check_rules(self) -> "ScenarioParams":
        errors: list[str] = []
        if self.unapproved_adjustment_ids and self.source is not ScenarioSource.AUTO_BUILT:
            errors.append("unapproved_adjustment_ids are allowed only when source = auto_built")
        # Scope is checked only for runs of the preset itself; for custom and auto_built
        # scenarios preset_id is provenance only ("based on this preset").
        if self.preset_id is not None and self.source in SCOPE_CHECKED_SOURCES:
            own = self.preset_id.scope.driver
            if own is not None:
                for driver in self.drivers_set():
                    if driver is not own:
                        errors.append(
                            f"preset {self.preset_id.value} is scoped to {own.value} "
                            f"but sets {driver.value} fields"
                        )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def drivers_set(self) -> list[OwnerSpec]:
        """Drivers whose block differs from its defaults."""
        return [
            driver
            for driver, attr in DRIVER_BLOCKS.items()
            if getattr(self, attr) != type(getattr(self, attr))()
        ]

    def zero_shock(self) -> "ScenarioParams":
        """Baseline parameters: every shock field reset, every state and meta field unchanged."""
        return _zero_shock(self)

    @classmethod
    def field_categories(cls) -> dict[str, FieldCategory | None]:
        """Flat map of dotted field path to category, recursing into driver blocks."""
        return _categories(cls, "")


def _zero_shock[M: BaseModel](model: M) -> M:
    updates: dict[str, Any] = {}
    for name, info in type(model).model_fields.items():
        category = field_category(type(model), name)
        if category is SHOCK:
            updates[name] = info.get_default(call_default_factory=True)
        elif category is BLOCK:
            updates[name] = _zero_shock(getattr(model, name))
    return model.model_copy(update=updates, deep=True)


def _categories(model: type[BaseModel], prefix: str) -> dict[str, FieldCategory | None]:
    result: dict[str, FieldCategory | None] = {}
    for name, info in model.model_fields.items():
        category = field_category(model, name)
        path = f"{prefix}{name}"
        if category is BLOCK:
            result.update(_categories(info.annotation, f"{path}."))  # type: ignore[arg-type]
        else:
            result[path] = category
    return result
