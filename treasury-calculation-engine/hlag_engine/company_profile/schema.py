"""Company profile schema and loader (spec 3.1, 3.3, 3.7, D69, D70).

The profile is one YAML file, immutable during normal use. Every parameter is read
through resolve() (company_profile/resolve.py), never straight from the YAML.

Placeholders: a disclosed figure not yet entered has ``value: null`` and
``status: to_enter``; a value awaiting a check keeps its working default with
``status: to_verify``; an anchor value that needs loaded market history (step 3) has
``status: pending_history``. docs/placeholders.md lists them all.
"""

import hashlib
import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import EventType, ProvenanceLabel

PROFILE_DIR = Path(__file__).parent
DEFAULT_PROFILE_PATH = PROFILE_DIR / "company_profile.yaml"  # the first profile version
# Names the active profile file (P32). Switched only by the explicit, audit-logged admin step
# hlag_engine.data.profile_admin.activate_profile_version.
ACTIVE_SETTING_PATH = PROFILE_DIR / "active.yaml"


class ProfileError(Exception):
    """Base class for profile problems."""


class UnknownParameter(ProfileError, KeyError):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:
        return f"unknown_parameter: {self.key} is not in the profile"


class MissingProfileValue(ProfileError):
    """A required value is a placeholder (status to_enter or pending_history)."""

    def __init__(self, key: str, status: str):
        super().__init__(f"missing_profile_value: {key} is a placeholder ({status})")
        self.key = key
        self.status = status


class EntryStatus(StrEnum):
    OK = "ok"
    TO_ENTER = "to_enter"  # disclosed figure, not yet entered (value is null)
    TO_VERIFY = "to_verify"  # working default in use, must be checked
    PENDING_HISTORY = "pending_history"  # needs loaded market history (step 3)


PLACEHOLDER_STATUSES = frozenset({EntryStatus.TO_ENTER, EntryStatus.TO_VERIFY, EntryStatus.PENDING_HISTORY})
NULL_STATUSES = frozenset({EntryStatus.TO_ENTER, EntryStatus.PENDING_HISTORY})


class ParamKind(StrEnum):
    """How a parameter is resolved (3.2, D52)."""

    SIZING = "sizing"  # resolved once at T0
    TIME_VARYING = "time_varying"  # resolved per date
    STATE = "state"  # a switch identical in both passes
    CONTEXT = "context"  # disclosed context, never used in calculations


class Source(ContractModel):
    report: str = Field(min_length=1)
    page: str | None = None


class ProfileEntry(ContractModel):
    """One profile parameter, in the shape of spec 3.1."""

    value: Any = None
    label: ProvenanceLabel
    kind: ParamKind
    unit: str | None = None
    source: Source | None = None
    formula: str | None = None
    allowed_range: tuple[float, float] | None = None
    allowed_values: list[Any] | None = None  # for non-numeric ASSUMPTION values
    adjustable: bool = False
    rationale: str | None = None
    as_of: date | None = None
    period: str | None = None  # reporting period the value reflects, e.g. "H1 2026"
    status: EntryStatus = EntryStatus.OK
    note: str | None = None  # where to find or how to derive a placeholder

    @model_validator(mode="after")
    def _rules(self) -> "ProfileEntry":
        errors: list[str] = []
        if self.label is ProvenanceLabel.DISCLOSED:
            if self.source is None:
                errors.append("DISCLOSED entries need source")
            if self.adjustable:
                errors.append("DISCLOSED entries must have adjustable: false")
        if self.label is ProvenanceLabel.DERIVED and not self.formula:
            errors.append("DERIVED entries need formula")
        if self.label is ProvenanceLabel.ASSUMPTION:
            if self.allowed_range is None and self.allowed_values is None:
                errors.append("ASSUMPTION entries need allowed_range or allowed_values")
            if not self.rationale:
                errors.append("ASSUMPTION entries need rationale")
        if self.allowed_range is not None and self.allowed_range[0] > self.allowed_range[1]:
            errors.append("allowed_range must be [low, high]")
        if self.value is None and self.status not in NULL_STATUSES:
            errors.append("value is null but status is not to_enter or pending_history")
        if self.value is not None and self.status in NULL_STATUSES:
            errors.append(f"status {self.status.value} needs value: null")
        if self.status in PLACEHOLDER_STATUSES and not self.note:
            errors.append("placeholders need a note saying where to find or how to derive the value")
        if self.value is not None and not self.in_bounds(self.value):
            errors.append(f"value {self.value!r} is outside its allowed range or values")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @property
    def overridable(self) -> bool:
        """P21 + P66: keys with an allowed_range, and state switches with allowed_values, may be overridden per run."""
        return self.allowed_range is not None or (self.kind is ParamKind.STATE and self.allowed_values is not None)

    def in_bounds(self, value: Any) -> bool:
        if self.allowed_range is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            low, high = self.allowed_range
            return low <= value <= high
        if self.allowed_values is not None:
            # same type as well as equal, so 1 never passes for True
            return any(type(value) is type(v) and value == v for v in self.allowed_values)
        return True


class ValueMode(StrEnum):
    STEP_ONLY = "step_only"
    STEP_OR_DIRECT = "step_or_direct"
    SWITCH = "switch"


class StepSizes(ContractModel):
    """Step magnitudes; the sign follows the event direction (3.3)."""

    small: float = Field(gt=0)
    medium: float = Field(gt=0)
    large: float = Field(gt=0)


