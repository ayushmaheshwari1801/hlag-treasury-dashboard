"""Engine-side adjustment validation (spec 3.3, 3.7, 10.3, D48, D52).

validate_adjustment rejects a proposal when: the key is not in the catalogue; no cited
event has a listed event type; a direct value is proposed for a step_only key or from a
qualitative signal; the new value breaks a hard limit; the review date is more than
max_review_days after the effective date; or no event_ids are given. It also checks the
value modes (step_only, step_or_direct, switch) and the step amount, and warns when a
future-dated adjustment targets a sizing key (D52).

Signals are passed in; signal storage arrives in step 3.
"""

import dataclasses
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.enums import AdjustmentStatus, SignalKind
from hlag_engine.contracts.signal import Signal
from hlag_engine.company_profile.resolve import RunContext, catalogue_entry, is_sizing, resolve
from hlag_engine.company_profile.schema import CatalogueEntry, MissingProfileValue, ValueMode

STEP_TOLERANCE = 1e-9


@dataclass
class AdjustmentValidation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, name: str, message: str) -> None:
        self.errors.append(f"{name}: {message}")

    def warn(self, name: str, message: str) -> None:
        self.warnings.append(f"{name}: {message}")


def validate_adjustment(
    proposal: Adjustment,
    signals: Mapping[str, Signal] | Iterable[Signal],
    ctx: RunContext,
) -> AdjustmentValidation:
    """Validate a proposed adjustment against the catalogue. ctx.as_of is today's T0."""
    result = AdjustmentValidation()
    by_id = signals if isinstance(signals, Mapping) else {s.event_id: s for s in signals}
    key = proposal.param_key

    if proposal.status is not AdjustmentStatus.PROPOSED:
        result.error("not_a_proposal", f"status is {proposal.status.value}, expected proposed")
    if not proposal.event_ids:
        result.error("missing_event_ids", "an adjustment must cite at least one event")

    entry = catalogue_entry(key, ctx)
    if entry is None:
        result.error("key_not_in_catalogue", f"{key} is not in the adjustment catalogue")
        return result

    cited = []
    for event_id in proposal.event_ids:
        if event_id not in by_id:
            result.error("unknown_event", f"event {event_id} was not supplied")
        else:
            cited.append(by_id[event_id])
    listed = [s for s in cited if s.event_type in entry.event_types]
    if cited and not listed:
        allowed = ", ".join(t.value for t in entry.event_types)
        result.error("event_type_not_allowed", f"none of the cited events has a type listed for {key} ({allowed})")

    _check_mode(proposal, entry, listed, result)
    _check_hard_limits(proposal, entry, result)

    window = (proposal.review_date - proposal.effective_date).days
    if window > entry.max_review_days:
        result.error(
            "review_too_long",
            f"review date is {window} days after the effective date (max {entry.max_review_days})",
        )

    sizing = is_sizing(key, ctx)
    if sizing and proposal.effective_date > ctx.as_of:
        result.warn(
            "future_dated_sizing_key",
            f"{key} is a sizing key; it takes effect only from the first run with T0 on or after "
            f"{proposal.effective_date} (D52)",
        )
    _check_old_value(proposal, entry, ctx, result)
    return result


def _check_mode(proposal: Adjustment, entry: CatalogueEntry, listed: list[Signal], result: AdjustmentValidation) -> None:
    kinds = {s.kind for s in listed}
    if entry.value_mode is ValueMode.SWITCH:
        if proposal.step is not None:
            result.error("step_not_allowed", "switch keys take a direct value, never a step")
        if not isinstance(proposal.new_value, bool):
            result.error("switch_value_not_bool", f"switch value must be true or false, got {proposal.new_value!r}")
        if listed and not kinds & {SignalKind.FACTUAL, SignalKind.QUANTITATIVE}:
            result.error("switch_needs_factual_or_quantitative", "a switch is set only from factual or quantitative signals (D48)")
        return

    if proposal.step is None:
        if entry.value_mode is ValueMode.STEP_ONLY:
            result.error("direct_value_not_allowed", f"{proposal.param_key} is step_only")
        elif listed and SignalKind.QUANTITATIVE not in kinds:
            result.error("direct_value_from_qualitative", "a direct value needs a quantitative signal; qualitative news chooses a step")
        return

    assert entry.steps is not None
    step = getattr(entry.steps, proposal.step.value)
    old, new = proposal.old_value, proposal.new_value
    if not _is_number(old) or not _is_number(new):
        result.error("step_needs_numbers", "old_value and new_value must be numbers for a step")
        return
    if entry.step_effect == "reduce_floor_zero":
        expected = 0.0 if proposal.step.value == "large" else max(0.0, old - step)
        if not math.isclose(new, expected, abs_tol=STEP_TOLERANCE):
            result.error("step_amount_mismatch", f"a {proposal.step.value} cut from {old} gives {expected}, not {new}")
    elif not math.isclose(abs(new - old), step, abs_tol=STEP_TOLERANCE):
        result.error("step_amount_mismatch", f"a {proposal.step.value} step is ±{step}; {old} -> {new} moves {new - old}")


def _check_hard_limits(proposal: Adjustment, entry: CatalogueEntry, result: AdjustmentValidation) -> None:
    if entry.hard_limits is None:
        return
    low, high = entry.hard_limits
    value = proposal.new_value
    if not _is_number(value) or not low <= value <= high:
        result.error("hard_limit_breach", f"new value {value!r} is outside the hard limits [{low}, {high}]")


def _check_old_value(proposal: Adjustment, entry: CatalogueEntry, ctx: RunContext, result: AdjustmentValidation) -> None:
    """old_value must be the value as resolved on effective_date, without overrides."""
    at_effective = dataclasses.replace(ctx, as_of=proposal.effective_date, overrides={})
    try:
        current = resolve(proposal.param_key, proposal.effective_date, at_effective)
    except MissingProfileValue as exc:
        result.warn("old_value_unchecked", str(exc))
        return
    if _is_number(current) and _is_number(proposal.old_value):
        same = math.isclose(current, proposal.old_value, abs_tol=STEP_TOLERANCE)
    else:
        same = current == proposal.old_value
    if not same:
        result.error("old_value_mismatch", f"old_value {proposal.old_value!r} differs from the resolved value {current!r}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
