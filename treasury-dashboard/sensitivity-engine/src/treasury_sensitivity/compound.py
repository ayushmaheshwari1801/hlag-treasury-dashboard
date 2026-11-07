"""Compound stress (spec s9): aggregate only compatible cash effects and avoid known double counting.

Sums each module's selected scenario as if they occur together over one horizon. No covariance or
timing model: that is the 'Full build' phase in the specification.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from .engines.common import EngineContext, ScenarioRequest
from .materiality import assess
from .models import D, ImpactType, ModuleResult, Provenance, StatusAssessment, StrictModel, combine_provenance, quantize

CASH_TYPES = {ImpactType.FX_CASH, ImpactType.INTEREST_CASH, ImpactType.BUNKER_CASH, ImpactType.FREIGHT_CASH}


class CompoundRequest(StrictModel):
    scenarios: dict[str, ScenarioRequest] = Field(default_factory=dict)
    modules: list[str] = Field(default_factory=lambda: ["fx", "interest", "bunker", "freight"])
    target_horizon_days: int | None = None
    bunker_surcharge_in_freight_rate: bool | None = None


class CompoundLine(StrictModel):
    module: str
    label: str
    impact_type: str
    horizon_days: int | None
    amount_usd_m: float | None
    provenance: Provenance
    included: bool
    reason: str | None = None


class CompoundResult(StrictModel):
    as_of: datetime
    target_horizon_days: int
    included: list[CompoundLine]
    excluded: list[CompoundLine]
    overlap_adjustment: CompoundLine | None
    total_usd_m: float | None
    adverse_usd_m: float | None
    assessment: StatusAssessment
    warnings: list[str]
    notes: list[str]


def _input(result: ModuleResult, key: str) -> Decimal | None:
    for item in result.inputs:
        if item.key == key and item.quantity.raw is not None:
            return D(item.quantity.raw)
    return None


def aggregate(ctx: EngineContext, results: dict[str, ModuleResult], req: CompoundRequest, scenario_req: ScenarioRequest) -> CompoundResult:
    cfg = ctx.defaults["compound"]
    target = req.target_horizon_days or cfg["target_horizon_days"]
    flag = cfg["bunker_surcharge_in_freight_rate"] if req.bunker_surcharge_in_freight_rate is None else req.bunker_surcharge_in_freight_rate
    included: list[CompoundLine] = []
    excluded: list[CompoundLine] = []
    amounts: dict[str, Decimal] = {}

    for module, res in results.items():
        head = res.headline
        base = dict(module=module, label=res.title, impact_type=res.impact_type.value, horizon_days=res.horizon_days)
        if res.impact_type not in CASH_TYPES:
            excluded.append(CompoundLine(**base, amount_usd_m=None, provenance=Provenance.DERIVED, included=False,
                            reason="Earnings proxy, not cash: excluded from cash totals"))
            continue
        if head is None or head.impact.raw is None:
            excluded.append(CompoundLine(**base, amount_usd_m=None, provenance=Provenance.UNAVAILABLE, included=False,
                            reason="NOT ASSESSABLE: required inputs missing"))
            continue
        if res.horizon_days != target:
            excluded.append(CompoundLine(**base, amount_usd_m=head.impact_usd_m, provenance=head.impact.provenance, included=False,
                            reason=f"Horizon {res.horizon_days} days differs from the compound horizon of {target} days"))
            continue
        usd = ctx.to_usd_m(D(head.impact.raw), head.impact.currency or "USD")
        if usd is None:
            excluded.append(CompoundLine(**base, amount_usd_m=None, provenance=Provenance.UNAVAILABLE, included=False,
                            reason="No sourced, dated conversion rate to USD"))
            continue
        amounts[module] = usd
        included.append(CompoundLine(**base, amount_usd_m=float(quantize(usd, 3)), provenance=head.impact.provenance, included=True))

    overlap_line = None
    notes = ["Totals are computed from unrounded component values, so they can differ from the sum of displayed figures in the last digit.",
             "Selected scenarios are summed as if they occur together; no covariance or cash-timing model is applied.",
             "Interest Mode A (earnings proxy) never enters the cash total."]
    if flag and "bunker" in amounts and "freight" in amounts:
        dp = D(results["bunker"].headline.shock)
        q, r, t = (_input(results["bunker"], k) for k in ("consumption_mt", "recovery_fraction", "cash_factor"))
        if None not in (q, r, t):
            recovery = dp * q * r * t                     # USD m of bunker cost already recovered via surcharges
            if dp > 0 and amounts["freight"] > 0 and recovery > 0:
                overlap = min(amounts["freight"], recovery)
                overlap_line = CompoundLine(module="overlap", label="Bunker surcharge recovery counted inside freight revenue",
                    impact_type="Double-count adjustment", horizon_days=target, amount_usd_m=float(quantize(-overlap, 3)),
                    provenance=Provenance.DERIVED, included=True,
                    reason="Bunker net cost is already after recovery r; the same recovery is removed from the freight proxy")
                amounts["overlap"] = -overlap
            else:
                notes.append("Bunker surcharge overlap checked: no adjustment (needs a bunker price rise and a freight gain).")
    elif not flag:
        notes.append("Surcharge overlap adjustment disabled by request; bunker recovery may be double counted if realised rates include surcharges.")

    total = sum(amounts.values(), Decimal(0)) if amounts else None
    adverse = None if total is None else max(Decimal(0), -total)
    limit_usd = None
    if scenario_req.limit is not None:
        limit_usd = ctx.to_usd_m(D(scenario_req.limit.amount), scenario_req.limit.currency)
    assessment = assess(th=ctx.thresholds, adverse_usd_m=adverse, impact_available=total is not None, comparable_to_liquidity=True,
                        liquidity_usd_m=ctx.liquidity.amount_usd_m, limit_usd_m=limit_usd, triggers=scenario_req.confirmed_triggers,
                        extra_missing=[] if included else ["no compatible cash components"])
    warnings = list(ctx.warnings)
    if excluded:
        warnings.append(f"{len(excluded)} component(s) excluded; see reasons")
    return CompoundResult(as_of=ctx.as_of, target_horizon_days=target, included=included, excluded=excluded,
                          overlap_adjustment=overlap_line, total_usd_m=None if total is None else float(quantize(total, 3)),
                          adverse_usd_m=None if adverse is None else float(quantize(adverse, 3)),
                          assessment=assessment, warnings=warnings, notes=notes)
