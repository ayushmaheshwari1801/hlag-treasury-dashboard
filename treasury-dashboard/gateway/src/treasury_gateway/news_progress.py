"""Read persisted execution metadata without collecting news or opening a writer."""
import json
import sqlite3
import sys
from pathlib import Path


def writer_busy(database):
    path = Path(str(database) + ".lock")
    if not path.exists():
        return False
    with path.open("r+b") as lock:
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        if sys.platform == "win32":
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return False


def news_progress(database):
    database = Path(database).resolve()
    if not database.exists():
        return {"running": False, "status": "idle", "run_id": None}
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            latest = db.execute("SELECT run_id,status,data FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
            totals = {"articles": db.execute("SELECT count(*) FROM articles").fetchone()[0],
                      "events": db.execute("SELECT count(*) FROM events").fetchone()[0]}
        if not latest:
            return {"running": False, "status": "idle", "run_id": None}
        run_id, status, saved_data = latest
        saved = json.loads(saved_data) if saved_data else {}
        running = status == "running" and writer_busy(database)
        if status == "running" and not running:
            status = "interrupted"
        progress = {}
        try:
            progress = json.loads(Path(str(database) + ".progress.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if progress.get("run_id") != run_id:
            progress = {}
        return {**progress, "run_id": run_id, "running": running, "status": status,
                "stage": progress.get("stage", "processing" if running else status),
                "database_totals": totals, "errors": saved.get("errors", []) or ([{"stage": progress.get("stage", "run"), "code": "interrupted", "message": "The previous process stopped before saving a final result. Existing Articles and pending work are preserved. Check service/provider availability before an explicit refresh; no automatic retry."}] if status == "interrupted" else []), "warnings": saved.get("metrics", {}).get("warnings", [])}
    except (OSError, sqlite3.Error):
        return {"running": False, "status": "unavailable", "run_id": None}
