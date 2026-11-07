"""Append-only audit store: snapshots, formula/threshold versions, reviewer comments and overrides.

SQLite triggers reject UPDATE and DELETE; each log entry hashes the previous one so edits made
outside the application are detectable with ``verify_chain``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, module TEXT NOT NULL,
  formula_version TEXT NOT NULL, threshold_version TEXT NOT NULL, defaults_version TEXT NOT NULL,
  input_hash TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, event_type TEXT NOT NULL, module TEXT,
  snapshot_id TEXT REFERENCES snapshots(id), actor TEXT, comment TEXT, details TEXT NOT NULL,
  prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots BEGIN SELECT RAISE(ABORT, 'audit records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots BEGIN SELECT RAISE(ABORT, 'audit records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS log_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS log_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit records are append-only'); END;
"""
GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditStore:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    def save_snapshot(self, module: str, request: dict, result: dict) -> str:
        sid = str(uuid.uuid4())
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)",
                (sid, datetime.now(timezone.utc).isoformat(), module, result["formula_version"], result["threshold_version"],
                 result["defaults_version"], digest(canonical(request)), canonical(result)))
        return sid

    def snapshot(self, sid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone()
        return None if row is None else {**dict(row), "payload": json.loads(row["payload"])}

    def append(self, event_type: str, *, module: str | None = None, snapshot_id: str | None = None,
               actor: str | None = None, comment: str | None = None, details: dict | None = None) -> dict:
        with self.lock, self.conn:
            last = self.conn.execute("SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last["entry_hash"] if last else GENESIS
            ts = datetime.now(timezone.utc).isoformat()
            body = {"ts": ts, "event_type": event_type, "module": module, "snapshot_id": snapshot_id, "actor": actor,
                    "comment": comment, "details": details or {}}
            entry_hash = digest(prev + canonical(body))
            cur = self.conn.execute(
                "INSERT INTO audit_log (ts,event_type,module,snapshot_id,actor,comment,details,prev_hash,entry_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, event_type, module, snapshot_id, actor, comment, canonical(details or {}), prev, entry_hash))
        return {"seq": cur.lastrowid, **body, "prev_hash": prev, "entry_hash": entry_hash}

    def entries(self, *, module: str | None = None, limit: int = 100) -> list[dict]:
        q, args = "SELECT * FROM audit_log", []
        if module:
            q, args = q + " WHERE module=?", [module]
        rows = self.conn.execute(q + " ORDER BY seq DESC LIMIT ?", (*args, limit)).fetchall()
        return [{**dict(r), "details": json.loads(r["details"])} for r in rows]

    def verify_chain(self) -> tuple[bool, int | None]:
        prev = GENESIS
        for r in self.conn.execute("SELECT * FROM audit_log ORDER BY seq"):
            body = {"ts": r["ts"], "event_type": r["event_type"], "module": r["module"], "snapshot_id": r["snapshot_id"],
                    "actor": r["actor"], "comment": r["comment"], "details": json.loads(r["details"])}
            if r["prev_hash"] != prev or r["entry_hash"] != digest(prev + canonical(body)):
                return False, r["seq"]
            prev = r["entry_hash"]
        return True, None
