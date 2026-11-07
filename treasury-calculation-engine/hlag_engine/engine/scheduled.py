"""Scheduled runs and the baseline change (spec 4.10, D66, D71), P85-P86. Deterministic: no LLM.

run_scheduled: after each daily data load, the baseline plus the standard set (red_sea_reescalation, demand_slump,
oil_spike, fed_cutting_cycle, eur_rally, em_stress and the sensitivity family), source = scheduled, stored. The
baseline is run as a ZIM on/off pair (run A as the profile has it, run B with the switch flipped), so the dashboard
can show ZIM both ways. A preset that cannot be built (missing inputs) or fails is listed, never fatal.

baseline_change: today's anchored baseline against an earlier scheduled baseline. Step 1: today's T0 on the earlier
run's stored market snapshot, anchor adjustment included; other changes = its minimum headroom minus the earlier
baseline's (time roll, adjustments, profile updates). Step 2: today's snapshot swapped in one driver group at a time
in the fixed order (freight, bunker, rates, FX), recomputing the anchored baseline; each step's change is that
driver's effect, and the last step is today's baseline. The parts telescope, so they sum exactly to the total.
Windows: daily (the previous scheduled baseline) and weekly (the latest scheduled baseline at least 7 days earlier;
the brief and the dashboard headline use it, D67). Limitation: within the driver steps the anchor term cancels only
while the date of the minimum stays put, because the minimum is not linear; the split depends on the fixed order.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from hlag_engine.company_profile.registers import load_registers
from hlag_engine.company_profile.resolve import RunContext, resolve
from hlag_engine.company_profile.schema import load_profile
from hlag_engine.contracts.baseline_change import BaselineChange, BaselineChangeWindow, DriverChange
from hlag_engine.contracts.enums import PresetId, ScenarioSource
from hlag_engine.contracts.pair_result import PairResult
from hlag_engine.contracts.run_record import RunRecord
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.engine.presets import STANDARD_SET, build_preset
from hlag_engine.engine.record import to_jsonable
from hlag_engine.engine.run import Engine, snapshot_from_record
from hlag_engine.engine.snapshot import default_as_of
from hlag_engine.modules.interface import ProfileContext

BASELINE_NAME = "Scheduled baseline"
SWAP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("freight", ("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE")),
    ("bunker", ("VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM", "BRENT")),
    ("rates", ("SOFR", "ESTR", "BB_OAS", "UST_2Y", "EA_2Y")),
    ("fx", tuple(f"FX_USD_PER_{c}" for c in ("EUR", "CAD", "GBP", "CNY", "INR", "BRL"))),
)
M = Decimal(1_000_000)
TOLERANCE_USD = Decimal("0.01")


@dataclass
class ScheduledResult:
    as_of: date
    baseline_run_id: str | None = None
    zim_pair: PairResult | None = None
    runs: dict[str, str] = field(default_factory=dict)
    skipped: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


def baseline_params(as_of: date) -> ScenarioParams:
    return ScenarioParams(name=BASELINE_NAME, start_date=as_of + timedelta(days=1), source=ScenarioSource.SCHEDULED)


def run_scheduled(engine: Engine, as_of: date | None = None, preset_ids: list[PresetId] | None = None,
                  with_zim_pair: bool = True) -> ScheduledResult:
    from hlag_engine.engine.compare import run_pair

    as_of = as_of or default_as_of(engine.store)
    result = ScheduledResult(as_of)
    profile = engine.profile or load_profile()
    registers = engine.registers or load_registers()
    base = baseline_params(as_of)
    if with_zim_pair:
        resolver = RunContext.for_scenario(profile, as_of, base, tuple(engine._adjustments(base)), registers)
        zim_now = bool(resolve("rates.zim_tranche_on", as_of, resolver))
        pair = run_pair(engine, base, {"zim_tranche_on": not zim_now}, as_of)
        result.baseline_run_id, result.zim_pair = pair.run_a.run_id, pair.result
    else:
        result.baseline_run_id = engine.run_scenario(base, as_of).run_id
    ctx = ProfileContext(RunContext(profile, as_of, tuple(engine._adjustments(base)), registers=registers))
    for preset in (STANDARD_SET if preset_ids is None else preset_ids):
        params, missing = build_preset(preset, ctx, ScenarioSource.SCHEDULED)
        if params is None:
            result.skipped.append({"preset": preset.value, "missing": [m.model_dump() for m in missing]})
            continue
        try:
            result.runs[preset.value] = engine.run_scenario(params, as_of).run_id
        except Exception as exc:  # listed, never fatal for the scheduled job
            result.failed.append({"preset": preset.value, "error": f"{type(exc).__name__}: {exc}"})
    engine.store.append_audit("scheduler", "scheduled_runs_completed", f"scheduled:{as_of.isoformat()}",
                              to_jsonable({"baseline": result.baseline_run_id, "runs": result.runs,
                                           "skipped": result.skipped, "failed": result.failed}))
    return result


# ---------------------------------------------------------------- baseline change (D71)


def scheduled_baselines(engine: Engine) -> list[RunRecord]:
    """Scheduled baseline run A records (not the ZIM-flipped run B), oldest first."""
    rows = engine.store.conn.execute(
        "SELECT record_json FROM run_records WHERE record_json LIKE ? ORDER BY as_of, created_at",
        (f'%"name":"{BASELINE_NAME}"%',),
    ).fetchall()
    out = []
    for r in rows:
        rec = RunRecord.model_validate_json(r["record_json"])
        if rec.params.source is not ScenarioSource.SCHEDULED or rec.params.name != BASELINE_NAME:
            continue
        if rec.pair_id is not None and rec.run_id != _pair_first(engine, rec.pair_id):
            continue
        out.append(rec)
    return out


def _pair_first(engine: Engine, pair_id: str) -> str:
    row = engine.store.conn.execute(
        "SELECT run_id FROM run_records WHERE pair_id = ? ORDER BY created_at, run_id LIMIT 1", (pair_id,)).fetchone()
    return row["run_id"]


def _h_b(record: RunRecord) -> Decimal:
    return Decimal(record.results["liquidity"]["h_b_min_usd"])


@dataclass
class ChangeOutcome:
    window: BaselineChangeWindow
    change: BaselineChange | None = None
    skipped: str | None = None


def baseline_change(engine: Engine, as_of: date, window: BaselineChangeWindow, store_result: bool = True) -> ChangeOutcome:
    baselines = scheduled_baselines(engine)
    today = [b for b in baselines if b.as_of == as_of]
    if not today:
        return ChangeOutcome(window, skipped=f"no scheduled baseline for {as_of}; run the scheduled set first")
    current = today[-1]
    cutoff = as_of - timedelta(days=7) if window is BaselineChangeWindow.WEEKLY else as_of - timedelta(days=1)
    earlier = [b for b in baselines if b.as_of <= cutoff]
    if not earlier:
        need = "at least 7 days old" if window is BaselineChangeWindow.WEEKLY else "from an earlier date"
        return ChangeOutcome(window, skipped=f"no scheduled baseline {need}")
    previous = earlier[-1]
    prev_snapshot = snapshot_from_record(previous)
    today_snapshot = snapshot_from_record(current)

    step, _ = engine.anchored_baseline(as_of, prev_snapshot)
    other = step - _h_b(previous)
    snapshot = dict(prev_snapshot)
    parts: dict[str, Decimal] = {}
    for group, series in SWAP_GROUPS:
        for sid in series:
            if sid in today_snapshot:
                snapshot[sid] = today_snapshot[sid]
            else:
                snapshot.pop(sid, None)
        value, _ = engine.anchored_baseline(as_of, snapshot)
        parts[group] = value - step
        step = value
    total = _h_b(current) - _h_b(previous)
    if abs(step - _h_b(current)) > TOLERANCE_USD:
        raise ValueError(f"the last swap step ({step}) differs from today's baseline ({_h_b(current)})")
    if abs(other + sum(parts.values()) - total) > TOLERANCE_USD:
        raise ValueError("baseline change parts do not sum to the total")
    total = other + sum(parts.values())  # exactly the parts' sum (the check above bounds it against the records)
    change = BaselineChange(
        window=window, date=as_of, previous_run_id=previous.run_id, current_run_id=current.run_id,
        total_usd_m=float(total / M), by_driver_usd_m=DriverChange(**{k: float(v / M) for k, v in parts.items()}),
        other_usd_m=float(other / M),
    )
    if store_result:
        engine.store.save_baseline_change(change)
    return ChangeOutcome(window, change)
