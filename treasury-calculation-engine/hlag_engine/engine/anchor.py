"""Anchor adjustment (spec 3.7, D69, D70), P79.

A zero-shock base pass on the anchor snapshot, then the T0 values swapped in one driver group at a time in the
fixed engine order (freight, bunker, rates, FX); the last step is the normal base pass at T0. A_k(t) = the
change in USD amounts of DELTA_ONLY lines on date t at step k; A(t) = the sum of the parts. BASE_AMOUNT lines
are excluded because their base amounts already use T0 prices. Labelled ASSUMPTION. With T0 prices equal to
the anchor, A = 0 (computed without the extra passes). While an anchor value is still a placeholder, A is not
computed: the keys are reported missing and the run is INCOMPLETE.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from hlag_engine.company_profile.schema import Profile
from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import BaselineTreatment
from hlag_engine.contracts.exposure import MissingInput
from hlag_engine.engine.liquidity import TreatmentFlags, line_treatment
from hlag_engine.modules.interface import SnapshotEntry

GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("freight", ("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE")),
    ("bunker", ("VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM")),  # Brent has no anchor; it stays at T0
    ("rates", ("SOFR", "ESTR", "BB_OAS", "UST_2Y", "EA_2Y")),
    ("fx", tuple(f"FX_USD_PER_{c}" for c in ("EUR", "CAD", "GBP", "CNY", "INR", "BRL"))),
)


@dataclass
class AnchorAdjustment:
    by_driver: dict[str, dict[date, Decimal]] = field(default_factory=dict)  # A_k(t)
    missing: list[MissingInput] = field(default_factory=list)
    computed: bool = False
    note: str = ""

    @property
    def total(self) -> dict[date, Decimal]:
        out: dict[date, Decimal] = defaultdict(Decimal)
        for part in self.by_driver.values():
            for d, a in part.items():
                out[d] += a
        return dict(out)


def anchor_values(profile: Profile, snapshot: dict[str, SnapshotEntry]) -> tuple[dict[str, float], list[MissingInput]]:
    values, missing = {}, []
    for _, series in GROUPS:
        for sid in series:
            if sid not in snapshot:
                continue
            entry = profile.anchor_snapshot.series.get(sid)
            if entry is None or entry.value is None:
                missing.append(MissingInput(key=f"anchor_snapshot.{sid}",
                                            status=entry.status.value if entry else "not_in_profile",
                                            affects="anchor adjustment (3.7)"))
            else:
                values[sid] = float(entry.value)
    return values, missing


def delta_only_by_date(lines: Sequence[CashLine], flags: TreatmentFlags) -> dict[date, Decimal]:
    out: dict[date, Decimal] = defaultdict(Decimal)
    for line in lines:
        if line_treatment(line, flags) is BaselineTreatment.DELTA_ONLY:
            out[line.date] += line.amount_usd_base
    return dict(out)


def anchor_adjustment(profile: Profile, snapshot: dict[str, SnapshotEntry], flags: TreatmentFlags,
                      t0_base_lines: Sequence[CashLine],
                      base_pass: Callable[[dict[str, SnapshotEntry]], tuple[list[CashLine], list[MissingInput]]]
                      ) -> AnchorAdjustment:
    """base_pass(snapshot) runs the zero-shock base pass on a snapshot and returns converted lines and errors."""
    anchors, missing = anchor_values(profile, snapshot)
    if missing:
        return AnchorAdjustment(missing=missing, note="anchor values pending: adjustment not computed")
    if all(anchors[s] == snapshot[s].value for s in anchors):
        return AnchorAdjustment({g: {} for g, _ in GROUPS}, computed=True, note="T0 prices equal the anchor: A = 0")
    step = {sid: replace(snapshot[sid], value=v, stale=False, filled=False) for sid, v in anchors.items()}
    step = {**snapshot, **step}
    lines, errors = base_pass(step)
    if errors:
        return AnchorAdjustment(missing=errors, note="a module could not run on the anchor snapshot")
    previous = delta_only_by_date(lines, flags)
    result = AnchorAdjustment(computed=True)
    for i, (group, series) in enumerate(GROUPS):
        step = {**step, **{s: snapshot[s] for s in series if s in snapshot}}
        if i == len(GROUPS) - 1:
            current = delta_only_by_date(t0_base_lines, flags)  # the last step is the normal base pass
        else:
            lines, errors = base_pass(step)
            if errors:
                return AnchorAdjustment(missing=errors, note="a module could not run on a swap step")
            current = delta_only_by_date(lines, flags)
        dates = set(previous) | set(current)
        result.by_driver[group] = {d: current.get(d, Decimal(0)) - previous.get(d, Decimal(0))
                                   for d in sorted(dates) if current.get(d, Decimal(0)) != previous.get(d, Decimal(0))}
        previous = current
    return result