class CatalogueEntry(ContractModel):
    """One allowlisted key agents may propose changing (3.3)."""

    module: str
    event_types: list[EventType] = Field(min_length=1)
    value_mode: ValueMode
    steps: StepSizes | None = None
    hard_limits: tuple[float, float] | None = None
    max_review_days: int = Field(ge=1)
    # For availability keys the step is an absolute cut floored at 0, and large means 0 (3.7).
    step_effect: str = "add"  # "add" (±step) or "reduce_floor_zero"

    @model_validator(mode="after")
    def _mode_fields(self) -> "CatalogueEntry":
        if self.value_mode is ValueMode.SWITCH:
            if self.steps is not None or self.hard_limits is not None:
                raise ValueError("switch keys have no steps or hard_limits")
        else:
            if self.steps is None or self.hard_limits is None:
                raise ValueError("step keys need steps and hard_limits")
        if self.step_effect not in ("add", "reduce_floor_zero"):
            raise ValueError("step_effect must be add or reduce_floor_zero")
        return self


class AnchorSeries(ContractModel):
    value: float | None = None
    label: ProvenanceLabel
    status: EntryStatus = EntryStatus.OK
    formula: str | None = None
    source: Source | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _placeholder(self) -> "AnchorSeries":
        if self.value is None and self.status not in NULL_STATUSES:
            raise ValueError("null anchor value needs status pending_history or to_enter")
        if self.status in PLACEHOLDER_STATUSES and not self.note:
            raise ValueError("anchor placeholders need a note")
        return self


class AnchorSnapshot(ContractModel):
    """Anchor snapshot (3.7, D69). Its period is always the OCF run-rate's period (D70)."""

    period: str = Field(min_length=1)
    start: date
    end: date
    series: dict[str, AnchorSeries]
    context: dict[str, Any] = Field(default_factory=dict)  # not used in calculations


class AvailabilityCatalogue(ContractModel):
    """Template for liquidity.<tier>.<counterparty> keys, one per register bank (3.7)."""

    tiers: list[str] = Field(min_length=1)
    entry: CatalogueEntry


class Profile(ContractModel):
    profile_version: str = Field(min_length=1)
    parameters: dict[str, ProfileEntry]
    catalogue: dict[str, CatalogueEntry]
    availability_catalogue: AvailabilityCatalogue
    anchor_snapshot: AnchorSnapshot
    content_hash: str = ""

    # Keys whose period must equal the anchor period (D70): the OCF run-rate and P0.
    ANCHOR_LINKED_PREFIXES: ClassVar[tuple[str, ...]] = ("liquidity.baseline_ocf_daily_usd", "freight.p0.")

    @model_validator(mode="after")
    def _cross_checks(self) -> "Profile":
        errors: list[str] = []
        for key, entry in self.catalogue.items():
            if key not in self.parameters:
                errors.append(f"catalogue key {key} has no profile entry")
            elif not self.parameters[key].adjustable:
                errors.append(f"catalogue key {key} must be adjustable in the profile")
            elif self.parameters[key].label is ProvenanceLabel.DISCLOSED:
                errors.append(f"disclosed figure {key} cannot be in the catalogue")
        for key, entry in self.parameters.items():
            if entry.adjustable and key not in self.catalogue:
                errors.append(f"{key} is adjustable but not in the catalogue")
        period = self.anchor_snapshot.period
        linked = [
            k for k in self.parameters if k.startswith(self.ANCHOR_LINKED_PREFIXES)
        ]
        if not linked:
            errors.append("profile has no OCF run-rate or P0 entries to link to the anchor")
        for key in linked:
            if self.parameters[key].period != period:
                errors.append(
                    f"{key} period {self.parameters[key].period!r} differs from the anchor "
                    f"period {period!r}; replace them together (D70)"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def entry(self, key: str) -> ProfileEntry:
        try:
            return self.parameters[key]
        except KeyError:
            raise UnknownParameter(key) from None

    def placeholders(self) -> dict[str, ProfileEntry]:
        return {k: e for k, e in self.parameters.items() if e.status in PLACEHOLDER_STATUSES}


def content_hash(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def profile_from_dict(raw: dict[str, Any]) -> Profile:
    data = dict(raw)
    data.pop("content_hash", None)
    return Profile.model_validate({**data, "content_hash": content_hash(data)})


def active_profile_path(setting_path: Path | str = ACTIVE_SETTING_PATH) -> Path:
    """The profile file named by the active setting (P32), in the setting's own folder."""
    setting_path = Path(setting_path)
    with open(setting_path, encoding="utf-8") as handle:
        setting = yaml.safe_load(handle) or {}
    name = setting.get("profile_file") if isinstance(setting, dict) else None
    if not isinstance(name, str) or not name or Path(name).name != name or not name.endswith(".yaml"):
        raise ProfileError(f"{setting_path}: profile_file must be a plain .yaml file name, got {name!r}")
    return setting_path.parent / name


def load_profile(path: Path | str | None = None) -> Profile:
    """Load a profile file; with no path, the active profile version (P32)."""
    return profile_from_dict(load_raw(path))


def load_raw(path: Path | str | None = None) -> dict[str, Any]:
    with open(path if path is not None else active_profile_path(), encoding="utf-8") as handle:
        return yaml.safe_load(handle)
