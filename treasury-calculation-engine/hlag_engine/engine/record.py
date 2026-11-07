"""Run record (spec 4.7): building the record, writing it in one transaction with every cash line, and
loading the exact profile version a record names (for replay)."""

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hlag_engine.company_profile.registers import Registers
from hlag_engine.company_profile.schema import PROFILE_DIR, Profile, load_profile
from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.run_record import RunRecord
from hlag_engine.data.store import Store


class ReplayError(ValueError):
    pass


def to_jsonable(obj: Any) -> Any:
    """Plain JSON values: Decimal -> str, dates -> ISO, enums -> value, dict keys -> str."""
    if isinstance(obj, BaseModel):
        return to_jsonable(obj.model_dump(mode="json"))
    if is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {(k.isoformat() if isinstance(k, date) else k.value if isinstance(k, Enum) else str(k)): to_jsonable(v)
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        items = [to_jsonable(v) for v in obj]
        return sorted(items, key=str) if isinstance(obj, set) else items
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, float) and obj != obj:
        return None
    return obj


def registers_hash(registers: Registers | None) -> str | None:
    if registers is None:
        return None
    return "sha256:" + hashlib.sha256(registers.model_dump_json().encode()).hexdigest()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "run"


def next_run_id(store: Store, as_of: date, name: str) -> str:
    prefix = f"run_{as_of:%Y%m%d}_{slug(name)}"
    rows = store.conn.execute("SELECT run_id FROM run_records WHERE run_id LIKE ?", (prefix + "_%",)).fetchall()
    taken = {r["run_id"] for r in rows}
    n = 1
    while f"{prefix}_{n}" in taken:
        n += 1
    return f"{prefix}_{n}"


def next_pair_id(store: Store, as_of: date, name: str) -> str:
    prefix = f"pair_{as_of:%Y%m%d}_{slug(name)}"
    rows = store.conn.execute("SELECT pair_id FROM run_records WHERE pair_id LIKE ?", (prefix + "_%",)).fetchall()
    taken = {r["pair_id"] for r in rows}
    n = 1
    while f"{prefix}_{n}" in taken:
        n += 1
    return f"{prefix}_{n}"


def write_run(store: Store, record: RunRecord, lines: list[CashLine]) -> None:
    """Record, cash lines and audit entry in one transaction; a failure writes nothing."""
    with store.transaction():
        store.save_run_record(record)
        store.save_cash_lines(record.run_id, lines)
        store.append_audit("engine", "run_completed", record.run_id, {
            "as_of": record.as_of, "source": record.params.source.value, "status": record.status.value,
            "profile_version": record.profile_version, "lines": len(lines),
        })


def load_profile_version(version: str, content_hash: str, directory: Path = PROFILE_DIR) -> Profile:
    """The profile file whose version and content hash match a run record."""
    for path in sorted(directory.glob("*.yaml")):
        if path.name == "active.yaml":
            continue
        try:
            profile = load_profile(path)
        except Exception:
            continue
        if profile.profile_version == version:
            if profile.content_hash != content_hash:
                raise ReplayError(f"profile {version} in {path.name} has changed since the run (hash differs)")
            return profile
    raise ReplayError(f"no profile file with version {version}")


def canonical(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), sort_keys=True)
