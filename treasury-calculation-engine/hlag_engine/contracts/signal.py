"""Signal schema from the news engine (spec 2.8, 10.6, D58) and submit_signal validation.

Units: when magnitude_unit is ``pct``, magnitude is a whole percent (25 means 25%);
``bp`` is basis points.
"""

from typing import Any

from pydantic import (
    AwareDatetime,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import (
    AffectedDriver,
    Direction,
    EventType,
    MagnitudeUnit,
    PresetId,
    SignalKind,
    Trade,
)
from hlag_engine.contracts.iso_codes import ISO_3166_ALPHA2, ISO_4217


class Signal(ContractModel):
    """One news event, in the format agreed with the news engine team."""

    event_id: str = Field(min_length=1)
    version: int = Field(ge=1, strict=True)
    published_at: AwareDatetime
    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_links: list[HttpUrl] = Field(min_length=1)
    event_type: EventType
    kind: SignalKind
    affected_drivers: list[AffectedDriver] = Field(default_factory=list)
    affected_trades: list[Trade] = Field(default_factory=list)
    affected_currencies: list[str] = Field(default_factory=list)  # ISO 4217
    affected_countries: list[str] = Field(default_factory=list)  # ISO 3166-1 alpha-2
    named_entities: list[str] = Field(default_factory=list)
    direction: Direction
    magnitude: float | None = None
    magnitude_unit: MagnitudeUnit | None = None
    magnitude_subject: str | None = None
    suggested_preset: PresetId | None = None
    confidence: float = Field(ge=0, le=1)
    expected_duration_days: int | None = Field(default=None, ge=1)

    @field_validator("affected_currencies")
    @classmethod
    def _iso_4217(cls, value: list[str]) -> list[str]:
        bad = [code for code in value if code not in ISO_4217]
        if bad:
            raise PydanticCustomError(
                "unknown_currency_code", "not ISO 4217 currency codes: {bad}", {"bad": bad}
            )
        return value

    @field_validator("affected_countries")
    @classmethod
    def _iso_3166(cls, value: list[str]) -> list[str]:
        bad = [code for code in value if code not in ISO_3166_ALPHA2]
        if bad:
            raise PydanticCustomError(
                "unknown_country_code", "not ISO 3166-1 alpha-2 country codes: {bad}", {"bad": bad}
            )
        return value

    @model_validator(mode="after")
    def _magnitude_rules(self) -> "Signal":
        quantitative = self.kind is SignalKind.QUANTITATIVE
        if not quantitative and self.magnitude is not None:
            raise PydanticCustomError(
                "magnitude_not_allowed", "magnitude is for quantitative signals only"
            )
        if quantitative and self.magnitude is None:
            raise PydanticCustomError(
                "missing_magnitude", "a quantitative signal must state a magnitude"
            )
        if self.magnitude is not None and self.magnitude_unit is None:
            raise PydanticCustomError(
                "missing_magnitude_unit", "a magnitude needs a magnitude_unit"
            )
        return self


class RejectedSignal(ContractModel):
    event_id: str | None  # None when the submitted item had no usable event_id
    errors: list[str] = Field(min_length=1)


class SubmitSignalResponse(ContractModel):
    """Response of submit_signal (10.6)."""

    accepted: list[str] = Field(default_factory=list)
    rejected: list[RejectedSignal] = Field(default_factory=list)


def submit_signal(signals: Signal | dict[str, Any] | list[Signal | dict[str, Any]]) -> SubmitSignalResponse:
    """Validate one signal or a list of signals (10.6).

    Each item is accepted or rejected independently. Every rejection lists named errors in
    the form ``"<error_name>: <message>"``, for example ``"invalid_event_type: ..."`` or
    ``"missing_evidence_link: ..."``.

    Validation only. Storing accepted signals and rejecting an equal or lower version of a
    known event_id as stale is done by data.signals.submit_signal (step 3, P34).
    """
    items = signals if isinstance(signals, list) else [signals]
    response = SubmitSignalResponse()
    for item in items:
        if isinstance(item, Signal):
            item = item.model_dump(mode="json")  # re-validate: objects can be built unchecked
        if not isinstance(item, dict):
            response.rejected.append(
                RejectedSignal(event_id=None, errors=["invalid_signal: a signal must be an object"])
            )
            continue
        raw_id = item.get("event_id")
        event_id = raw_id if isinstance(raw_id, str) and raw_id else None
        try:
            signal = Signal.model_validate(item)
        except ValidationError as exc:
            response.rejected.append(
                RejectedSignal(event_id=event_id, errors=_named_errors(exc))
            )
            continue
        response.accepted.append(signal.event_id)
    return response


def _named_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for err in exc.errors():
        loc = err["loc"]
        field = str(loc[0]) if loc else ""
        kind = err["type"]
        if not field:
            name = kind  # cross-field rules raise named custom errors
        elif kind == "missing":
            name = f"missing_{field}"
        elif kind == "extra_forbidden":
            name = f"unknown_field_{field}"
        elif field == "evidence_links" and kind == "too_short":
            name = "missing_evidence_link"
        elif kind in ("unknown_currency_code", "unknown_country_code"):
            name = kind
        else:
            name = f"invalid_{field}"
        where = ".".join(str(part) for part in loc)
        message = err["msg"]
        errors.append(f"{name}: {where}: {message}" if where else f"{name}: {message}")
    return errors
