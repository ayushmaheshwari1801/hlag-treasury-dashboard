"""The daily job (spec 4.10, D66): load market data, evaluate Tier A rules, run the scheduled set, compute the daily
and weekly baseline changes. Deterministic apart from the data load, which the caller supplies (the engine package
makes no network calls; hlag_engine.data.cli wires the API connectors in)."""

from collections.abc import Callable
from datetime import date
from typing import Any

from hlag_engine.contracts.baseline_change import BaselineChangeWindow
from hlag_engine.engine.alerts import apply_data_alerts, apply_outcomes
from hlag_engine.engine.record import to_jsonable
from hlag_engine.engine.run import Engine
from hlag_engine.engine.scheduled import baseline_change, run_scheduled
from hlag_engine.engine.snapshot import build_snapshot, default_as_of
from hlag_engine.engine.tier_a import SeriesSource, evaluate_rules, market_monitor
from hlag_engine.company_profile.schema import load_profile
from hlag_engine.data.series import SERIES


def evaluate_tier_a(engine: Engine, as_of: date) -> dict:
    """Evaluate every module's rules, update the alerts, write auto-build requests, add the data alerts."""
    rules = [r for m in engine.modules for r in m.alert_rules()]
    playbook = [e for m in engine.modules for e in m.playbook()]
    source = SeriesSource(engine.store, as_of, engine.profile or load_profile())
    outcomes = evaluate_rules(rules, source)
    now = engine.clock()
    result = apply_outcomes(engine.store, as_of, outcomes, playbook, now)
    snapshot = build_snapshot(engine.store, as_of)
    material = sorted({s for m in engine.modules for s in m.material_series})
    stale = [s for s in material if s in snapshot and snapshot[s].stale] + [s for s in material if s not in snapshot]
    apply_data_alerts(engine.store, as_of, stale, now, result)
    monitor = market_monitor(source, list(SERIES))
    summary = to_jsonable({"as_of": as_of, "opened": result.opened, "updated": result.updated, "cleared": result.cleared,
                           "auto_build_requests": result.auto_build_requests, "not_evaluable": result.not_evaluable,
                           "market_monitor": monitor})
    engine.store.append_audit("tier_a", "tier_a_evaluated", f"tier_a:{as_of.isoformat()}", summary)
    return summary


def run_daily(engine: Engine, as_of: date | None = None,
              loader: Callable[[Any, date], Any] | None = None, load_date: date | None = None,
              preset_ids: list | None = None) -> dict:
    """preset_ids: the scheduled set (default: the standard set)."""
    out: dict[str, Any] = {}
    if loader is not None:
        report = loader(engine.store, load_date or as_of or date.today())
        out["load"] = {"stale_series": getattr(report, "stale_series", None), "paths": getattr(report, "paths", None)}
    t0 = as_of or default_as_of(engine.store)
    out["as_of"] = t0
    out["tier_a"] = evaluate_tier_a(engine, t0)
    scheduled = run_scheduled(engine, t0, preset_ids)
    out["scheduled"] = {"baseline": scheduled.baseline_run_id, "runs": scheduled.runs, "skipped": scheduled.skipped,
                        "failed": scheduled.failed,
                        "zim_pair": scheduled.zim_pair.pair_id if scheduled.zim_pair else None}
    out["baseline_change"] = {}
    for window in (BaselineChangeWindow.DAILY, BaselineChangeWindow.WEEKLY):
        outcome = baseline_change(engine, t0, window)
        out["baseline_change"][window.value] = outcome.change if outcome.change else {"skipped": outcome.skipped}
    return to_jsonable(out)
