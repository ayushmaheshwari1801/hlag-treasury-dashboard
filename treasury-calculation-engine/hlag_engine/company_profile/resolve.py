"""Parameter resolution through three layers (spec 3.2, D52).

Order: company profile, then approved active adjustments, then run overrides.
- Sizing (and state and context) keys are resolved once at T0: an adjustment applies from
  the first run whose T0 is on or after its effective date (D52).
- Time-varying keys are resolved per date: an adjustment applies on dates with
  effective_date <= date < review_date.
- Two adjustments on one key apply in effective_date order; on the same date the later
  approval wins.
- A run override replaces the resolved value for every date in the horizon.
- The value is checked against hard limits after each layer.
- In an auto-built run, listed unapproved adjustments apply as if approved.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.enums import AdjustmentStatus, ProvenanceLabel, ScenarioSource
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.company_profile.registers import Registers
from hlag_engine.company_profile.schema import (
    CatalogueEntry,
    MissingProfileValue,
    ParamKind,
    Profile,
    ProfileError,
    UnknownParameter,
)

AVAILABILITY_PREFIX = "liquidity."
AVAILABILITY_BASE_VALUE = 1.0  # a counterparty's share of a tier is fully available by default


class HardLimitBreach(ProfileError):
    def __init__(self, key: str, value: Any, layer: str, limits: Any):
        super().__init__(f"hard_limit_breach: {key} = {value!r} from {layer} is outside {limits}")
        self.key, self.value, self.layer, self.limits = key, value, layer, limits


class OverrideNotAllowed(ProfileError):
    def __init__(self, key: str, reason: str):
        super().__init__(f"override_not_allowed: {key}: {reason}")
        self.key = key


@dataclass(frozen=True)
class RunContext:
    """What resolve() needs from a run: the profile, T0 and the adjustment and override layers."""

    profile: Profile
    as_of: date
    adjustments: tuple[Adjustment, ...] = ()
    overrides: Mapping[str, Any] = field(default_factory=dict)
    unapproved_ids: frozenset[str] = frozenset()  # applied as if approved (auto_built only)
    registers: Registers | None = None  # needed to resolve availability keys

    @classmethod
    def for_scenario(
        cls,
        profile: Profile,
        as_of: date,
        params: ScenarioParams,
        adjustments: tuple[Adjustment, ...] = (),
        registers: Registers | None = None,
    ) -> "RunContext":
        unapproved = (
            frozenset(params.unapproved_adjustment_ids)
            if params.source is ScenarioSource.AUTO_BUILT
            else frozenset()
        )
        return cls(profile, as_of, tuple(adjustments), dict(params.overrides), unapproved, registers)


@dataclass(frozen=True)
class ResolvedValue:
    key: str
    value: Any
    layer: str  # "profile", "adjustment" or "override"
    label: ProvenanceLabel
    adjustment_id: str | None = None


def resolve(key: str, on: date, ctx: RunContext) -> Any:
    """The value of a parameter on a date, through the three layers."""
    return resolve_detail(key, on, ctx).value


def resolve_detail(key: str, on: date, ctx: RunContext) -> ResolvedValue:
    entry_kind, base_value, base_label, catalogue = _base(key, ctx)
    lookup = on if entry_kind is ParamKind.TIME_VARYING else ctx.as_of

    if key in ctx.overrides:
        value = ctx.overrides[key]
        _check_override(key, value, ctx, catalogue)
        return ResolvedValue(key, value, "override", ProvenanceLabel.ASSUMPTION)

    if base_value is None:
        raise MissingProfileValue(key, ctx.profile.entry(key).status.value)
    result = ResolvedValue(key, base_value, "profile", base_label)

    for adj in active_adjustments(key, lookup, ctx):
        if catalogue is None:
            raise ProfileError(f"adjustment {adj.adjustment_id} targets {key}, which is not in the catalogue")
        _check_hard_limits(key, adj.new_value, f"adjustment {adj.adjustment_id}", catalogue)
        result = ResolvedValue(key, adj.new_value, "adjustment", ProvenanceLabel.ASSUMPTION, adj.adjustment_id)
    return result


def active_adjustments(key: str, on: date, ctx: RunContext) -> list[Adjustment]:
    """Adjustments on a key in force on a date, in the order they apply (last one wins)."""
    applicable = [
        a
        for a in ctx.adjustments
        if a.param_key == key
        and (
            a.status is AdjustmentStatus.APPROVED
            or (a.status is AdjustmentStatus.PROPOSED and a.adjustment_id in ctx.unapproved_ids)
        )
        and a.effective_date <= on < a.review_date
    ]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        applicable,
        key=lambda a: (a.effective_date, a.decided_at is None, a.decided_at or epoch),
    )


def catalogue_entry(key: str, ctx: RunContext) -> CatalogueEntry | None:
    if key in ctx.profile.catalogue:
        return ctx.profile.catalogue[key]
    if _is_availability_key(key, ctx):
        return ctx.profile.availability_catalogue.entry
    return None


def is_sizing(key: str, ctx: RunContext) -> bool:
    return _base(key, ctx)[0] is not ParamKind.TIME_VARYING


def _base(key: str, ctx: RunContext) -> tuple[ParamKind, Any, ProvenanceLabel, CatalogueEntry | None]:
    if key in ctx.profile.parameters:
        entry = ctx.profile.parameters[key]
        return entry.kind, entry.value, entry.label, ctx.profile.catalogue.get(key)
    if _is_availability_key(key, ctx):
        return (
            ParamKind.TIME_VARYING,
            AVAILABILITY_BASE_VALUE,
            ProvenanceLabel.ASSUMPTION,
            ctx.profile.availability_catalogue.entry,
        )
    raise UnknownParameter(key)


def _is_availability_key(key: str, ctx: RunContext) -> bool:
    if ctx.registers is None or not key.startswith(AVAILABILITY_PREFIX):
        return False
    return key in ctx.registers.availability_keys(ctx.profile.availability_catalogue.tiers)


def _check_hard_limits(key: str, value: Any, layer: str, catalogue: CatalogueEntry) -> None:
    if catalogue.hard_limits is None:  # switch keys
        if not isinstance(value, bool):
            raise HardLimitBreach(key, value, layer, "true / false")
        return
    low, high = catalogue.hard_limits
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise HardLimitBreach(key, value, layer, [low, high])


def _check_override(key: str, value: Any, ctx: RunContext, catalogue: CatalogueEntry | None) -> None:
    entry = ctx.profile.parameters.get(key)
    if entry is None or not entry.overridable:
        raise OverrideNotAllowed(key, "only keys with an allowed_range, or state switches, may be overridden (P21, P66)")
    if not entry.in_bounds(value):
        allowed = list(entry.allowed_range) if entry.allowed_range is not None else entry.allowed_values
        raise OverrideNotAllowed(key, f"{value!r} is outside the allowed values {allowed}")
    if catalogue is not None:
        _check_hard_limits(key, value, "override", catalogue)
