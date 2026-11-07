"""Shared typed contracts: provenance, quantities, statuses and module results."""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(StrEnum):
    OBSERVED = "OBSERVED"
    DISCLOSED_HISTORICAL = "DISCLOSED HISTORICAL"
    DERIVED = "DERIVED"
    ILLUSTRATIVE = "ILLUSTRATIVE"
    UNAVAILABLE = "UNAVAILABLE"


class Reliability(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Status(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    LOW = "LOW"
    NOT_ASSESSABLE = "NOT ASSESSABLE"


class ImpactType(StrEnum):
    FX_CASH = "FX modelled cash effect"
    INTEREST_EARNINGS = "Interest Mode A: annualised pre-tax EARNINGS proxy (not cash)"
    INTEREST_CASH = "Interest Mode B: ILLUSTRATIVE cash interest"
    BUNKER_CASH = "Bunker: ILLUSTRATIVE cash cost"
    FREIGHT_CASH = "Freight: ILLUSTRATIVE revenue-to-cash proxy"


MODULES = ("fx", "interest", "bunker", "freight")


# ----------------------------------------------------------------- decimals
def D(value: Any) -> Decimal:
    """Exact Decimal from int/str/float (floats go through repr, so 0.73 stays 0.73)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool is not a number")
    return Decimal(str(value))


def quantize(value: Decimal, places: int) -> Decimal:
    """Round half away from zero (what a reviewer expects: 1.2775 -> 1.278)."""
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def combine_provenance(*parts: Provenance) -> Provenance:
    """Weakest-link rule for a computed number."""
    if any(p == Provenance.UNAVAILABLE for p in parts):
        return Provenance.UNAVAILABLE
    if any(p == Provenance.ILLUSTRATIVE for p in parts):
        return Provenance.ILLUSTRATIVE
    return Provenance.DERIVED


class Quantity(StrictModel):
    """A displayed number. Exactly one provenance tag; None means UNAVAILABLE, never zero."""
    label: str
    value: float | None
    raw: str | None = None            # exact decimal string before display rounding
    unit: str
    currency: str | None = None
    provenance: Provenance
    note: str | None = None

    @classmethod
    def of(cls, label: str, value: Decimal | None, unit: str, provenance: Provenance, *,
           places: int = 3, currency: str | None = None, note: str | None = None) -> "Quantity":
        if value is None:
            return cls(label=label, value=None, unit=unit, currency=currency,
                       provenance=Provenance.UNAVAILABLE, note=note or "UNAVAILABLE")
        value = Decimal(0) if value == 0 else value          # no negative zero in the contract
        return cls(label=label, value=float(quantize(value, places)), raw=format(value, "f"),
                   unit=unit, currency=currency, provenance=provenance, note=note)


class InputSpec(StrictModel):
    """An editable assumption with bounds; the UI renders controls from this."""
    key: str
    label: str
    unit: str
    default: float | str | None
    minimum: float | None = None
    maximum: float | None = None
    editable: bool = True
    provenance: Provenance
    note: str | None = None


class InputValue(StrictModel):
    key: str
    quantity: Quantity
    overridden: bool = False
    editable: bool = True
    minimum: float | None = None
    maximum: float | None = None


class MarketBlock(StrictModel):
    series_id: str
    name: str
    unit: str
    source: str
    source_kind: str                      # fixture | live | cache
    latest_value: float | None
    latest_date: date | None
    previous_value: float | None
    previous_date: date | None
    change: float | None
    change_unit: str | None
    change_pct: float | None = None
    comparison_mode: str
    observation_timestamp: str | None
    retrieval_timestamp: datetime | None
    freshness: str                         # FRESH | STALE | UNAVAILABLE
    age_days: int | None
    provenance: Provenance
    caveat: str
    gaps: list[dict] = Field(default_factory=list)
    error: str | None = None


class ScenarioRow(StrictModel):
    label: str
    shock: float
    shock_unit: str
    impact: Quantity
    impact_usd_m: float | None = None
    adverse_usd_m: float | None = None
    status: Status
    rule_id: str
    liquidity_ratio: float | None = None


class StatusAssessment(StrictModel):
    status: Status
    rule_id: str
    rule_trace: list[str]
    threshold_version: str
    thresholds_note: str
    liquidity_ratio: float | None = None
    limit_utilisation: float | None = None
    missing: list[str] = Field(default_factory=list)


class TreasuryAction(StrictModel):
    rule_id: str
    condition: str
    action: str
    assumptions: list[str] = Field(default_factory=list)


class ModuleResult(StrictModel):
    module: str
    title: str
    formula_version: str
    threshold_version: str
    defaults_version: str
    as_of: datetime
    horizon_days: int
    impact_type: ImpactType
    reliability: Reliability
    reliability_reasons: list[str]
    market: MarketBlock
    inputs: list[InputValue]
    intermediate: list[Quantity]
    formula: str
    methodology: list[str]
    headline: ScenarioRow | None
    scenarios: list[ScenarioRow]
    assessment: StatusAssessment
    worst_case_status: Status
    disclosed_reference: list[Quantity] = Field(default_factory=list)
    treasury_actions: list[TreasuryAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    mode: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ValidationProblem(ValueError):
    """User/assumption input is invalid; the API maps this to HTTP 422."""
