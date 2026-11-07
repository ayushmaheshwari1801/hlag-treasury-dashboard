"""Replaceable inputs: the connection points for real treasury data.

Four kinds of data ship as DEMO defaults and can be replaced through the API (or, later, by a connector that calls the same
methods): liquidity, debt maturity profile, approved limits and FX exposures/hedges. Every change is validated, persisted
(when a data directory is configured) and audited by the service. Deleting an override restores the demo default.
"""
from __future__ import annotations

import copy
import json
import threading
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .config import Defaults
from .materiality import ApprovedLimit
from .models import MODULES, Provenance, ValidationProblem

KINDS = ("liquidity", "debt_maturity", "limits", "exposures")
SETTABLE_PROVENANCE = [p.value for p in Provenance if p is not Provenance.UNAVAILABLE]


def _num(value: Any, name: str, *, minimum: float | None = None, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValidationProblem(f"{name} must be a number")
    try:
        v = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationProblem(f"{name} must be a number") from None
    if not v.is_finite():
        raise ValidationProblem(f"{name} must be finite")
    if positive and v <= 0:
        raise ValidationProblem(f"{name} must be greater than zero")
    if minimum is not None and v < Decimal(str(minimum)):
        raise ValidationProblem(f"{name} must be at least {minimum}")
    return float(v)


def _text(data: dict, key: str, *, required: bool = True) -> str | None:
    v = data.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ValidationProblem(f"{key} is required")
        return None
    if not isinstance(v, str) or len(v) > 300:
        raise ValidationProblem(f"{key} must be text of at most 300 characters")
    return v.strip()


def _provenance(data: dict) -> str:
    p = data.get("provenance", Provenance.OBSERVED.value)
    if p not in SETTABLE_PROVENANCE:
        raise ValidationProblem("provenance must be one of " + ", ".join(SETTABLE_PROVENANCE))
    return p


def validate(kind: str, data: Any, *, today: date) -> dict:
    if kind not in KINDS:
        raise KeyError(kind)
    if not isinstance(data, dict):
        raise ValidationProblem("data must be an object")
    if kind == "liquidity":
        allowed = {"reported_total_usd_m", "balance_date", "balance_date_eurusd", "minimum_liquidity_eur_m", "source", "provenance"}
        extra = set(data) - allowed
        if extra:
            raise ValidationProblem("Unknown field(s): " + ", ".join(sorted(extra)))
        try:
            bd = date.fromisoformat(str(data.get("balance_date")))
        except ValueError:
            raise ValidationProblem("balance_date must be an ISO date (YYYY-MM-DD)") from None
        if bd > today:
            raise ValidationProblem("balance_date cannot be in the future")
        out = {"reported_total_usd_m": _num(data.get("reported_total_usd_m"), "reported_total_usd_m", positive=True),
               "balance_date": bd.isoformat(), "source": _text(data, "source"), "provenance": _provenance(data)}
        if "balance_date_eurusd" in data:
            out["balance_date_eurusd"] = str(_num(data["balance_date_eurusd"], "balance_date_eurusd", positive=True))
        if "minimum_liquidity_eur_m" in data:
            out["minimum_liquidity_eur_m"] = _num(data["minimum_liquidity_eur_m"], "minimum_liquidity_eur_m", minimum=0)
        return out
    if kind == "debt_maturity":
        allowed = {"categories", "years", "values", "unit", "reported_total_usd_m", "source", "provenance"}
        extra = set(data) - allowed
        if extra:
            raise ValidationProblem("Unknown field(s): " + ", ".join(sorted(extra)))
        cats, years, values = data.get("categories"), data.get("years"), data.get("values")
        if not (isinstance(cats, list) and cats and all(isinstance(c, str) and c for c in cats)):
            raise ValidationProblem("categories must be a non-empty list of names")
        if not (isinstance(years, list) and years and all(isinstance(y, str) and y for y in years)) or len(set(years)) != len(years):
            raise ValidationProblem("years must be a non-empty list of unique labels")
        if not isinstance(values, dict) or set(values) != set(years):
            raise ValidationProblem("values must have exactly one entry per year")
        clean = {}
        for y in years:
            row = values[y]
            if not isinstance(row, list) or len(row) != len(cats):
                raise ValidationProblem(f"values[{y}] must list one amount per category ({len(cats)})")
            clean[y] = [_num(v, f"values[{y}]", minimum=0) for v in row]
        out = {"categories": cats, "years": years, "values": clean, "unit": _text(data, "unit", required=False) or "EUR m",
               "source": _text(data, "source"), "provenance": _provenance(data)}
        if "reported_total_usd_m" in data:
            out["reported_total_usd_m"] = _num(data["reported_total_usd_m"], "reported_total_usd_m", positive=True)
        return out
    if kind == "limits":
        if set(data) - set(MODULES):
            raise ValidationProblem("limits are keyed by module: " + ", ".join(MODULES))
        out = {}
        for module, lim in data.items():
            try:
                al = ApprovedLimit(**lim)
            except Exception as exc:
                raise ValidationProblem(f"limit for {module}: {exc}") from None
            if al.amount <= 0 or al.currency not in ("USD", "EUR"):
                raise ValidationProblem(f"limit for {module}: amount must be positive and currency USD or EUR")
            out[module] = al.model_dump()
        return out
    from .engines.fx import Hedge, Position
    extra = set(data) - {"positions", "hedges"}
    if extra:
        raise ValidationProblem("Unknown field(s): " + ", ".join(sorted(extra)))
    try:
        positions = [Position(**p) for p in data.get("positions", [])]
        hedges = [Hedge(**h) for h in data.get("hedges", [])]
    except Exception as exc:
        raise ValidationProblem(f"exposures: {exc}") from None
    for p in positions:
        _num(p.amount_m, f"position {p.id} amount_m", positive=True)
    for h in hedges:
        _num(h.notional_m, f"hedge {h.id} notional_m", positive=True)
    if len({p.id for p in positions}) != len(positions) or len({h.id for h in hedges}) != len(hedges):
        raise ValidationProblem("position and hedge ids must be unique")
    return {"positions": [p.model_dump(mode="json") for p in positions], "hedges": [h.model_dump(mode="json") for h in hedges]}


class DataSources:
    def __init__(self, directory: Path | str | None = None):
        self.dir = Path(directory) if directory else None
        self.lock = threading.Lock()
        self.version = 0
        self._store: dict[str, dict] = {}
        if self.dir and (self.dir / "overrides.json").exists():
            self._store = json.loads((self.dir / "overrides.json").read_text(encoding="utf-8"))

    def _persist(self) -> None:
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / "overrides.json.tmp"
            tmp.write_text(json.dumps(self._store, indent=1), encoding="utf-8")
            tmp.replace(self.dir / "overrides.json")

    def put(self, kind: str, data: dict, actor: str, *, today: date) -> dict:
        clean = validate(kind, data, today=today)
        entry = {"data": clean, "updated_by": actor, "updated_at": datetime.now(timezone.utc).isoformat()}
        with self.lock:
            self._store[kind] = entry
            self.version += 1
            self._persist()
        return entry

    def delete(self, kind: str) -> bool:
        if kind not in KINDS:
            raise KeyError(kind)
        with self.lock:
            existed = self._store.pop(kind, None) is not None
            self.version += 1
            self._persist()
        return existed

    def get(self, kind: str) -> dict | None:
        if kind not in KINDS:
            raise KeyError(kind)
        return self._store.get(kind)

    def status(self) -> dict:
        return {k: ({"source": "connected", "updated_by": self._store[k]["updated_by"], "updated_at": self._store[k]["updated_at"]}
                    if k in self._store else {"source": "demo", "updated_by": None, "updated_at": None}) for k in KINDS}

    # --------------------------------------------------------------- application
    def effective_defaults(self, base: Defaults) -> Defaults:
        raw = copy.deepcopy(base.raw)
        if (e := self._store.get("liquidity")):
            raw["liquidity"].update(e["data"])
        if (e := self._store.get("debt_maturity")):
            raw["debt_maturity"].update(e["data"])
        return Defaults(raw)

    def limit_for(self, module: str) -> ApprovedLimit | None:
        e = self._store.get("limits")
        return ApprovedLimit(**e["data"][module]) if e and module in e["data"] else None

    def exposures(self):
        e = self._store.get("exposures")
        if not e:
            return None, None
        from .engines.fx import Hedge, Position
        return [Position(**p) for p in e["data"]["positions"]], [Hedge(**h) for h in e["data"]["hedges"]]
