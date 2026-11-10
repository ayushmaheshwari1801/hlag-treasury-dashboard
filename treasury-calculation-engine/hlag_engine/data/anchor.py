"""Anchor snapshot values from loaded history (spec 3.7, D69, D70), P31.

An explicit administrator action, never run silently:

1. compute_anchor_values(store, profile) previews the values (read-only).
2. write_anchor_profile_version(...) writes them into a NEW profile file with a new
   profile_version. It never overwrites a file and never switches the active profile; that is
   the separate step hlag_engine.data.profile_admin.activate_profile_version (P32).

Targets are the profile's pending_history items:
- anchor_snapshot series: the anchor-period (H1 2026) average of real, confirmed prints
  (carry-forward rows are not averaged), labelled DERIVED as in the profile. History must cover
  the period: first print within the staleness limit of the start, last print within it of the
  end, and no gap between prints longer than the limit (daily 5 days, weekly 14).
- fx.lock_rate_usd_per_unit.INR and .BRL: the ECB reference rate on 31 Dec 2025 converted to USD
  per unit, labelled MARKET (9B.4); a real print on that date is required.

FX_USD_PER_EUR (DISCLOSED 1.1667, to_verify) is never changed; the ECB H1 average is reported
beside it as a check.
"""

import copy
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from hlag_engine.company_profile.schema import EntryStatus, Profile, active_profile_path, load_profile, profile_from_dict
from hlag_engine.data.series import MarketDataSettings, load_market_settings, spec_for
from hlag_engine.data.store import Store

LOCK_RATE_DATE = date(2025, 12, 31)  # 9B.4: INR and BRL policy hedges are locked at this ECB rate
LOCK_RATE_KEYS = {
    "fx.lock_rate_usd_per_unit.INR": "FX_USD_PER_INR",
    "fx.lock_rate_usd_per_unit.BRL": "FX_USD_PER_BRL",
}
EUR_CHECK_SERIES = "FX_USD_PER_EUR"


class AnchorError(Exception):
    pass


class AnchorCoverageError(AnchorError):
    def __init__(self, problems: dict[str, list[str]]):
        self.problems = problems
        lines = [f"{target}: {'; '.join(p)}" for target, p in problems.items()]
        super().__init__("history does not cover these targets: " + " | ".join(lines))


@dataclass
class AnchorValue:
    target: str  # anchor series ID or profile key
    series_id: str
    kind: str  # "average" or "lock_rate"
    value: float | None = None
    prints: int = 0
    first_print: date | None = None
    last_print: date | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def covered(self) -> bool:
        return self.value is not None and not self.problems


@dataclass
class AnchorComputation:
    period: str
    start: date
    end: date
    values: dict[str, AnchorValue]
    eur_check: dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)


def round_sig(value: float, digits: int = 10) -> float:
    return float(f"{value:.{digits}g}")


def _average(store: Store, series_id: str, start: date, end: date, settings: MarketDataSettings) -> AnchorValue:
    out = AnchorValue(series_id, series_id, "average")
    spec = spec_for(series_id)
    limit = settings.stale_after(spec)
    rows = store.get_series(series_id, start, end, include_filled=False)
    if not rows:
        out.problems.append(f"no loaded history between {start} and {end}")
        return out
    out.prints, out.first_print, out.last_print = len(rows), rows[0].date, rows[-1].date
    unconfirmed = [r.date.isoformat() for r in rows if not r.confirmed]
    if unconfirmed:
        out.problems.append(f"unconfirmed values in the period: {', '.join(unconfirmed[:5])}")
    if (rows[0].date - start).days > limit:
        out.problems.append(f"history starts {rows[0].date}, more than {limit} days after {start}")
    if (end - rows[-1].date).days > limit:
        out.problems.append(f"history ends {rows[-1].date}, more than {limit} days before {end}")
    for a, b in zip(rows, rows[1:]):
        if (b.date - a.date).days > limit:
            out.problems.append(f"gap of {(b.date - a.date).days} days between {a.date} and {b.date}")
    out.value = round_sig(sum(r.value for r in rows) / len(rows))
    return out


def _lock_rate(store: Store, key: str, series_id: str) -> AnchorValue:
    out = AnchorValue(key, series_id, "lock_rate")
    row = store.get_market_row(series_id, LOCK_RATE_DATE)
    if row is None or row.filled:
        out.problems.append(f"no published {series_id} value on {LOCK_RATE_DATE}")
        return out
    if not row.confirmed:
        out.problems.append(f"{series_id} on {LOCK_RATE_DATE} is unconfirmed")
    out.value, out.prints, out.first_print, out.last_print = round_sig(row.value), 1, row.date, row.date
    return out


def pending_targets(profile: Profile) -> dict[str, str]:
    """{target: kind} for every pending_history item this action can fill."""
    targets = {
        sid: "average"
        for sid, s in profile.anchor_snapshot.series.items()
        if s.status is EntryStatus.PENDING_HISTORY
    }
    for key in LOCK_RATE_KEYS:
        entry = profile.parameters.get(key)
        if entry is not None and entry.status is EntryStatus.PENDING_HISTORY:
            targets[key] = "lock_rate"
    return targets


