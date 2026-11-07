"""Shared engine plumbing: request model, context, input resolution, rows, reliability."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from ..config import Defaults
from ..materiality import ApprovedLimit, ConfirmedTrigger, Thresholds, assess, worst
from ..models import (D, InputSpec, InputValue, MarketBlock, Provenance, Quantity, Reliability, ScenarioRow,
                      Status, StatusAssessment, StrictModel, ValidationProblem, quantize)
from ..market import Series


class ScenarioRequest(StrictModel):
    """Everything a caller may change. Anything not listed here cannot be changed."""
    overrides: dict[str, float | str] = Field(default_factory=dict)   # editable assumptions, by key
    shocks: list[float] | None = None                                  # custom sensitivity rows
    headline_shock: float | None = None                                # selected scenario
    mode: str | None = None                                            # interest: "A" | "B"
    benchmark: str | None = None                                       # interest: ECB_DFR | ECB_ESTR
    options: dict[str, str] = Field(default_factory=dict)              # non-numeric choices, e.g. position_type
    limit: ApprovedLimit | None = None
    confirmed_triggers: list[ConfirmedTrigger] = Field(default_factory=list)


@dataclass
class Liquidity:
    amount_usd_m: Decimal | None
    provenance: Provenance
    as_of: date | None
    note: str


@dataclass
class EngineContext:
    defaults: Defaults
    thresholds: Thresholds
    as_of: datetime
    series: dict[str, Series]
    blocks: dict[str, MarketBlock]
    liquidity: Liquidity
    eurusd: Decimal | None            # USD per EUR, sourced and dated
    eurusd_date: date | None
    eurusd_provenance: Provenance = Provenance.UNAVAILABLE
    warnings: list[str] = field(default_factory=list)
    positions: list | None = None     # exposures/hedges from a connected source; None -> synthetic fixtures
    hedges: list | None = None

    def to_usd_m(self, amount: Decimal | None, currency: str) -> Decimal | None:
        if amount is None:
            return None
        if currency == "USD":
            return amount
        if currency == "EUR" and self.eurusd is not None:
            return amount * self.eurusd
        return None               # no sourced, dated rate -> not comparable


def resolve_inputs(specs: list[InputSpec], overrides: dict[str, Any]) -> tuple[dict[str, Decimal | None], list[InputValue]]:
    known = {s.key for s in specs}
    unknown = set(overrides) - known
    if unknown:
        raise ValidationProblem(f"Unknown assumption(s): {', '.join(sorted(unknown))}")
    values: dict[str, Decimal | None] = {}
    shown: list[InputValue] = []
    for spec in specs:
        overridden = spec.key in overrides
        if overridden and not spec.editable:
            raise ValidationProblem(f"{spec.key} is not editable")
        raw = overrides[spec.key] if overridden else spec.default
        value = None if raw is None else D(raw)
        if value is not None:
            if spec.minimum is not None and value < D(spec.minimum):
                raise ValidationProblem(f"{spec.key}={value} is below the minimum {spec.minimum}")
            if spec.maximum is not None and value > D(spec.maximum):
                raise ValidationProblem(f"{spec.key}={value} is above the maximum {spec.maximum}")
        values[spec.key] = value
        prov = Provenance.ILLUSTRATIVE if overridden and value is not None else spec.provenance
        note = "User-supplied assumption. " + (spec.note or "") if overridden else spec.note
        shown.append(InputValue(key=spec.key, overridden=overridden, editable=spec.editable,
                                minimum=spec.minimum, maximum=spec.maximum,
                                quantity=Quantity.of(spec.label, value, spec.unit, prov, places=6, note=note.strip() if note else None)))
    return values, shown


def make_row(ctx: EngineContext, req: ScenarioRequest, *, label: str, shock: Decimal, shock_unit: str,
             impact: Decimal | None, impact_unit: str, currency: str, places: int,
             comparable_to_liquidity: bool, prov: Provenance, extra_missing: list[str] | None = None,
             adverse_when_negative: bool = True) -> tuple[ScenarioRow, StatusAssessment]:
    impact_usd = ctx.to_usd_m(impact, currency) if impact is not None else None
    adverse = None
    if impact_usd is not None:
        adverse = max(Decimal(0), -impact_usd if adverse_when_negative else impact_usd)
    missing = list(extra_missing or [])
    if impact is not None and impact_usd is None and (comparable_to_liquidity or req.limit):
        missing.append(f"sourced, dated {currency}/USD rate for conversion")
    limit_usd = None
    if req.limit is not None:
        limit_usd = ctx.to_usd_m(D(req.limit.amount), req.limit.currency)
        if limit_usd is None:
            missing.append("limit currency conversion")
    assessment = assess(th=ctx.thresholds, adverse_usd_m=adverse, impact_available=impact is not None,
                        comparable_to_liquidity=comparable_to_liquidity, liquidity_usd_m=ctx.liquidity.amount_usd_m,
                        limit_usd_m=limit_usd, triggers=req.confirmed_triggers, extra_missing=missing)
    row = ScenarioRow(label=label, shock=float(shock), shock_unit=shock_unit,
                      impact=Quantity.of("Impact", impact, impact_unit, prov, places=places, currency=currency),
                      impact_usd_m=float(quantize(impact_usd, 3)) if impact_usd is not None and comparable_to_liquidity else None,
                      adverse_usd_m=None if adverse is None else float(quantize(adverse, 3)),
                      status=assessment.status, rule_id=assessment.rule_id, liquidity_ratio=assessment.liquidity_ratio)
    return row, assessment


def table_shocks(req: ScenarioRequest, base: Decimal, multipliers: list, labels: list[str],
                 bounds: tuple[Decimal, Decimal] | None = None) -> tuple[list[tuple[str, Decimal]], list[str]]:
    """Custom shocks are returned as given (the caller validates them strictly). Derived rows that fall outside the
    allowed range are omitted and reported, so a valid selected scenario is never rejected by its own stress rows."""
    if req.shocks is not None:
        return [(f"Custom {i + 1}", D(s)) for i, s in enumerate(req.shocks)], []
    rows, omitted = [], []
    for m, label in zip(multipliers, labels):
        value = base * D(m)
        if bounds is not None and not bounds[0] <= value <= bounds[1]:
            omitted.append(f"{label} ({float(value):+g})")
        else:
            rows.append((label, value))
    return rows, omitted


def reliability(market: MarketBlock, inputs: list[InputValue], extra: list[str] | None = None) -> tuple[Reliability, list[str]]:
    """Prototype rule: any illustrative/historical/unavailable input, or non-fresh data, means LOW (spec: all four LOW)."""
    reasons = list(extra or [])
    if market.provenance == Provenance.ILLUSTRATIVE:
        reasons.append("Market series is a synthetic fixture")
    if market.freshness != "FRESH":
        reasons.append(f"Market data freshness: {market.freshness}")
    for item in inputs:
        p = item.quantity.provenance
        if p in (Provenance.ILLUSTRATIVE, Provenance.DISCLOSED_HISTORICAL, Provenance.UNAVAILABLE):
            reasons.append(f"{item.quantity.label}: {p.value}")
    return (Reliability.LOW, reasons) if reasons else (Reliability.MEDIUM, ["All inputs observed and fresh; internal exposures still unvalidated"])


def stat_label(statuses: list[Status]) -> Status:
    return worst(statuses)


STATUS_ACTIONS = {
    Status.CRITICAL: ("ACT-CRIT", "Urgent senior Treasury review and funding/mitigation assessment."),
    Status.HIGH: ("ACT-HIGH", "Formal review of exposure, hedges, timing and liquidity."),
    Status.LOW: ("ACT-LOW", "Monitor only."),
    Status.NOT_ASSESSABLE: ("ACT-NA", "Request validation of the missing inputs; do not infer a low risk."),
}


def status_action(assessment: StatusAssessment):
    from ..models import TreasuryAction
    rule, text = STATUS_ACTIONS[assessment.status]
    return TreasuryAction(rule_id=rule, condition=f"Status is {assessment.status.value} under {assessment.rule_id} "
                          f"({assessment.threshold_version})", action=text)


def build_result(ctx: EngineContext, req: ScenarioRequest, *, module: str, title: str, impact_type, horizon_days: int,
                 market_key: str, inputs: list[InputValue], intermediate: list[Quantity], formula: str,
                 methodology: list[str], headline: tuple[ScenarioRow, StatusAssessment] | None,
                 rows: list[tuple[ScenarioRow, StatusAssessment]], disclosed_reference: list[Quantity] | None = None,
                 actions=None, warnings: list[str] | None = None, labels: dict[str, str] | None = None,
                 mode: str | None = None, extra: dict | None = None, reliability_extra: list[str] | None = None):
    from ..models import ModuleResult
    market = ctx.blocks[market_key]
    rel, reasons = reliability(market, inputs, reliability_extra)
    all_warnings = list(ctx.warnings) + list(warnings or [])
    if market.freshness != "FRESH":
        all_warnings.append(f"Market data is {market.freshness}" + (f": {market.error}" if market.error else ""))
    if market.error and market.freshness == "FRESH":
        all_warnings.append(market.error)
    if market.gaps:
        all_warnings.append(f"{len(market.gaps)} gap(s) in the series; missing dates are shown as missing, never interpolated")
    head_row, head_assess = headline if headline else (None, None)
    if head_assess is None:                       # nothing computable: surface a NOT ASSESSABLE assessment
        head_assess = assess(th=ctx.thresholds, adverse_usd_m=None, impact_available=False, comparable_to_liquidity=True,
                             liquidity_usd_m=ctx.liquidity.amount_usd_m, limit_usd_m=None, triggers=req.confirmed_triggers)
    acts = list(actions or [])
    acts.append(status_action(head_assess))
    return ModuleResult(
        module=module, title=title, formula_version=ctx.defaults.formula_version, threshold_version=ctx.thresholds.version,
        defaults_version=ctx.defaults.defaults_version, as_of=ctx.as_of, horizon_days=horizon_days, impact_type=impact_type,
        reliability=rel, reliability_reasons=reasons, market=market, inputs=inputs, intermediate=intermediate, formula=formula,
        methodology=methodology, headline=head_row, scenarios=[r for r, _ in rows], assessment=head_assess,
        worst_case_status=worst([r.status for r, _ in rows] + ([head_row.status] if head_row else [])),
        disclosed_reference=disclosed_reference or [], treasury_actions=acts, warnings=all_warnings,
        labels=labels or {}, mode=mode, extra=extra or {})


def horizon_scale(overrides: dict, baseline_days: int) -> tuple[Decimal, str | None]:
    """Pro-rata factor when the caller changes the horizon: assumptions are stated for the baseline horizon."""
    if "horizon_days" not in overrides:
        return Decimal(1), None
    days = D(overrides["horizon_days"])
    if days == baseline_days or days <= 0:
        return Decimal(1), None
    return days / baseline_days, (f"Horizon is {int(days)} days: volume/exposure assumptions stated for {baseline_days} days "
                                  f"are scaled pro rata (x{float(days / baseline_days):.3f}) unless overridden")
