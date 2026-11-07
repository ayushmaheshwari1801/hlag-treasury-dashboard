"""SQLite store (spec 1.5, 3.6, 4.7) with a small data-access layer.

One file, nine tables. The audit log is append-only: SQLite triggers abort any UPDATE or
DELETE on it (P35). Records that have a contract are stored as their contract JSON and read
back through the contract, so the store cannot hold an invalid record.
"""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.baseline_change import BaselineChange
from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import AdjustmentStatus, ProvenanceLabel
from hlag_engine.contracts.run_record import RunRecord
from hlag_engine.contracts.signal import Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_data (
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source TEXT NOT NULL,
    label TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    filled INTEGER NOT NULL CHECK (filled IN (0, 1)),
    days_since_print INTEGER NOT NULL CHECK (days_since_print >= 0),
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    source_ref TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
);
CREATE TABLE IF NOT EXISTS run_records (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    status TEXT NOT NULL,
    pair_id TEXT,
    superseded_by TEXT,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_lines (
    line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sub_id TEXT NOT NULL,
    date TEXT NOT NULL,
    line_code TEXT NOT NULL,
    pass_type TEXT,
    line_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cash_lines_run ON cash_lines (run_id);
CREATE TABLE IF NOT EXISTS adjustments (
    adjustment_id TEXT PRIMARY KEY,
    param_key TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    review_date TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirm_queue (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL,
    unit TEXT,
    source_url TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    failed_checks TEXT NOT NULL,
    major_move_alert INTEGER NOT NULL CHECK (major_move_alert IN (0, 1)),
    state TEXT NOT NULL CHECK (state IN ('pending', 'confirmed', 'corrected', 'rejected')),
    resolved_by TEXT,
    resolved_at TEXT,
    resolved_value REAL,
    resolution_note TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    object_id TEXT NOT NULL,
    details TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TABLE IF NOT EXISTS signals (
    event_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    signal_json TEXT NOT NULL,
    superseded_by_version INTEGER,
    superseded_at TEXT,
    PRIMARY KEY (event_id, version)
);
"""

# Tables whose shape changed after step 3 (P82, P84, P86); created here, and rebuilt by _migrate on older files.
AUTO_BUILD_SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_build_queue (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'signal' CHECK (source IN ('signal', 'tier_a')),
    event_id TEXT,              -- a news event (source = signal); null for Tier A requests
    version INTEGER,            -- that event's version
    dedupe_key TEXT,            -- Tier A: the rule ID
    request_date TEXT,          -- Tier A: the day of the request (one per rule per day)
    payload TEXT,               -- the AutoBuildRequest JSON (Tier A)
    queued_at TEXT NOT NULL,
    queued_for TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'built', 'cancelled')),
    run_id TEXT,
    UNIQUE (event_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS auto_build_dedupe ON auto_build_queue (source, dedupe_key, request_date);
"""
BASELINE_CHANGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS baseline_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    window TEXT NOT NULL DEFAULT 'daily' CHECK (window IN ('daily', 'weekly')),
    date TEXT NOT NULL,
    previous_run_id TEXT NOT NULL,
    current_run_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    UNIQUE (date, current_run_id, window)
);
"""
ALERTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    active_key TEXT NOT NULL,   -- the rule ID, or data:<kind>:<series> for data alerts
    severity TEXT,
    state TEXT NOT NULL CHECK (state IN ('open', 'acknowledged', 'cleared')),
    opened_at TEXT NOT NULL,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    cleared_at TEXT,
    updated_on TEXT NOT NULL,   -- last evaluation date that confirmed it
    alert_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_active ON alerts (active_key, state);
"""

TABLES = (
    "market_data",
    "run_records",
    "cash_lines",
    "adjustments",
    "confirm_queue",
    "audit_log",
    "signals",
    "auto_build_queue",
    "baseline_changes",
    "alerts",  # not in the 1.5 list: Tier A alert lifecycle (P82)
)


class StoreError(Exception):
    """A store operation that would break a rule (unknown id, wrong state)."""


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MarketRow:
    series_id: str
    date: date
    value: float
    unit: str
    source: str
    label: ProvenanceLabel
    retrieved_at: datetime
    filled: bool
    days_since_print: int
    confirmed: bool
    source_ref: str


@dataclass(frozen=True)
class QueueItem:
    queue_id: int
    series_id: str
    date: date
    value: float | None
    unit: str | None
    source_url: str
    submitted_by: str
    submitted_at: datetime
    failed_checks: list[str]
    major_move_alert: bool
    state: str
    resolved_by: str | None
    resolved_at: datetime | None
    resolved_value: float | None
    resolution_note: str | None


@dataclass(frozen=True)
class AuditEntry:
    entry_id: int
    time: datetime
    actor: str
    action: str
    object_id: str
    details: dict[str, Any]


@dataclass(frozen=True)
class StoredSignal:
    signal: Signal
    received_at: datetime
    superseded_by_version: int | None
    superseded_at: datetime | None


@dataclass(frozen=True)
class AutoBuildItem:
    item_id: int
    event_id: str | None
    version: int | None
    queued_at: datetime
    queued_for: date
    state: str
    run_id: str | None
    source: str = "signal"
    dedupe_key: str | None = None
    request_date: date | None = None
    payload: dict | None = None


@dataclass(frozen=True)
class StoredAlert:
    alert: "Alert"
    active_key: str
    updated_on: date


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    """Data-access layer over one SQLite file (or ":memory:")."""

    def __init__(self, path: Path | str = ":memory:", clock: Callable[[], datetime] = utc_now):
        self.path = str(path)
        self.clock = clock
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self._depth = 0

    def close(self) -> None:
        self.conn.close()

    def _columns(self, table: str) -> set[str]:
        return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _migrate(self) -> None:
        """Create the tables added after step 3 and rebuild older versions of changed tables (data kept)."""
        if "auto_build_queue" in self.table_names() and "source" not in self._columns("auto_build_queue"):
            self.conn.executescript("ALTER TABLE auto_build_queue RENAME TO auto_build_queue_old;")
            self.conn.executescript(AUTO_BUILD_SCHEMA)
            self.conn.executescript(
                "INSERT INTO auto_build_queue (item_id, source, event_id, version, queued_at, queued_for, state, run_id) "
                "SELECT item_id, 'signal', event_id, version, queued_at, queued_for, state, run_id FROM auto_build_queue_old;"
                "DROP TABLE auto_build_queue_old;")
        if "baseline_changes" in self.table_names() and "window" not in self._columns("baseline_changes"):
            self.conn.executescript("ALTER TABLE baseline_changes RENAME TO baseline_changes_old;")
            self.conn.executescript(BASELINE_CHANGES_SCHEMA)
            self.conn.executescript(
                "INSERT INTO baseline_changes (change_id, window, date, previous_run_id, current_run_id, record_json) "
                "SELECT change_id, 'daily', date, previous_run_id, current_run_id, record_json FROM baseline_changes_old;"
                "DROP TABLE baseline_changes_old;")
        self.conn.executescript(AUTO_BUILD_SCHEMA + BASELINE_CHANGES_SCHEMA + ALERTS_SCHEMA)

    def now(self) -> datetime:
        return self.clock()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """All writes inside commit together or not at all (nesting joins the outer one)."""
        if self._depth:
            self._depth += 1
            try:
                yield self.conn
            finally:
                self._depth -= 1
            return
        self._depth = 1
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        finally:
            self._depth = 0

    def table_names(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return sorted(r["name"] for r in rows if not r["name"].startswith("sqlite_"))

    # ------------------------------------------------------------------ audit log (4.7)
    def append_audit(self, actor: str, action: str, object_id: str, details: dict[str, Any] | None = None) -> int:
        with self.transaction():
            cur = self.conn.execute(
                "INSERT INTO audit_log (time, actor, action, object_id, details) VALUES (?, ?, ?, ?, ?)",
                (self.now().isoformat(), actor, action, object_id, json.dumps(details or {}, sort_keys=True, default=str)),
            )
        return int(cur.lastrowid)

    def list_audit(self, action: str | None = None, object_id: str | None = None) -> list[AuditEntry]:
        sql, args = "SELECT * FROM audit_log WHERE 1 = 1", []
        if action is not None:
            sql, args = sql + " AND action = ?", [*args, action]
        if object_id is not None:
            sql, args = sql + " AND object_id = ?", [*args, object_id]
        rows = self.conn.execute(sql + " ORDER BY entry_id", args).fetchall()
        return [
            AuditEntry(r["entry_id"], datetime.fromisoformat(r["time"]), r["actor"], r["action"], r["object_id"], json.loads(r["details"]))
            for r in rows
        ]

    # ------------------------------------------------------------------ market data (3.6)
    def upsert_market_rows(self, rows: list[MarketRow]) -> None:
        with self.transaction():
            self.conn.executemany(
                "INSERT OR REPLACE INTO market_data VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.series_id, r.date.isoformat(), float(r.value), r.unit, r.source, r.label.value,
                        r.retrieved_at.isoformat(), int(r.filled), r.days_since_print, int(r.confirmed), r.source_ref,
                    )
                    for r in rows
                ],
            )

    @staticmethod
    def _market_row(r: sqlite3.Row) -> MarketRow:
        return MarketRow(
            series_id=r["series_id"], date=date.fromisoformat(r["date"]), value=r["value"], unit=r["unit"],
            source=r["source"], label=ProvenanceLabel(r["label"]), retrieved_at=datetime.fromisoformat(r["retrieved_at"]),
            filled=bool(r["filled"]), days_since_print=r["days_since_print"], confirmed=bool(r["confirmed"]),
            source_ref=r["source_ref"],
        )

    def get_market_row(self, series_id: str, on: date) -> MarketRow | None:
        r = self.conn.execute(
            "SELECT * FROM market_data WHERE series_id = ? AND date = ?", (series_id, on.isoformat())
        ).fetchone()
        return self._market_row(r) if r else None

    def get_series(
        self, series_id: str, start: date | None = None, end: date | None = None, include_filled: bool = True
    ) -> list[MarketRow]:
        sql, args = "SELECT * FROM market_data WHERE series_id = ?", [series_id]
        if start is not None:
            sql, args = sql + " AND date >= ?", [*args, start.isoformat()]
        if end is not None:
            sql, args = sql + " AND date <= ?", [*args, end.isoformat()]
        if not include_filled:
            sql += " AND filled = 0"
        return [self._market_row(r) for r in self.conn.execute(sql + " ORDER BY date", args).fetchall()]

    def latest_print_before(self, series_id: str, before: date) -> MarketRow | None:
        """Latest real print (filled = false) dated strictly before a date."""
        r = self.conn.execute(
            "SELECT * FROM market_data WHERE series_id = ? AND date < ? AND filled = 0 ORDER BY date DESC LIMIT 1",
            (series_id, before.isoformat()),
        ).fetchone()
        return self._market_row(r) if r else None

    def latest_on_or_before(self, series_id: str, on: date) -> MarketRow | None:
        r = self.conn.execute(
            "SELECT * FROM market_data WHERE series_id = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (series_id, on.isoformat()),
        ).fetchone()
        return self._market_row(r) if r else None

    def set_market_confirmed(self, series_id: str, on: date) -> None:
        with self.transaction():
            cur = self.conn.execute(
                "UPDATE market_data SET confirmed = 1 WHERE series_id = ? AND date = ?", (series_id, on.isoformat())
            )
            if cur.rowcount == 0:
                raise StoreError(f"no market value for {series_id} on {on}")

    # ------------------------------------------------------------------ run records and cash lines (4.7)
    def save_run_record(self, record: RunRecord) -> None:
        with self.transaction():
            self.conn.execute(
                "INSERT INTO run_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.run_id, record.created_at.isoformat(), record.as_of.isoformat(), record.status.value,
                    record.pair_id, record.superseded_by, record.model_dump_json(),
                ),
            )

    def get_run_record(self, run_id: str) -> RunRecord | None:
        r = self.conn.execute("SELECT record_json FROM run_records WHERE run_id = ?", (run_id,)).fetchone()
        return RunRecord.model_validate_json(r["record_json"]) if r else None

    def mark_run_superseded(self, run_id: str, superseded_by: str) -> None:
        record = self.get_run_record(run_id)
        if record is None:
            raise StoreError(f"unknown run {run_id}")
        if self.get_run_record(superseded_by) is None:
            raise StoreError(f"unknown run {superseded_by}")
        updated = record.model_copy(update={"superseded_by": superseded_by})
        with self.transaction():
            self.conn.execute(
                "UPDATE run_records SET superseded_by = ?, record_json = ? WHERE run_id = ?",
                (superseded_by, updated.model_dump_json(), run_id),
            )

    def save_cash_lines(self, run_id: str, lines: list[CashLine]) -> None:
        for line in lines:
            if line.run_id is not None and line.run_id != run_id:
                raise StoreError(f"cash line {line.sub_id} belongs to run {line.run_id}, not {run_id}")
        with self.transaction():
            self.conn.executemany(
                "INSERT INTO cash_lines (run_id, sub_id, date, line_code, pass_type, line_json) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, line.sub_id, line.date.isoformat(), line.line_code.value,
                        line.pass_type.value if line.pass_type else None, line.model_dump_json(),
                    )
                    for line in lines
                ],
            )

    def get_cash_lines(self, run_id: str) -> list[CashLine]:
        rows = self.conn.execute(
            "SELECT line_json FROM cash_lines WHERE run_id = ? ORDER BY line_id", (run_id,)
        ).fetchall()
        return [CashLine.model_validate_json(r["line_json"]) for r in rows]

    # ------------------------------------------------------------------ adjustments (2.9)
    def save_adjustment(self, adjustment: Adjustment) -> None:
        """Insert or update an adjustment record (the workflow rules are in step 7 and the dashboard)."""
        with self.transaction():
            self.conn.execute(
                "INSERT OR REPLACE INTO adjustments VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    adjustment.adjustment_id, adjustment.param_key, adjustment.status.value,
                    adjustment.effective_date.isoformat(), adjustment.review_date.isoformat(),
                    self.now().isoformat(), adjustment.model_dump_json(),
                ),
            )

    def get_adjustment(self, adjustment_id: str) -> Adjustment | None:
        r = self.conn.execute("SELECT record_json FROM adjustments WHERE adjustment_id = ?", (adjustment_id,)).fetchone()
        return Adjustment.model_validate_json(r["record_json"]) if r else None

    def list_adjustments(self, status: AdjustmentStatus | None = None) -> list[Adjustment]:
        sql, args = "SELECT record_json FROM adjustments", []
        if status is not None:
            sql, args = sql + " WHERE status = ?", [status.value]
        rows = self.conn.execute(sql + " ORDER BY adjustment_id", args).fetchall()
        return [Adjustment.model_validate_json(r["record_json"]) for r in rows]

    # ------------------------------------------------------------------ confirm queue (3.6, D8, D31)
    def add_queue_item(
        self, *, series_id: str, on: date, value: float | None, unit: str | None, source_url: str,
        submitted_by: str, failed_checks: list[str], major_move_alert: bool,
    ) -> int:
        with self.transaction():
            cur = self.conn.execute(
                "INSERT INTO confirm_queue (series_id, date, value, unit, source_url, submitted_by, submitted_at,"
                " failed_checks, major_move_alert, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (
                    series_id, on.isoformat(), value, unit, source_url, submitted_by, self.now().isoformat(),
                    json.dumps(failed_checks), int(major_move_alert),
                ),
            )
        return int(cur.lastrowid)

    @staticmethod
    def _queue_item(r: sqlite3.Row) -> QueueItem:
        return QueueItem(
            queue_id=r["queue_id"], series_id=r["series_id"], date=date.fromisoformat(r["date"]), value=r["value"],
            unit=r["unit"], source_url=r["source_url"], submitted_by=r["submitted_by"],
            submitted_at=datetime.fromisoformat(r["submitted_at"]), failed_checks=json.loads(r["failed_checks"]),
            major_move_alert=bool(r["major_move_alert"]), state=r["state"], resolved_by=r["resolved_by"],
            resolved_at=_dt(r["resolved_at"]), resolved_value=r["resolved_value"], resolution_note=r["resolution_note"],
        )

    def get_queue_item(self, queue_id: int) -> QueueItem | None:
        r = self.conn.execute("SELECT * FROM confirm_queue WHERE queue_id = ?", (queue_id,)).fetchone()
        return self._queue_item(r) if r else None

    def list_queue(self, state: str | None = "pending") -> list[QueueItem]:
        sql, args = "SELECT * FROM confirm_queue", []
        if state is not None:
            sql, args = sql + " WHERE state = ?", [state]
        return [self._queue_item(r) for r in self.conn.execute(sql + " ORDER BY queue_id", args).fetchall()]

    def resolve_queue_item(
        self, queue_id: int, *, state: str, resolved_by: str, resolved_value: float | None, note: str | None
    ) -> None:
        with self.transaction():
            cur = self.conn.execute(
                "UPDATE confirm_queue SET state = ?, resolved_by = ?, resolved_at = ?, resolved_value = ?,"
                " resolution_note = ? WHERE queue_id = ? AND state = 'pending'",
                (state, resolved_by, self.now().isoformat(), resolved_value, note, queue_id),
            )
            if cur.rowcount == 0:
                raise StoreError(f"queue item {queue_id} is not pending")

    # ------------------------------------------------------------------ signals (2.8, 10.6)
    def latest_signal_version(self, event_id: str) -> int | None:
        r = self.conn.execute("SELECT MAX(version) AS v FROM signals WHERE event_id = ?", (event_id,)).fetchone()
        return r["v"]

    def insert_signal(self, signal: Signal) -> None:
        with self.transaction():
            self.conn.execute(
                "INSERT INTO signals (event_id, version, received_at, signal_json) VALUES (?, ?, ?, ?)",
                (signal.event_id, signal.version, self.now().isoformat(), signal.model_dump_json()),
            )

    def mark_signal_superseded(self, event_id: str, version: int, by_version: int) -> None:
        with self.transaction():
            self.conn.execute(
                "UPDATE signals SET superseded_by_version = ?, superseded_at = ? WHERE event_id = ? AND version = ?",
                (by_version, self.now().isoformat(), event_id, version),
            )

    @staticmethod
    def _stored_signal(r: sqlite3.Row) -> StoredSignal:
        return StoredSignal(
            signal=Signal.model_validate_json(r["signal_json"]), received_at=datetime.fromisoformat(r["received_at"]),
            superseded_by_version=r["superseded_by_version"], superseded_at=_dt(r["superseded_at"]),
        )

    def get_signal(self, event_id: str, version: int | None = None) -> StoredSignal | None:
        """The given version, or the current (highest) version."""
        if version is None:
            version = self.latest_signal_version(event_id)
        r = self.conn.execute(
            "SELECT * FROM signals WHERE event_id = ? AND version = ?", (event_id, version)
        ).fetchone()
        return self._stored_signal(r) if r else None

    def list_signal_versions(self, event_id: str) -> list[StoredSignal]:
        rows = self.conn.execute("SELECT * FROM signals WHERE event_id = ? ORDER BY version", (event_id,)).fetchall()
        return [self._stored_signal(r) for r in rows]

    def list_current_signals(self) -> list[Signal]:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE superseded_by_version IS NULL ORDER BY event_id"
        ).fetchall()
        return [self._stored_signal(r).signal for r in rows]

    # ------------------------------------------------------------------ auto-build queue (10.6, D64)
    def enqueue_auto_build(self, event_id: str, version: int, queued_for: date) -> int:
        """A signal-driven request: one per event version (D64)."""
        with self.transaction():
            cur = self.conn.execute(
                "INSERT INTO auto_build_queue (source, event_id, version, queued_at, queued_for, state) "
                "VALUES ('signal', ?, ?, ?, ?, 'queued')",
                (event_id, version, self.now().isoformat(), queued_for.isoformat()),
            )
        return int(cur.lastrowid)

    def enqueue_tier_a_request(self, rule_id: str, request_date: date, payload: dict) -> int | None:
        """A Tier A AutoBuildRequest: at most one per rule per day (4.5); None when that day's request exists."""
        try:
            with self.transaction():
                cur = self.conn.execute(
                    "INSERT INTO auto_build_queue (source, dedupe_key, request_date, payload, queued_at, queued_for, state) "
                    "VALUES ('tier_a', ?, ?, ?, ?, ?, 'queued')",
                    (rule_id, request_date.isoformat(), json.dumps(payload, sort_keys=True, default=str),
                     self.now().isoformat(), request_date.isoformat()),
                )
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid)

    def list_auto_build(self, state: str | None = None, queued_for: date | None = None,
                        source: str | None = None) -> list[AutoBuildItem]:
        sql, args = "SELECT * FROM auto_build_queue WHERE 1 = 1", []
        if source is not None:
            sql, args = sql + " AND source = ?", [*args, source]
        if state is not None:
            sql, args = sql + " AND state = ?", [*args, state]
        if queued_for is not None:
            sql, args = sql + " AND queued_for = ?", [*args, queued_for.isoformat()]
        rows = self.conn.execute(sql + " ORDER BY item_id", args).fetchall()
        return [
            AutoBuildItem(
                r["item_id"], r["event_id"], r["version"], datetime.fromisoformat(r["queued_at"]),
                date.fromisoformat(r["queued_for"]), r["state"], r["run_id"], r["source"], r["dedupe_key"],
                date.fromisoformat(r["request_date"]) if r["request_date"] else None,
                json.loads(r["payload"]) if r["payload"] else None,
            )
            for r in rows
        ]

    def set_auto_build_state(self, item_id: int, state: str, run_id: str | None = None) -> None:
        with self.transaction():
            cur = self.conn.execute(
                "UPDATE auto_build_queue SET state = ?, run_id = ? WHERE item_id = ?", (state, run_id, item_id)
            )
            if cur.rowcount == 0:
                raise StoreError(f"unknown auto-build item {item_id}")

    # ------------------------------------------------------------------ baseline changes (4.10)
    def save_baseline_change(self, change: BaselineChange) -> None:
        with self.transaction():
            self.conn.execute(
                "INSERT INTO baseline_changes (window, date, previous_run_id, current_run_id, record_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (change.window.value, change.date.isoformat(), change.previous_run_id, change.current_run_id,
                 change.model_dump_json()),
            )

    def list_baseline_changes(self, start: date | None = None, end: date | None = None,
                              window: str | None = None) -> list[BaselineChange]:
        sql, args = "SELECT record_json FROM baseline_changes WHERE 1 = 1", []
        if window is not None:
            sql, args = sql + " AND window = ?", [*args, window]
        if start is not None:
            sql, args = sql + " AND date >= ?", [*args, start.isoformat()]
        if end is not None:
            sql, args = sql + " AND date <= ?", [*args, end.isoformat()]
        rows = self.conn.execute(sql + " ORDER BY date, change_id", args).fetchall()
        return [BaselineChange.model_validate_json(r["record_json"]) for r in rows]

    # ------------------------------------------------------------------ alerts (4.5, 4.7), P82
    def save_alert(self, alert: "Alert", active_key: str, updated_on: date) -> None:
        """Insert or update one alert with its lifecycle state."""
        e = alert.explanation
        with self.transaction():
            self.conn.execute(
                "INSERT OR REPLACE INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (e.alert_id, e.tier.value, active_key, e.severity.value if e.severity else None, alert.state.value,
                 alert.opened_at.isoformat(), alert.acknowledged_by,
                 alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
                 alert.cleared_at.isoformat() if alert.cleared_at else None, updated_on.isoformat(),
                 alert.model_dump_json()),
            )

    @staticmethod
    def _stored_alert(r: sqlite3.Row) -> StoredAlert:
        from hlag_engine.contracts.alert import Alert

        return StoredAlert(Alert.model_validate_json(r["alert_json"]), r["active_key"], date.fromisoformat(r["updated_on"]))

    def get_alert(self, alert_id: str) -> StoredAlert | None:
        r = self.conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
        return self._stored_alert(r) if r else None

    def active_alert(self, active_key: str) -> StoredAlert | None:
        """The open or acknowledged alert for a rule (or data key), if any."""
        r = self.conn.execute(
            "SELECT * FROM alerts WHERE active_key = ? AND state != 'cleared' ORDER BY opened_at DESC LIMIT 1",
            (active_key,),
        ).fetchone()
        return self._stored_alert(r) if r else None

    def list_alerts(self, state: str | None = None, tier: str | None = None) -> list[StoredAlert]:
        sql, args = "SELECT * FROM alerts WHERE 1 = 1", []
        if state is not None:
            sql, args = sql + " AND state = ?", [*args, state]
        if tier is not None:
            sql, args = sql + " AND tier = ?", [*args, tier]
        return [self._stored_alert(r) for r in self.conn.execute(sql + " ORDER BY opened_at, alert_id", args).fetchall()]
