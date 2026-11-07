"""Loader for the hand-collected weekly history (spec 3.6), P30.

Accepts exactly this format: header `series_id,date,value,unit,source_url,notes`; series
WCI_SHA_RTM, WCI_SHA_LAX, WCI_RTM_NYC, WCI_COMPOSITE (USD/FEU) and VLSFO_SIN, VLSFO_RTM,
HSFO_SIN, HSFO_RTM (USD/t); dates YYYY-MM-DD.

- Any bad row rejects the whole file; nothing is written and every problem is listed with its
  line number. The rejection is written to the audit log.
- Rows are stored with label MARKET and confirmed = true (a person collected them).
- source_url cites the source: an http(s) link, or a Bloomberg Terminal reference of the form
  `Bloomberg Terminal, <ticker> Index (<field>)`, e.g. `Bloomberg Terminal, WCIDCOMP Index (PX388 INTERVAL_AVG)`
  (P89). It is stored as source_ref.
- A week-on-week move beyond the plausibility band is a warning in the load report and in the
  audit log, not a confirm-queue entry (the queue is for agent-retrieved values).
- Loading the same file again is a no-op. A row whose value differs from a stored value for the
  same series and date is an error unless replace=True.
"""

import csv
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from hlag_engine.contracts.enums import ProvenanceLabel
from hlag_engine.data.checks import (
    MISSING_VALUE,
    days_since_print,
    is_stale,
    move_pct,
    outside_band,
    value_checks,
)
from hlag_engine.data.series import AGENT_SERIES, MarketDataSettings, load_market_settings, spec_for
from hlag_engine.data.store import MarketRow, Store

HEADER = ["series_id", "date", "value", "unit", "source_url", "notes"]
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TERMINAL_REF = re.compile(r"^Bloomberg Terminal, [A-Z0-9]+ Index \([^()]+\)$")  # P89
SOURCE = "hand-collected CSV"


class HistoryCSVError(Exception):
    def __init__(self, path: str, errors: list[str]):
        self.path, self.errors = path, errors
        super().__init__(f"{path}: {len(errors)} problem(s): " + "; ".join(errors[:10]))


@dataclass
class BandWarning:
    line: int
    series_id: str
    date: date
    value: float
    previous_date: date
    previous_value: float
    move_pct: float


@dataclass
class HistoryLoadReport:
    path: str
    rows_read: int
    rows_written: int
    rows_unchanged: int
    by_series: dict[str, int]
    last_print: dict[str, date]
    stale_series: list[str] = field(default_factory=list)  # only when as_of is given
    warnings: list[BandWarning] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Row:
    line: int
    series_id: str
    date: date
    value: float
    unit: str
    source_url: str


def is_source_ref(text: str) -> bool:
    """An http(s) link or a Bloomberg Terminal reference (P89)."""
    return text.startswith(("http://", "https://")) or TERMINAL_REF.match(text) is not None


def _parse(path: Path) -> tuple[list[_Row], list[str]]:
    errors: list[str] = []
    rows: list[_Row] = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            return [], ["empty file: expected the header " + ",".join(HEADER)]
        if [h.strip() for h in header] != HEADER:
            return [], [f"wrong header {header}: expected exactly " + ",".join(HEADER)]
        seen: set[tuple[str, date]] = set()
        for line_no, cells in enumerate(reader, start=2):
            if not cells or all(not c.strip() for c in cells):
                continue
            if len(cells) != len(HEADER):
                errors.append(f"line {line_no}: wrong_column_count: {len(cells)} columns, expected {len(HEADER)}")
                continue
            sid, day, raw_value, unit, url, _notes = (c.strip() for c in cells)
            problems: list[str] = []
            if sid not in AGENT_SERIES:
                problems.append(f"unknown_series: {sid!r} (expected one of {', '.join(AGENT_SERIES)})")
            parsed_date = None
            if not DATE_PATTERN.match(day):
                problems.append(f"invalid_date: {day!r} (expected YYYY-MM-DD)")
            else:
                try:
                    parsed_date = date.fromisoformat(day)
                except ValueError:
                    problems.append(f"invalid_date: {day!r}")
            value: float | None = None
            if raw_value:
                try:
                    value = float(raw_value)
                    if not math.isfinite(value):
                        problems.append(f"invalid_value: {raw_value!r}")
                except ValueError:
                    problems.append(f"invalid_value: {raw_value!r}")
            if not is_source_ref(url):
                problems.append(
                    "missing_source_url: every value must cite an http(s) source_url or a Bloomberg Terminal"
                    " reference ('Bloomberg Terminal, <ticker> Index (<field>)')"
                )
            if sid in AGENT_SERIES and not any(p.startswith("invalid_value") for p in problems):
                for reason in value_checks(spec_for(sid), value, unit):
                    detail = {
                        MISSING_VALUE: "value is empty",
                        "non_positive_value": f"{value} is not positive",
                        "wrong_unit": f"{unit!r}, expected {spec_for(sid).unit}",
                    }[reason]
                    problems.append(f"{reason}: {detail}")
            if parsed_date is not None and sid in AGENT_SERIES:
                if (sid, parsed_date) in seen:
                    problems.append(f"duplicate_row: {sid} {parsed_date} appears more than once")
                seen.add((sid, parsed_date))
            if problems:
                errors += [f"line {line_no}: {p}" for p in problems]
            else:
                rows.append(_Row(line_no, sid, parsed_date, value, unit, url))  # type: ignore[arg-type]
    return rows, errors