def compute_anchor_values(
    store: Store, profile: Profile, settings: MarketDataSettings | None = None
) -> AnchorComputation:
    """Preview: the value and coverage of every pending_history target. Writes nothing."""
    settings = settings or load_market_settings()
    snap = profile.anchor_snapshot
    values: dict[str, AnchorValue] = {}
    for target, kind in pending_targets(profile).items():
        if kind == "average":
            values[target] = _average(store, target, snap.start, snap.end, settings)
        else:
            values[target] = _lock_rate(store, target, LOCK_RATE_KEYS[target])
    eur = _average(store, EUR_CHECK_SERIES, snap.start, snap.end, settings)
    disclosed = snap.series[EUR_CHECK_SERIES].value if EUR_CHECK_SERIES in snap.series else None
    eur_check = {
        "profile_value": disclosed,
        "ecb_average": eur.value,
        "difference": None if eur.value is None or disclosed is None else round_sig(eur.value - disclosed, 6),
        "problems": eur.problems,
        "note": "FX_USD_PER_EUR is DISCLOSED (to_verify) and is not changed by this action",
    }
    return AnchorComputation(snap.period, snap.start, snap.end, values, eur_check)


# ---------------------------------------------------------------- writing a new profile version


def _replace_once(pattern: str, repl, text: str, what: str) -> str:
    new, count = re.subn(pattern, repl, text, count=0, flags=re.MULTILINE)
    if count != 1:
        raise AnchorError(f"expected exactly one {what} in the profile file, found {count}")
    return new


def _set_version(text: str, version: str) -> str:
    return _replace_once(r"^profile_version:.*$", f'profile_version: "{version}"', text, "profile_version line")


def _fill_anchor_line(text: str, series_id: str, value: float) -> str:
    def repl(m: re.Match) -> str:
        body = m.group(2)
        if body.count("value: null") != 1 or body.count("status: pending_history") != 1:
            raise AnchorError(f"anchor line for {series_id} is not a pending_history placeholder")
        body = body.replace("value: null", f"value: {value!r}").replace("status: pending_history", "status: ok")
        return f"{m.group(1)}{series_id}: {{{body}}}"

    return _replace_once(rf"^(\s+){re.escape(series_id)}:\s*\{{(.*)\}}\s*$", repl, text, f"anchor line {series_id}")


def _fill_param_block(text: str, key: str, value: float, as_of: date) -> str:
    lines = text.split("\n")
    heads = [i for i, line in enumerate(lines) if line.rstrip() == f"  {key}:"]
    if len(heads) != 1:
        raise AnchorError(f"expected exactly one block for {key}, found {len(heads)}")
    i = heads[0] + 1
    block_end = i
    while block_end < len(lines) and lines[block_end].startswith("    "):
        block_end += 1
    block = lines[i:block_end]
    if block.count("    value: null") != 1 or block.count("    status: pending_history") != 1:
        raise AnchorError(f"{key} is not a pending_history placeholder")
    if any(line.startswith("    as_of:") for line in block):
        raise AnchorError(f"{key} already has an as_of line")
    new_block = []
    for line in block:
        if line == "    value: null":
            new_block += [f"    value: {value!r}", f"    as_of: {as_of.isoformat()}"]
        elif line == "    status: pending_history":
            new_block.append("    status: ok")
        else:
            new_block.append(line)
    return "\n".join(lines[:i] + new_block + lines[block_end:])


@dataclass
class AnchorWriteResult:
    source_path: str
    out_path: str
    profile_version: str
    content_hash: str
    values: dict[str, float]
    left_pending: list[str]
    eur_check: dict[str, Any]


def write_anchor_profile_version(
    store: Store,
    *,
    out_path: Path | str,
    new_version: str,
    actor: str,
    source_path: Path | str | None = None,
    targets: list[str] | None = None,
    settings: MarketDataSettings | None = None,
) -> AnchorWriteResult:
    """Write a new profile file with the anchor values filled (administrator action, P31).

    All pending targets must be covered by loaded history unless `targets` explicitly names a
    subset; the others then stay pending_history. Writes one audit entry.
    """
    source = Path(source_path) if source_path is not None else active_profile_path()
    out = Path(out_path)
    if out.exists():
        raise AnchorError(f"{out} already exists; a profile version is never overwritten")
    if out.resolve() == source.resolve():
        raise AnchorError("out_path must differ from the source profile")
    profile = load_profile(source)
    if new_version == profile.profile_version:
        raise AnchorError(f"new_version must differ from the source version {profile.profile_version}")

    computation = compute_anchor_values(store, profile, settings)
    pending = pending_targets(profile)
    chosen = list(pending) if targets is None else list(targets)
    unknown = [t for t in chosen if t not in pending]
    if unknown:
        raise AnchorError(f"not pending_history targets: {unknown}")
    if not chosen:
        raise AnchorError("nothing to fill: no pending_history targets")
    uncovered = {t: computation.values[t].problems or ["no value"] for t in chosen if not computation.values[t].covered}
    if uncovered:
        raise AnchorCoverageError(uncovered)

    text = source.read_text(encoding="utf-8")
    old_raw = yaml.safe_load(text)
    expected = copy.deepcopy(old_raw)
    expected["profile_version"] = new_version
    text = _set_version(text, new_version)
    values: dict[str, float] = {}
    for target in chosen:
        value = computation.values[target].value
        assert value is not None
        values[target] = value
        if pending[target] == "average":
            text = _fill_anchor_line(text, target, value)
            expected["anchor_snapshot"]["series"][target].update(value=value, status="ok")
        else:
            text = _fill_param_block(text, target, value, LOCK_RATE_DATE)
            expected["parameters"][target].update(value=value, status="ok", as_of=LOCK_RATE_DATE)

    new_raw = yaml.safe_load(text)
    if new_raw != expected:
        raise AnchorError("internal check failed: the new file differs from the source in more than the targets")
    new_profile = profile_from_dict(new_raw)  # full validation

    out.write_text(text, encoding="utf-8")
    result = AnchorWriteResult(
        source_path=str(source), out_path=str(out), profile_version=new_version,
        content_hash=new_profile.content_hash, values=values,
        left_pending=[t for t in pending if t not in chosen], eur_check=computation.eur_check,
    )
    store.append_audit(actor, "profile_version_written", new_version, asdict(result))
    return result
