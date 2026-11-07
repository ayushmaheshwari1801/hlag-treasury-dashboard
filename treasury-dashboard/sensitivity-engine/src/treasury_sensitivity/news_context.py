"""Read-only bridge to the news engine's saved article exports.

News explains movements; it never enters a formula. Numeric signals from CONFIRMED articles may be
offered as *candidate assumptions* that a person must adopt explicitly (``applied`` is always false).
Only the exported JSON is read, so the news package is not imported and never called.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MODULE_TERMS = {
    "fx": ["exchange rate", "currency", "fx", "foreign exchange", "cad", "canadian dollar", "usd", "euro", "yuan", "hedg", "dollar"],
    "interest": ["ecb", "interest rate", "policy rate", "deposit facility", "rate hike", "rate cut", "euribor", "€str", "central bank", "monetary policy"],
    "bunker": ["bunker", "vlsfo", "fuel", "imo", "rotterdam", "low sulphur", "low-sulphur", "marine fuel", "oil price"],
    "freight": ["scfi", "freight rate", "container rate", "spot rate", "fbx", "red sea", "suez", "port strike", "strike", "congestion", "container shipping"],
}


def latest_export(directory: Path) -> Path | None:
    files = [p for p in Path(directory).rglob("articles_*.json") if p.is_file()] if Path(directory).exists() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _terms(module: str, text: str) -> list[str]:
    low = text.lower()
    return sorted({t for t in MODULE_TERMS[module] if re.search(rf"(?<![a-z]){re.escape(t)}", low)})


BPS_UNITS = {"bps", "basis points", "basis point", "bp"}
SIGN = {"increase": 1, "decrease": -1}


def _candidate(module: str, signal: dict) -> dict | None:
    """Only unambiguous moves become candidates. A bare level (e.g. 600 USD/t) is not a shock and is skipped."""
    if signal.get("level") != "numeric":
        return None
    indicator = signal.get("indicator", "").lower()
    change = signal.get("change") or None
    unit = ((change or {}).get("unit") or signal.get("unit") or "").lower()
    key = value = None
    if module == "interest" and "rate" in indicator:
        if change and unit in BPS_UNITS:
            key, value = "shock_bps", change["delta"]
        elif change and unit in {"pp", "percentage points", "percentage point"}:
            key, value = "shock_bps", change["delta"] * 100
        elif not change and unit in BPS_UNITS and signal.get("direction") in SIGN and signal.get("value") is not None:
            key, value = "shock_bps", SIGN[signal["direction"]] * abs(signal["value"])   # real exports: size in value, sign in direction
    elif change and module == "bunker" and re.search(r"(usd|\$)\s*(/|per)\s*(t|tonne|mt|ton)", unit):
        key, value = "price_shock", change["delta"]
    elif change and module == "freight" and ("scfi" in indicator or "freight" in indicator):
        if unit in {"%", "percent"}:
            key, value = "scfi_change_pct", change["delta"]
        elif change.get("previous"):
            key, value = "scfi_change_pct", change["delta"] / change["previous"] * 100
    if key is None:
        return None
    return {"override_key": key, "suggested_value": round(float(value), 6), "indicator": signal.get("indicator"),
            "source_unit": unit, "applied": False, "requires_user_confirmation": True,
            "note": "Taken from a confirmed article. Not used in any calculation unless a person submits it as an assumption."}


def news_for_module(module: str, export: Path | None, *, limit: int = 10) -> dict[str, Any]:
    if module not in MODULE_TERMS:
        raise KeyError(module)
    base = {"module": module, "context_only": True, "export": None if export is None else str(export), "as_of": None,
            "status": None, "coverage_gaps": [], "items": [], "message": None}
    if export is None:
        return {**base, "message": "No saved news export found. News context is optional and the calculations are unaffected."}
    try:
        data = json.loads(Path(export).read_text(encoding="utf-8"))
        articles = data["articles"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {**base, "message": f"News export could not be read ({type(exc).__name__}); calculations are unaffected."}
    items = []
    for a in articles:
        if a.get("review_status") == "rejected":
            continue
        text = " ".join([a.get("title", ""), a.get("summary", ""), *a.get("facts", []),
                         *(s.get("indicator", "") for s in a.get("signals", []))])
        matched = _terms(module, text)
        if not matched:
            continue
        pending = a.get("review_status") != "confirmed"
        items.append({
            "article_id": a.get("article_id"), "title": a.get("title"), "url": a.get("url"), "published_at": a.get("published_at"),
            "category": a.get("category"), "score": a.get("score"), "review_status": a.get("review_status"),
            "unverified": pending, "summary": a.get("summary"), "matched_terms": matched,
            "signals": [s for s in a.get("signals", [])],
            "candidate_assumptions": [] if pending else [c for c in (_candidate(module, s) for s in a.get("signals", [])) if c]})
    items.sort(key=lambda i: (-(i["score"] or 0), i["published_at"] or ""))
    return {**base, "as_of": data.get("as_of"), "status": data.get("status"), "coverage_gaps": data.get("coverage_gaps", []),
            "items": items[:limit],
            "message": None if items else "No saved articles match this module's keywords. This is not evidence of no risk."}