def load_history_csv(
    store: Store,
    path: Path | str,
    *,
    actor: str = "admin",
    replace: bool = False,
    as_of: date | None = None,
    settings: MarketDataSettings | None = None,
) -> HistoryLoadReport:
    settings = settings or load_market_settings()
    path = Path(path)
    rows, errors = _parse(path)

    to_write: list[_Row] = []
    unchanged = 0
    for row in rows:
        stored = store.get_market_row(row.series_id, row.date)
        if stored is None:
            to_write.append(row)
        elif stored.value == row.value and stored.confirmed and not stored.filled:
            unchanged += 1
        elif replace or stored.value == row.value:  # same value from a person confirms it
            to_write.append(row)
        else:
            errors.append(
                f"line {row.line}: conflicts_with_stored: {row.series_id} {row.date} is stored as {stored.value}"
                f" ({stored.source}); pass replace=True to overwrite"
            )
    if errors:
        store.append_audit(actor, "history_csv_rejected", str(path), {"errors": errors})
        raise HistoryCSVError(str(path), errors)

    # Band warnings against the previous print, from this file or the store (P25, P30).
    warnings: list[BandWarning] = []
    file_values = {(r.series_id, r.date): r for r in rows}
    for row in sorted(rows, key=lambda r: (r.series_id, r.date)):
        earlier = [r for (s, d), r in file_values.items() if s == row.series_id and d < row.date]
        prev_file = max(earlier, key=lambda r: r.date) if earlier else None
        prev_store = store.latest_print_before(row.series_id, row.date)
        prev = None
        if prev_file and (prev_store is None or prev_file.date >= prev_store.date):
            prev = (prev_file.date, prev_file.value)
        elif prev_store:
            prev = (prev_store.date, prev_store.value)
        spec = spec_for(row.series_id)
        if prev and outside_band(spec, settings, row.value, prev[1]):
            warnings.append(
                BandWarning(row.line, row.series_id, row.date, row.value, prev[0], prev[1], round(move_pct(row.value, prev[1]), 4))
            )

    retrieved_at = store.now()
    with store.transaction():
        store.upsert_market_rows(
            [
                MarketRow(
                    series_id=r.series_id, date=r.date, value=r.value, unit=spec_for(r.series_id).unit, source=SOURCE,
                    label=ProvenanceLabel.MARKET, retrieved_at=retrieved_at, filled=False, days_since_print=0,
                    confirmed=True, source_ref=r.source_url,
                )
                for r in to_write
            ]
        )
        by_series: dict[str, int] = {}
        for r in rows:
            by_series[r.series_id] = by_series.get(r.series_id, 0) + 1
        last_print = {}
        stale: list[str] = []
        for sid in by_series:
            latest = store.get_series(sid, include_filled=False)[-1]
            last_print[sid] = latest.date
            if as_of is not None and is_stale(spec_for(sid), settings, days_since_print(latest.date, as_of)):
                stale.append(sid)
        report = HistoryLoadReport(
            path=str(path), rows_read=len(rows), rows_written=len(to_write), rows_unchanged=unchanged,
            by_series=by_series, last_print=last_print, stale_series=stale, warnings=warnings,
        )
        store.append_audit(
            actor, "history_csv_load", str(path),
            {"rows_read": len(rows), "rows_written": len(to_write), "rows_unchanged": unchanged,
             "by_series": by_series, "replace": replace, "warnings": len(warnings)},
        )
        for w in warnings:
            store.append_audit(
                actor, "history_band_warning", f"{w.series_id}:{w.date.isoformat()}",
                {"line": w.line, "value": w.value, "previous_date": w.previous_date,
                 "previous_value": w.previous_value, "move_pct": w.move_pct, "path": str(path)},
            )
    return report
