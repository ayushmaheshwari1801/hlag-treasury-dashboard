"""Tier B scenario status, desk exceptions and their explanation objects (spec 4.6, 4.7), P80-P81.

Status from headroom consumed c (thresholds.yaml, tier_b): LOW below watch, WATCH, HIGH, CRITICAL at or above
critical, or when the scenario breaches the reserve, or when the baseline breaches it (H_b* <= 0, regardless).
Then: proxy-based raises LOW to WATCH; incomplete data (a stale or missing material series, a missing required
input) turns LOW or WATCH into INCOMPLETE and leaves HIGH or CRITICAL with the incomplete-data flag.
OPPORTUNITY when c <= the opportunity threshold.

Desk exceptions fire regardless of the net status: a driver's G_d >= 25% of H_b* (skipped when H_b* <= 0);
a margin-eligible shortfall; a stale or missing material series.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from hlag_engine.company_profile.scenario_validation import DEFAULT_THRESHOLDS_PATH
from hlag_engine.contracts.alert import AlertExplanation, AlertInput
from hlag_engine.contracts.enums import AlertTier, OwnerSpec, ProvenanceLabel, ScenarioStatus
from hlag_engine.contracts.run_summary import DeskException, DeskExceptionKind

M = Decimal(1_000_000)


@dataclass(frozen=True)
class TierB:
    watch_pct: float
    high_pct: float
    critical_pct: float
    opportunity_pct: float
    desk_exception_pct: float


def load_tier_b(path: Path | str = DEFAULT_THRESHOLDS_PATH) -> TierB:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)["tier_b"]
    return TierB(float(raw["watch_pct"]), float(raw["high_pct"]), float(raw["critical_pct"]),
                 float(raw["opportunity_pct"]), float(raw["desk_exception_pct"]))


@dataclass
class StatusResult:
    status: ScenarioStatus
    computed: ScenarioStatus  # before the proxy and incomplete rules
    opportunity: bool
    incomplete_data: bool
    reasons: list[str] = field(default_factory=list)


def tier_b_status(c_pct: Decimal | None, baseline_breach: bool, scenario_breach: bool, proxy_based: bool,
                  incomplete_reasons: list[str], t: TierB) -> StatusResult:
    reasons = []
    if baseline_breach:
        computed = ScenarioStatus.CRITICAL
        reasons.append("the baseline breaches the reserve (H_b* <= 0)")
    elif scenario_breach:
        computed = ScenarioStatus.CRITICAL
        reasons.append("the scenario breaches the reserve")
    elif c_pct >= Decimal(repr(t.critical_pct)):
        computed = ScenarioStatus.CRITICAL
    elif c_pct >= Decimal(repr(t.high_pct)):
        computed = ScenarioStatus.HIGH
    elif c_pct >= Decimal(repr(t.watch_pct)):
        computed = ScenarioStatus.WATCH
    else:
        computed = ScenarioStatus.LOW
    status = computed
    if proxy_based and status is ScenarioStatus.LOW:
        status = ScenarioStatus.WATCH
        reasons.append("proxy-based: material lines use PROXY prices (D4)")
    incomplete = bool(incomplete_reasons)
    if incomplete:
        reasons += incomplete_reasons
        if status in (ScenarioStatus.LOW, ScenarioStatus.WATCH):
            status = ScenarioStatus.INCOMPLETE
    opportunity = c_pct is not None and c_pct <= Decimal(repr(t.opportunity_pct))
    return StatusResult(status, computed, opportunity, incomplete, reasons)


def desk_exceptions(gross: dict, h_b: Decimal, t: TierB, margin_shortfalls: list[tuple[str, Decimal, date]],
                    stale_or_missing: list[str]) -> list[DeskException]:
    out = []
    if h_b > 0:
        for driver, g in gross.items():
            pct = g.value / h_b * 100
            if g.value > 0 and pct >= Decimal(repr(t.desk_exception_pct)):
                out.append(DeskException(kind=DeskExceptionKind.DRIVER, driver=OwnerSpec(driver),
                                         gross_adverse_usd_m=round(float(g.value / M), 3),
                                         pct_of_headroom=round(float(pct), 2), date=g.date))
    for pass_name, shortfall, when in margin_shortfalls:
        out.append(DeskException(kind=DeskExceptionKind.MARGIN, gross_adverse_usd_m=round(float(-shortfall / M), 3),
                                 date=when, detail=f"margin-eligible shortfall in the {pass_name} pass (4.4)"))
    if stale_or_missing:
        out.append(DeskException(kind=DeskExceptionKind.DATA, series=list(stale_or_missing),
                                 detail="stale or missing material series"))
    return out


def explanations(run_id: str, as_of: date, status: StatusResult, c_pct: Decimal | None, h_b: Decimal,
                 exceptions: list[DeskException], t: TierB, event_ids: list[str]) -> list[AlertExplanation]:
    out = []
    for i, ex in enumerate(exceptions, start=1):
        if ex.kind is DeskExceptionKind.DRIVER:
            alert_id = f"alert_{run_id}_desk_{ex.driver.value}"
            inputs = [AlertInput(name=f"G_{ex.driver.value}", value=ex.gross_adverse_usd_m, label=ProvenanceLabel.ASSUMPTION,
                                 as_of=as_of, unit="USD m"),
                      AlertInput(name="H_b*", value=round(float(h_b / M), 3), label=ProvenanceLabel.ASSUMPTION,
                                 as_of=as_of, unit="USD m")]
            out.append(AlertExplanation(
                alert_id=alert_id, tier=AlertTier.DESK_EXCEPTION, rule_id="desk_exception.gross_adverse",
                inputs=inputs, formula="G_d / H_b* x 100", threshold=t.desk_exception_pct,
                observed_value=ex.pct_of_headroom, reason_code="gross_adverse_delta",
                limitations=["G_d is measured at base FX on the driver's gross lines (4.3)"],
                event_ids=event_ids, run_id=run_id))
        elif ex.kind is DeskExceptionKind.MARGIN:
            out.append(AlertExplanation(
                alert_id=f"alert_{run_id}_desk_margin_{i}", tier=AlertTier.DESK_EXCEPTION,
                rule_id="desk_exception.margin_eligible", formula="T4(t) - cumulative MARGIN_CALL outflow",
                threshold=0, observed_value=-(ex.gross_adverse_usd_m or 0), reason_code="margin_eligible_shortfall",
                event_ids=event_ids, run_id=run_id))
        else:
            out.append(AlertExplanation(
                alert_id=f"alert_{run_id}_desk_data", tier=AlertTier.DESK_EXCEPTION, rule_id="desk_exception.data",
                formula="material series stale (days since print over the limit) or missing at T0",
                observed_value=", ".join(ex.series), reason_code="stale_or_missing_material_series",
                event_ids=event_ids, run_id=run_id))
    if status.status not in (ScenarioStatus.LOW,):
        out.append(AlertExplanation(
            alert_id=f"alert_{run_id}_status", tier=AlertTier.SCENARIO, status=status.status,
            inputs=[AlertInput(name="c", value=None if c_pct is None else round(float(c_pct), 2),
                               label=ProvenanceLabel.ASSUMPTION, as_of=as_of, unit="pct")],
            formula="c = (H_b* - H_s*) / H_b* x 100", threshold=f"watch {t.watch_pct}, high {t.high_pct}, "
                                                             f"critical {t.critical_pct}",
            observed_value=None if c_pct is None else round(float(c_pct), 2),
            reason_code=f"status_{status.status.value.lower()}", limitations=status.reasons,
            event_ids=event_ids, run_id=run_id))
    return out
