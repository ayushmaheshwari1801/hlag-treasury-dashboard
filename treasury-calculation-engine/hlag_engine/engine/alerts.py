"""Tier A alert lifecycle, data alerts and auto-build requests (spec 4.5, 4.7), P82-P84.

One active alert per rule (or per series for data alerts). A rule that fires opens an alert, or updates the
active one (its observed values; severity may escalate); a rule that is evaluable and no longer fires clears it.
A rule that is not evaluable leaves its alert as it is. A severe alert whose rule has on_severe =
request_auto_build writes an AutoBuildRequest, at most one per rule per day. Acknowledging is a dashboard function
for people only and MUST NOT be exposed as an agent tool (4.8).
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from hlag_engine.contracts.alert import Alert, AlertExplanation, AlertInput
from hlag_engine.contracts.alert_rule import AlertRule, AlertRuleType, OnSevere
from hlag_engine.contracts.auto_build import AutoBuildRequest
from hlag_engine.contracts.enums import AlertSeverity, AlertState, AlertTier, ProvenanceLabel
from hlag_engine.contracts.playbook import PlaybookEntry
from hlag_engine.data.store import Store, StoreError
from hlag_engine.engine.tier_a import Outcome

HUMAN_ONLY_FUNCTIONS = ("acknowledge_alert",)
FORMULAS = {
    AlertRuleType.PCT_MOVE: "(latest / previous - 1) x 100 over the window; or |log change| / vol over the lookback",
    AlertRuleType.BP_MOVE: "(latest - previous) x 100 over the window (series in percent)",
    AlertRuleType.SPREAD_CHANGE: "change over the window of series A - series B",
    AlertRuleType.DIVERGENCE: "% change of series A minus % change of series B over the window (pp)",
    AlertRuleType.VOL_MULTIPLE: "|latest log change| / standard deviation of log changes over the lookback",
    AlertRuleType.VOL_REGIME: "short-window / long-window volatility: elevated from 1.25, stressed above 2.0",
}
RANK = {None: 0, AlertSeverity.WARNING: 1, AlertSeverity.SEVERE: 2}


@dataclass
class EvaluationResult:
    as_of: date
    opened: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)
    auto_build_requests: list[int] = field(default_factory=list)
    not_evaluable: list[dict] = field(default_factory=list)


def _threshold_text(rule: AlertRule) -> str:
    def level(t):
        parts = [f"{t.value:g} {rule.unit}"] if t.value is not None else []
        parts += [f"{t.vol_multiple:g}x vol"] if t.vol_multiple is not None else []
        return " or ".join(parts)
    return f"warning {level(rule.thresholds.warning)}; severe {level(rule.thresholds.severe)}"


def _explanation(alert_id: str, o: Outcome, as_of: date, playbook: list[PlaybookEntry]) -> AlertExplanation:
    refs = [e.entry_id for e in playbook if e.trigger_type.value == "alert_rule" and o.rule.rule_id.startswith(e.trigger)]
    limitations = ["the realised rate is the model's estimate"] if any(s.startswith("FREIGHT_REALISED") for s in o.rule.series) else []
    if o.unconfirmed:
        limitations.append("fired on an unconfirmed retrieved value (D8)")
    return AlertExplanation(
        alert_id=alert_id, tier=AlertTier.MARKET, rule_id=o.rule.rule_id, severity=o.severity,
        inputs=[AlertInput(name=name, value=value, label=label, as_of=d) for name, value, d, label in o.inputs]
        + ([AlertInput(name="vol_multiple", value=round(o.vol_multiple, 4), label=ProvenanceLabel.MARKET, as_of=as_of)]
           if o.vol_multiple is not None else []),
        formula=FORMULAS[o.rule.type], threshold=_threshold_text(o.rule),
        observed_value=None if o.observed is None else round(o.observed, 6), reason_code=o.rule.type.value,
        limitations=limitations, playbook_refs=refs, unconfirmed=o.unconfirmed,
    )


def apply_outcomes(store: Store, as_of: date, outcomes: list[Outcome], playbook: list[PlaybookEntry],
                   now: datetime) -> EvaluationResult:
    result = EvaluationResult(as_of)
    for o in outcomes:
        key = o.rule.rule_id
        active = store.active_alert(key)
        if not o.evaluable:
            result.not_evaluable.append({"rule_id": key, "reason": o.reason})
            continue
        if o.severity is None:
            if active is not None:
                cleared = active.alert.model_copy(update={"state": AlertState.CLEARED, "cleared_at": now})
                store.save_alert(cleared, key, as_of)
                store.append_audit("tier_a", "alert_cleared", cleared.alert_id, {"rule_id": key})
                result.cleared.append(cleared.alert_id)
            continue
        if active is None:
            alert_id = f"alert_tierA_{key}_{as_of:%Y%m%d}"
            alert = Alert(explanation=_explanation(alert_id, o, as_of, playbook), opened_at=now)
            store.save_alert(alert, key, as_of)
            store.append_audit("tier_a", "alert_opened", alert_id, {"rule_id": key, "severity": o.severity.value})
            result.opened.append(alert_id)
        else:
            alert_id = active.alert.alert_id
            severity = max(o.severity, active.alert.explanation.severity, key=lambda s: RANK[s])
            explanation = _explanation(alert_id, o, as_of, playbook).model_copy(update={"severity": severity})
            store.save_alert(active.alert.model_copy(update={"explanation": explanation}), key, as_of)
            result.updated.append(alert_id)
        if o.severity is AlertSeverity.SEVERE and o.rule.on_severe is OnSevere.REQUEST_AUTO_BUILD:
            request = AutoBuildRequest(rule_id=key, series=list(o.rule.series), move=o.observed or 0.0,
                                       unit=o.rule.unit, observed_on=as_of, alert_id=alert_id)
            item = store.enqueue_tier_a_request(key, as_of, request.model_dump(mode="json"))
            if item is not None:
                result.auto_build_requests.append(item)
    return result


def apply_data_alerts(store: Store, as_of: date, stale_series: list[str], now: datetime,
                      result: EvaluationResult) -> None:
    """'possible major move, pending confirmation' (D31) and 'stale material series' (4.5)."""
    wanted: dict[str, AlertExplanation] = {}
    for item in store.list_queue("pending"):
        if item.major_move_alert:
            key = f"data:major_move:{item.series_id}:{item.date.isoformat()}"
            wanted[key] = AlertExplanation(
                alert_id=f"alert_data_major_move_{item.series_id}_{item.date:%Y%m%d}", tier=AlertTier.MARKET,
                rule_id="data.possible_major_move", severity=AlertSeverity.WARNING,
                inputs=[AlertInput(name=item.series_id, value=item.value, label=ProvenanceLabel.MARKET, as_of=item.date,
                                   unit=item.unit)],
                formula="week-on-week move beyond the plausibility band (3.6, D31)", observed_value=item.value,
                reason_code="possible_major_move_pending_confirmation", unconfirmed=True,
                limitations=["possible major move, pending confirmation"])
    for sid in stale_series:
        key = f"data:stale:{sid}"
        wanted[key] = AlertExplanation(
            alert_id=f"alert_data_stale_{sid}_{as_of:%Y%m%d}", tier=AlertTier.MARKET, rule_id="data.stale_material_series",
            severity=AlertSeverity.WARNING, formula="days since the last print over the series' staleness limit (3.6)",
            observed_value=sid, reason_code="stale_material_series")
    for key, explanation in wanted.items():
        active = store.active_alert(key)
        if active is None:
            store.save_alert(Alert(explanation=explanation, opened_at=now), key, as_of)
            result.opened.append(explanation.alert_id)
        else:
            result.updated.append(active.alert.alert_id)
    for stored in store.list_alerts():
        if stored.active_key.startswith("data:") and stored.alert.state is not AlertState.CLEARED \
                and stored.active_key not in wanted:
            store.save_alert(stored.alert.model_copy(update={"state": AlertState.CLEARED, "cleared_at": now}),
                             stored.active_key, as_of)
            result.cleared.append(stored.alert.alert_id)


def acknowledge_alert(store: Store, alert_id: str, user: str, now: datetime) -> Alert:
    """Human-only (dashboard): mark an open alert as seen by a named user."""
    stored = store.get_alert(alert_id)
    if stored is None:
        raise StoreError(f"unknown alert {alert_id}")
    if stored.alert.state is not AlertState.OPEN:
        raise StoreError(f"alert {alert_id} is {stored.alert.state.value}, not open")
    alert = stored.alert.model_copy(update={"state": AlertState.ACKNOWLEDGED, "acknowledged_by": user,
                                            "acknowledged_at": now})
    store.save_alert(alert, stored.active_key, stored.updated_on)
    store.append_audit(user, "alert_acknowledged", alert_id, {})
    return alert
