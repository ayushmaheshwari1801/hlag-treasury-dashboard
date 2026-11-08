"""Fact pack for the Treasury recommendation engine (recommendation-agent/3).

Every figure the recommendation model may use is a numbered fact: {id, label, value, unit, date, source,
provenance}, with an EUR value for amounts. Derived figures are precomputed here so the model never does arithmetic.
Nothing here writes to the engine store: engine runs use write=False and market data is read through the read-only
store.

Situation run = today's baseline (T0 prices held flat, hlag_engine.modules.paths.base_path) plus the combined news
forecast. The Observed moves run re-applies last week's move on top of that baseline, so it is only an assumption
("last week's moves repeat") and appears as F.eng.trend_continues.*, never as something that happened.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any

from .dashboard_adapter import ROWS, NAMES, horizon_cutoff, row_amounts, slider_scenario

ROW_WORDS = {"freight": "freight", "bunker": "fuel", "rates": "interest", "fx": "currencies", "other": "other"}
from .forecast import ForecastOutput, Prediction, compile_scenario, rule_forecast

MAX_EVENTS = 8  # active Events ticked by default, ranked by score
OWNER_ROLES = ("Treasury", "CFO", "Credit Control", "Procurement", "Commercial/Pricing", "Risk Committee")
PAYMENT_CODES = ("DEBT_PRINCIPAL", "DIVIDEND", "INTEREST_EXPENSE", "REFINANCING_COST")
PAYMENT_COUNT = 5
CLOSED_KEYS = frozenset({"fx.hedge_ratio.eur_operating_costs", "fx.hedge_ratio.eur_dividend"})
STATUS_ORDER = {"LOW": 0, "WATCH": 1, "HIGH": 2, "CRITICAL": 3}
HEADER_SERIES = ("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE", "VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN",
                 "HSFO_RTM", "ESTR", "SOFR", "BB_OAS", "FX_USD_PER_EUR", "FX_USD_PER_INR", "FX_USD_PER_BRL",
                 "FX_USD_PER_GBP", "FX_USD_PER_CNY", "FX_USD_PER_CAD")
BUNKER_POLICY = {"text": "Fuel hedges: none in 2025, none at present; derivatives only in exceptional cases",
                 "source": "Hapag-Lloyd Annual Report 2025, pp. 24, 46 (engine specification table)"}
TREND_LABEL = "last week's moves repeat"
MATERIAL_PCT = 1.0  # a decision's cash at stake below 1% of the baseline lowest headroom is immaterial
NET_EFFECT_MONITOR_PCT = 10.0  # ILLUSTRATIVE: net situation cash effect >= 10% of baseline lowest headroom -> monitor
# News inputs that correspond to an engine playbook preset type: (input prefix, required sign or None, preset type).
PRESET_OF_INPUT = (("freight.transit_delay_days.", None, "transit_delay"),
                   ("freight.transit_delay_days.", None, "red_sea_reescalation"),
                   ("rates.rate_path.USD", -1, "fed_cutting_cycle"), ("fx.move_pct.EUR", 1, "eur_rally"))
SUMMARY_CHARS = 400
ARTICLE_CHARS = 1500  # per cited article: its numbers count as cited when the Event is cited


# ------------------------------------------------------------------------------------------- plain labels
NEWS = "With the ticked News"
ENGINE_SOURCE = "Liquidity forecast (calculation engine)"
TRADE_WORDS = {"ASIA_EUROPE": "Asia–Europe", "ASIA_AMERICA": "Asia–America", "EUROPE_AMERICA": "Europe–America",
               "AFRICA_INTRA": "Africa & Intraregional"}
INPUT_FIELDS = {"rate_shock_pct": "Freight rates", "volume_shock_pct": "Transport volumes",
                "transit_delay_days": "Collection delay", "vlsfo_shock_pct": "VLSFO fuel price",
                "hsfo_shock_pct": "HSFO fuel price", "consumption_uplift_pct": "Fuel consumption",
                "rate_path": "Interest-rate step", "credit_spread_change_bp": "Refinancing spread",
                "move_pct": "Exchange rate", "cash_flow": "Direct cash flow"}
POLICY_NAMES = {"cad": "CAD operating costs", "eur_debt": "EUR debt repayment", "eur_dividend": "EUR dividend",
                "eur_operating_costs": "EUR operating costs", "inr_brl": "INR and BRL obligations"}
WINDOW_WORDS = {"1d": "1 day", "1w": "1 week", "4w": "4 weeks", "20d": "20 days"}
# Engine playbook entries in plain business English: (verb, scope, owner). Hedge and swap steps are tested separately.
PLAYBOOK_PLAIN = {
    "freight.sustained_fall": ("Raise", "the liquidity buffer", "Treasury"),
    "freight.spike": ("Prepare", "a plan to deploy surplus cash", "Treasury"),
    "freight.disruption": ("Confirm", "the low-point dates against the payment calendar", "Treasury"),
    "bunker.sustained_rise": ("Review", "fuel surcharge (MFR) recovery with the commercial team", "Commercial/Pricing"),
    "bunker.sharp_fall": ("Prepare", "for faster cash release from lower fuel costs", "Treasury"),
    "bunker.red_sea": ("Assess", "the combined effect of higher freight rates, slower collections and higher fuel burn", "Treasury"),
    "rates.rates_rising": ("Prepare", "to bring refinancing forward", "Treasury"),
    "rates.rates_falling": ("Assess", "repaying floating-rate debt with surplus cash", "Treasury"),
    "rates.spreads_widening": ("Prepare", "early funding for upcoming debt maturities", "Treasury"),
    "fx.eur_rally": ("Review", "the timing of EUR purchases for planned EUR payments", "Treasury"),
    "fx.em_stress": ("Reduce", "cash held in INR and BRL", "Treasury"),
    "fx.volatility_spike": ("Increase", "the frequency of FX exposure reviews", "Treasury"),
}


def _lower(text: str) -> str:
    """Lower-case for use mid-sentence, keeping codes such as CAD, EUR or FX upper-case."""
    return " ".join(w if (w.isupper() and len(w) <= 4) else w.lower() for w in text.split(" "))


def series_name(sid: str) -> str:
    """A market series in words (the engine's display names), never its id."""
    from hlag_engine.engine.methodology import SERIES_NAME
    names = {**SERIES_NAME, "VLSFO_BLENDED": "VLSFO (hub-weighted)"}
    if sid.startswith("FREIGHT_REALISED_"):
        return "Realised freight rate, " + TRADE_WORDS.get(sid.removeprefix("FREIGHT_REALISED_"), "trade")
    return names.get(sid, sid.replace("_", " ").title())


def input_name(param_key: str) -> str:
    """A News forecast input in words: "Freight rates, Asia–Europe", "USD interest-rate step"."""
    parts = param_key.split(".")
    field = INPUT_FIELDS.get(parts[1] if len(parts) > 1 else "", parts[-1].replace("_", " "))
    subject = parts[2] if len(parts) > 2 else ""
    if parts[0] == "fx" and subject:
        return f"{subject} exchange rate"
    if parts[0] == "rates" and subject:
        return f"{subject} interest-rate step"
    return f"{field}, {TRADE_WORDS.get(subject, subject)}" if subject else field


def alert_label(a: dict) -> str:
    """Readable market alert: severity, series and move (not the rule's reference text)."""
    sev = "Severe" if a["severity"] == "severe" else "Warning"
    window = WINDOW_WORDS.get(a["window"], a["window"])
    names = [series_name(sid) for sid in a["series"]]
    moves = a.get("moves") or {}
    def pct(sid):
        v = moves.get(sid)
        return None if v is None else f"{'+' if v >= 0 else '−'}{abs(v):.1f}%"
    obs = a.get("observed")
    if a["type"] == "vol_regime":
        what = names[0].split(" / ")[0] if " / " in names[0] else names[0]
        return f"{sev}: {what} volatility is {'stressed' if a['severity'] == 'severe' else 'elevated'} (short-term vs long-term ratio {obs})"
    if a["type"] == "divergence" and len(names) == 2:
        faster = names[0] if (obs or 0) >= 0 else names[1]
        slower = names[1] if faster == names[0] else names[0]
        detail = ", ".join(x for x in (f"{names[0]} {pct(a['series'][0])}" if pct(a["series"][0]) else "",
                                       f"{names[1]} {pct(a['series'][1])}" if pct(a["series"][1]) else "") if x)
        m0, m1 = moves.get(a["series"][0]), moves.get(a["series"][1])
        if m0 is not None and m1 is not None and (m0 >= 0) != (m1 >= 0):
            up, down = (names[0], names[1]) if m0 >= 0 else (names[1], names[0])
            return f"{sev}: {up} rose while {down} fell over {window} ({detail})"
        if m0 is not None and m1 is not None and m0 < 0 and m1 < 0:
            return f"{sev}: {faster} fell less than {slower} over {window} ({detail})"
        return f"{sev}: {faster} rose faster than {slower} over {window}" + (f" ({detail})" if detail else "")
    if a["type"] == "spread_change" and len(names) == 2:
        return f"{sev}: the {names[0]} to {names[1]} spread {'widened' if (obs or 0) >= 0 else 'narrowed'} by {abs(obs or 0):g} over {window}"
    if a["type"] == "bp_move":
        return f"{sev}: {names[0]} {'rose' if (obs or 0) >= 0 else 'fell'} {abs(obs or 0):g} bp over {window}"
    return f"{sev}: {names[0]} {'rose' if (obs or 0) >= 0 else 'fell'} {abs(obs or 0):.1f}% over {window}"


# ------------------------------------------------------------------------------------------------ helpers
def _ts(value: str | None) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else 0.0


def _r(x: float | None, places: int = 1) -> float | None:
    return None if x is None else round(float(x), places)


def fact(fid: str, label: str, value: Any, unit: str | None, provenance: str, source: str,
         when: str | None = None, rate: float | None = None, **extra) -> dict:
    """One fact. Amounts in USD m also carry their EUR m value at the T0 rate (rate = USD per EUR)."""
    out = {"id": fid, "label": label, "value": value, "unit": unit, "date": when, "source": source,
           "provenance": provenance, **{k: v for k, v in extra.items() if v is not None}}
    if unit == "USD m" and isinstance(value, (int, float)) and rate:
        out["eur_value"] = _r(value / rate)
    return out


DRIVER_WORDS = {"price": "fuel price", "consumption": "fuel consumption", "price_and_consumption": "fuel price and consumption",
                "none": "none"}


def bunker_driver(scenario: dict) -> str:
    """What moves fuel cash in a (dashboard-convention) scenario: "price" (hedgeable with a swap or fixed-price
    supply), "consumption" (more tonnes at the same price: a price hedge cannot offset it), "price_and_consumption",
    or "none"."""
    b = (scenario or {}).get("bunker") or {}
    price = any(abs(float(b.get(k) or 0)) > 1e-9 for k in ("vlsfo_shock_pct", "hsfo_shock_pct"))
    volume = abs(float(b.get("consumption_uplift_pct") or 0)) > 1e-9
    return "price_and_consumption" if price and volume else "price" if price else "consumption" if volume else "none"


# ------------------------------------------------------------------------------------------ active Events
def event_published(event: dict, sources: dict[str, dict]) -> str | None:
    """The Event's date: its latest source Article publication (as news_v8._scenario_events)."""
    stamps = [sources[a].get("published_at") for a in event.get("article_ids", []) if a in sources]
    stamps = [s for s in stamps if s]
    return max(stamps, key=_ts) if stamps else None


def select_active_events(pool: dict | None, limit: int = MAX_EVENTS) -> dict:
    """Scored pool Events ranked by score total, latest Article date, then event_id. The top `limit` are included
    (ticked by default); the rest are listed as not included. Unscored Events are counted, not ranked."""
    if not pool:
        return {"included": [], "not_included": [], "unscored": [], "pool_run_id": None}
    sources = {s["article_id"]: s for s in pool.get("sources", [])}
    scored, unscored = [], []
    for e in pool.get("events", []):
        if not e.get("score"):
            unscored.append(e["event_id"])
            continue
        published = event_published(e, sources)
        primary = sources.get(e["article_ids"][0], {}) if e.get("article_ids") else {}
        scored.append({"event_id": e["event_id"], "title": e["title"], "category": e.get("category"),
                       "score": e["score"]["total"], "relevance": e["score"].get("relevance"),
                       "published_at": published, "publisher": primary.get("publisher")})
    scored.sort(key=lambda x: (-x["score"], -_ts(x["published_at"]), x["event_id"]))
    return {"included": scored[:limit], "not_included": scored[limit:], "unscored": sorted(unscored),
            "pool_run_id": pool.get("run_id")}


def saved_model_forecast(forecasts: list[dict], event_id: str, as_of: str, horizon: int, pool_run_id: str | None):
    """The per-Event model forecast prepared at startup, matched exactly as startup.prepare_event_impacts does."""
    return next((f for f in forecasts
                 if f.get("selected_event_ids", f.get("event_ids")) == [event_id]
                 and f["as_of"] == as_of and f["horizon_days"] == horizon
                 and f.get("source_run_id") == pool_run_id and f["mode"] == "model"), None)


def _slot(p: dict) -> tuple:
    """What one prediction sets: an input, a rate-path date, or one direct cash effect (overlap_key)."""
    if p["param_key"] == "direct.cash_flow":
        return ("direct.cash_flow", p.get("overlap_key"))
    if p["param_key"].startswith("rates.rate_path."):
        return (p["param_key"], p["effective_date"])
    return (p["param_key"],)


def slot_name(slot: tuple) -> str:
    return ":".join(str(x) for x in slot if x is not None)


def combine_event_forecasts(events: list[dict], pool: dict, forecasts: list[dict], as_of: date, horizon: int) -> dict:
    """One combined news forecast for the ticked Events.

    Per Event: its saved model forecast from startup (origin model_saved), or fixed rule inputs when none is saved
    (origin rule_based). No forecast model is called here. When Events set the same input, the higher score wins,
    then higher confidence, then the more recent Event; the others are listed as overlapping, not added (never
    summed). Opposite directions on one input are reported as a conflict."""
    sources = {s["article_id"]: s for s in pool.get("sources", [])}
    rank = {e["event_id"]: ((e.get("score") or {}).get("total", 0), _ts(event_published(e, sources))) for e in events}
    inputs, origins = [], {}
    for e in events:
        saved = saved_model_forecast(forecasts, e["event_id"], as_of.isoformat(), horizon, pool.get("run_id"))
        if saved:
            preds, origins[e["event_id"]] = saved["predictions"], {"origin": "model_saved", "forecast_id": saved["forecast_id"]}
        else:
            preds = [p.model_dump(mode="json") for p in rule_forecast([e], as_of, horizon).predictions]
            origins[e["event_id"]] = {"origin": "rule_based", "forecast_id": None}
        for p in preds:
            inputs.append({**p, "event_id": e["event_id"], "origin": origins[e["event_id"]]["origin"]})
    groups: dict[tuple, list[dict]] = {}
    for p in inputs:
        groups.setdefault(_slot(p), []).append(p)
    used, overlaps, conflicts = [], [], []
    for slot, items in groups.items():
        items.sort(key=lambda p: (-rank[p["event_id"]][0], -p["confidence"], -rank[p["event_id"]][1], p["event_id"]))
        winner, rest = items[0], items[1:]
        winner["used"] = True
        used.append(winner)
        for p in rest:
            p["used"] = False
        if rest:
            overlaps.append({"input": slot_name(slot), "used_event_id": winner["event_id"],
                             "not_added": [{"event_id": p["event_id"], "change": p["change"], "unit": p["unit"]} for p in rest]})
            signs = {1 if p["change"] > 0 else -1 for p in items if p["change"]}
            if len(signs) > 1:
                conflicts.append({"input": slot_name(slot), "used_event_id": winner["event_id"],
                                  "events": [{"event_id": p["event_id"], "direction": "up" if p["change"] > 0 else "down",
                                              "change": p["change"], "unit": p["unit"]} for p in items if p["change"]]})
    keep = {"param_key", "change", "unit", "effective_date", "duration_days", "confidence", "rationale", "event_ids",
            "label", "effect_id", "overlap_key", "cash_label"}
    output = ForecastOutput(predictions=[Prediction(**{k: v for k, v in p.items() if k in keep and v is not None})
                                         for p in used],
                            outlook="Combined news forecast of the ticked Events.")
    scenario = compile_scenario(output, as_of, horizon, {e["event_id"] for e in events})
    scenario["name"] = "News situation (baseline + combined news)"
    return {"scenario": scenario, "inputs": inputs, "overlaps": overlaps, "conflicts": conflicts, "origins": origins}


# --------------------------------------------------------------------------------------------- engine runs
def situation_runs(adapter, as_of: date, combined_scenario: dict, adjustments: list[dict],
                   market: dict | None = None, observed_run: dict | None = None) -> dict:
    """Baseline (preset), situation (baseline + combined news) and, optionally, trend continues (Observed moves).
    All read-only. observed_run: the page's saved Observed moves run, reused when given."""
    from .engine_adapter import to_engine_params
    from .risk_engine import scenario_params as normalise
    presets = {p["id"]: p for p in adapter.presets(as_of)}
    dash = normalise(combined_scenario, as_of)
    situation, result = adapter._run(as_of, dash, to_engine_params(dash, as_of), adjustments)
    runs = {"baseline": adapter.run(as_of, presets["baseline"]["scenario"], adjustments), "situation": situation,
            "situation_result": result}  # the engine RunResult, reused by the hedge lab (not part of the facts)
    if observed_run is not None:
        runs["trend"] = observed_run
    elif market is not None:
        runs["trend"] = adapter.run(as_of, slider_scenario({}, as_of, market), adjustments)
    return runs


def _short(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x.day} {x.strftime('%b')}"


def market_moves_7d(adapter, as_of: date, adjustments: list[dict] | None = None) -> dict:
    """Effect of the last week's market moves on the baseline forecast (read-only), measured per series as the market
    tiles do (engine_adapter.previous_print): weekly series (WCI, VLSFO/HSFO, Brent) latest print vs the previous
    print; daily series the latest print vs the print on or before 7 days earlier (nearest earlier date).

    The engine's baseline step (Engine.anchored_baseline, the P86 method) runs on today's snapshot with every series
    set back to its comparison print, then each driver group (freight, bunker, rates, FX; the engine's SWAP_GROUPS
    order) is swapped to today's prints in turn. Same profile, approved adjustments and history in every step, so
    only market moves differ. Nothing is written: anchored_baseline, build_snapshot and get_series only read."""
    import dataclasses
    from hlag_engine.data.series import SERIES
    from hlag_engine.engine.scheduled import SWAP_GROUPS
    from hlag_engine.engine.snapshot import build_snapshot
    from .engine_adapter import previous_print, to_engine_adjustments
    shared, M = adapter.shared, 1_000_000
    grouped = [sid for _, series in SWAP_GROUPS for sid in series]
    comparisons, missing = {}, []
    with shared.lock:
        today = build_snapshot(shared.store, as_of)
        prev = dict(today)
        for sid in grouped:
            if sid not in today or sid not in SERIES:
                continue
            rows = shared.store.get_series(sid, as_of - timedelta(days=60), as_of, include_filled=False)
            p = previous_print(rows, SERIES[sid].frequency)
            if p is None:
                missing.append(sid)
                continue
            prev[sid] = dataclasses.replace(today[sid], value=p.value, value_date=p.date)
            weekly = SERIES[sid].frequency.value == "weekly"
            comparisons[sid] = {"latest": rows[-1].date.isoformat(), "latest_value": rows[-1].value,
                                "previous": p.date.isoformat(), "previous_value": p.value,
                                "rule": "previous print" if weekly else "7 days earlier (nearest earlier date)",
                                "text": f"{series_name(sid)}: {_short(rows[-1].date.isoformat())} vs {_short(p.date.isoformat())}"}
        shared.engine.gateway_adjustments = to_engine_adjustments(adjustments or [], as_of)
        try:
            start, _ = shared.engine.anchored_baseline(as_of, prev)
            snapshot, step, parts = dict(prev), start, {}
            for group, series in SWAP_GROUPS:
                for sid in series:
                    if sid in today:
                        snapshot[sid] = today[sid]
                value, _ = shared.engine.anchored_baseline(as_of, snapshot)
                parts[group], step = value - step, value
            current, _ = shared.engine.anchored_baseline(as_of, today)
        finally:
            shared.engine.gateway_adjustments = []
    return {"available": True, "method": "per series as the market tiles: weekly latest vs previous print, daily "
                                         "latest vs 7 days earlier",
            "baseline_before_usd_m": float(start / M), "baseline_today_usd_m": float(current / M),
            "total_usd_m": float((current - start) / M),
            "by_driver_usd_m": {g: float(v / M) for g, v in parts.items()},
            "other_usd_m": float((current - step) / M),
            "comparisons": comparisons, "missing": sorted(missing)}


def playbook_entries(adapter) -> list[dict]:
    """The engine modules' playbook entries as plain dicts (read-only)."""
    return [{"entry_id": e.entry_id, "trigger_type": e.trigger_type.value, "trigger": e.trigger,
             "actions": list(e.actions), "hedge_tool": e.hedge_tool, "review_owner": e.review_owner}
            for m in adapter.shared.engine.modules for e in m.playbook()]


def market_alerts(adapter, as_of: date) -> list[dict]:
    """Tier A alert rules evaluated on real prints, read-only (not daily.evaluate_tier_a, which writes audit rows)."""
    from hlag_engine.engine.tier_a import SeriesSource, evaluate_rules
    shared = adapter.shared
    rules = [r for m in shared.engine.modules for r in m.alert_rules()]
    with shared.lock:
        outcomes = evaluate_rules(rules, SeriesSource(shared.store, as_of, shared.profile))
    out = []
    for o in outcomes:
        if not (o.evaluable and o.severity is not None):
            continue
        latest = {sid: v for sid, v, _, _ in o.inputs if "(previous)" not in sid}
        prev = {sid.removesuffix(" (previous)"): v for sid, v, _, _ in o.inputs if "(previous)" in sid}
        moves = {sid: _r((latest[sid] / prev[sid] - 1) * 100, 1) for sid in latest if prev.get(sid)}
        out.append({"rule_id": o.rule.rule_id, "module": o.rule.module.value, "severity": o.severity.value,
                    "type": o.rule.type.value, "observed": _r(o.observed, 2), "unit": o.rule.unit, "window": o.rule.window,
                    "description": o.rule.description, "series": list(o.rule.series), "moves": moves,
                    "date": max(d for _, _, d, _ in o.inputs).isoformat() if o.inputs else None})
    return out


# ----------------------------------------------------------------------------------------------- the pack
def _prior(points: list[dict], days: int) -> dict | None:
    if not points:
        return None
    last = date.fromisoformat(points[-1]["date"])
    return next((p for p in reversed(points[:-1]) if date.fromisoformat(p["date"]) <= last - timedelta(days=days)), None)


def _move(series: dict, prev: dict | None) -> tuple[float | None, str | None]:
    if prev is None or series.get("value") is None:
        return None, None
    if series.get("unit") == "%":
        return _r((series["value"] - prev["value"]) * 100, 1), "bp"
    return (_r((series["value"] / prev["value"] - 1) * 100, 2), "%") if prev["value"] else (None, None)


def build_fact_pack(*, as_of: date, months: int, runs: dict, market: dict, events: dict, ticked: list[str],
                    combined: dict, pool: dict, profile, registers, alerts: list[dict],
                    baseline_change: dict | None = None, market_moves: dict | None = None,
                    playbook: list[dict] | None = None) -> dict:
    """All facts for one recommendation session. Pure: every input is already computed."""
    facts: dict[str, dict] = {}

    def add(f: dict):
        facts[f["id"]] = f

    base, sit = runs["baseline"], runs["situation"]
    rate = (sit.get("market_snapshot", {}).get("FX_USD_PER_EUR") or {}).get("value")
    eng = ENGINE_SOURCE
    add(fact("F.fx.t0_usd_per_eur", "USD per EUR on the forecast date (the rate for all EUR figures)", rate, "USD per EUR", "MARKET",
             "Market data on the forecast date", as_of.isoformat()))
    add(fact("F.horizon.end", "End of the liquidity forecast", base["baseline_path"][-1]["date"], None, "ENGINE", eng))
    add(fact("F.horizon.selected_end", f"End of the selected {months}-month horizon", horizon_cutoff(as_of, months),
             None, "DERIVED", "Dashboard horizon"))

    # ---- liquidity: baseline (today's prices held flat)
    bs = base["summary"]
    add(fact("F.liq.base.min_headroom", "Today's prices: lowest headroom above the minimum reserve", _r(bs["baseline_min_usd_m"]),
             "USD m", "ENGINE", eng, bs["baseline_min_date"], rate))
    add(fact("F.liq.base.min_date", "Today's prices: date of the lowest headroom", bs["baseline_min_date"], None, "ENGINE", eng))
    add(fact("F.liq.base.status", "Today's prices: liquidity risk status", base["status"], None, "ENGINE", eng,
             reasons=base.get("status_reasons") or None))
    add(fact("F.liq.base.first_breach", "Today's prices: first reserve breach", bs.get("first_breach_date") or "none",
             None, "ENGINE", eng))
    params = profile.parameters

    def param(key):
        e = params.get(key)
        return (e.value, e.label.value) if e is not None else (None, None)
    for fid, key, label in (("F.liq.reserve.minimum", "liquidity.minimum_operational_reserve_usd", "Minimum reserve (operating floor)"),
                            ("F.liq.reserve.total", "liquidity.liquidity_reserve_usd",
                             "Reported liquidity reserve (cash, financial assets and unused credit lines)"),
                            ("F.liq.credit_lines_unused", "liquidity.unused_credit_lines_usd", "Unused credit lines"),
                            ("F.liq.cash", "liquidity.cash_and_equivalents_usd", "Cash and cash equivalents (last disclosure)")):
        value, label_ = param(key)
        if value is not None:
            add(fact(fid, label, _r(float(value) / 1e6), "USD m", label_, f"Company profile {profile.profile_version}", rate=rate))

    # ---- key payment dates (largest scheduled baseline outflows within 12 months)
    end = (as_of + timedelta(days=365)).isoformat()
    pays: dict[tuple, float] = {}
    for line in base.get("lines", []):
        if line["line_code"] in PAYMENT_CODES and as_of.isoformat() < line["date"] <= end and line["amount_usd_base_m"] < 0:
            k = (line["line_code"], line["date"])
            pays[k] = pays.get(k, 0.0) + line["amount_usd_base_m"]
    for i, ((code, day), amount) in enumerate(sorted(pays.items(), key=lambda kv: kv[1])[:PAYMENT_COUNT], 1):
        add(fact(f"F.pay.{i}", f"Scheduled payment: {code.replace('_', ' ').lower()}", _r(amount), "USD m", "ENGINE",
                 ENGINE_SOURCE + ", scheduled cash lines", day, rate))

    # ---- market (occurred): level, 1-week and 4-week moves, publication date, staleness
    series = {s["series_id"]: s for s in market.get("series", [])}
    for sid in HEADER_SERIES:
        s = series.get(sid)
        if not s or s.get("value") is None:
            add(fact(f"F.mkt.{sid}.level", f"{series_name(sid)}: latest price", None, None, "MARKET", "Market data", note="missing"))
            continue
        pts = market.get("history", {}).get(sid, [])
        src = s.get("source") or "Market data"
        add(fact(f"F.mkt.{sid}.level", f"{series_name(sid)}: latest price", s["value"], s.get("unit"), s.get("label") or "MARKET",
                 src, s.get("date"), stale=bool(s.get("stale"))))
        week = market.get("weekly", {}).get(sid)
        chg1, unit1 = _move(s, {"value": week["value"]} if week else None)
        add(fact(f"F.mkt.{sid}.chg_1w", f"{series_name(sid)}: 1-week change", chg1, unit1, "DERIVED", src, s.get("date"),
                 compared_with=week["date"] if week else None))
        prev4 = _prior(pts, 28)
        chg4, unit4 = _move(s, prev4)
        add(fact(f"F.mkt.{sid}.chg_4w", f"{series_name(sid)}: 4-week change", chg4, unit4, "DERIVED", src, s.get("date"),
                 compared_with=prev4["date"] if prev4 else None))
    for a in alerts:
        add(fact(f"F.alert.{a['rule_id']}", alert_label(a),
                 a["observed"], a["unit"], "ENGINE", "Market alert rules (calculation engine)", a["date"], window=a["window"],
                 severity=a["severity"], module=a["module"]))
    bc = baseline_change or {"available": False,
                             "reason": "no scheduled baseline for today and one at least 7 days older in the engine store"}
    add(fact("F.occ.baseline_change_1w", "Weekly change in the forecast at today's prices", bc.get("total_usd_m"),
             "USD m" if bc.get("available") else None, "ENGINE", "Scheduled forecasts (calculation engine)", rate=rate,
             available=bool(bc.get("available")), reason=bc.get("reason"), by_driver_usd_m=bc.get("by_driver_usd_m")))

    if market_moves:
        mm, label = market_moves, "Effect of market moves over the last 7 days on the baseline forecast"
        comps = mm.get("comparisons", {})
        note = ("Each series compared as the market tiles: weekly series latest vs previous print, daily series "
                "latest vs 7 days earlier (nearest earlier date). " + "; ".join(c["text"] for c in comps.values()) + ".")
        add(fact("F.eng.market_moves_7d", label, _r(mm["total_usd_m"]), "USD m", "DERIVED",
                 "Liquidity forecast at today's prices compared with last week's prices",
                 as_of.isoformat(), rate, note=note, comparisons={k: c["text"] for k, c in comps.items()},
                 missing=mm.get("missing") or None))
        for group, v in mm["by_driver_usd_m"].items():
            add(fact(f"F.eng.market_moves_7d.{group}", f"{label}: {ROW_WORDS.get(group, group)}", _r(v), "USD m", "DERIVED",
                     "Liquidity forecast at today's prices compared with last week's prices", as_of.isoformat(), rate))

    # ---- situation: baseline + combined news forecast
    ss = sit["summary"]
    cutoff = horizon_cutoff(as_of, months)
    sit_lines = [l for l in sit.get("lines", []) if as_of.isoformat() < l["date"] <= cutoff]
    sit_rows = row_amounts(sit_lines)
    net = sum(l["delta_usd_m"] for l in sit_lines)
    add(fact("F.sit.min_headroom", f"{NEWS}: lowest headroom above the minimum reserve", _r(ss["scenario_min_usd_m"]),
             "USD m", "ENGINE", eng, ss["scenario_min_date"], rate))
    add(fact("F.sit.min_date", f"{NEWS}: date of the lowest headroom (the low point)", ss["scenario_min_date"], None, "ENGINE", eng))
    add(fact("F.sit.status", f"{NEWS}: liquidity risk status", sit["status"], None, "ENGINE", eng,
             reasons=sit.get("status_reasons") or None))
    add(fact("F.sit.first_breach", f"{NEWS}: first reserve breach", ss.get("first_breach_date") or "none", None, "ENGINE", eng))
    add(fact("F.sit.headroom_change_vs_base", f"{NEWS}: change in lowest headroom against today's prices",
             _r(ss["scenario_min_usd_m"] - ss["baseline_min_usd_m"]), "USD m", "ENGINE", eng, rate=rate))
    add(fact("F.sit.bunker_driver", f"{NEWS}: what moves fuel cash (price, consumption, both or none)",
             bunker_driver(combined["scenario"]), None, "DERIVED", "News forecasts",
             value_text=DRIVER_WORDS.get(bunker_driver(combined["scenario"]), bunker_driver(combined["scenario"])),
             note="A fuel price hedge offsets price moves only; extra consumption at the same price needs an operations "
                  "review (routing, speed, consumption, surcharge recovery)."))
    add(fact("F.sit.net_cash_effect", f"{NEWS}: net cash effect within the horizon (all drivers)",
             _r(net), "USD m", "ENGINE", eng, cutoff, rate))
    for rid in ROWS:
        add(fact(f"F.sit.row.{rid}", f"{NEWS}: {_lower(NAMES[rid])} within the horizon",
                 _r(sit_rows[rid]), "USD m", "ENGINE", eng, cutoff, rate))
    low = ss["scenario_min_date"]
    after_rows = row_amounts([l for l in sit_lines if l["date"] > low])
    for rid in ROWS:
        if abs(sit_rows[rid]) > 0.05:
            add(fact(f"F.sit.row.{rid}.after_low_pct", f"{NEWS}: share of the {_lower(NAMES[rid])} cash effect dated after the low point",
                     _r(after_rows[rid] / sit_rows[rid] * 100, 1), "%", "DERIVED",
                     "Calculated from the forecast cash lines by date", low))
    desk = [d for d in sit.get("desk_exceptions", [])]
    for i, d in enumerate(desk, 1):
        add(fact(f"F.sit.desk.{d.get('side') or 'other'}.{i}", f"{NEWS}: large single-driver loss flagged ({ROW_WORDS.get(d.get('side'), d.get('side'))})",
                 _r(d.get("gross_adverse_usd_m")), "USD m", "ENGINE", eng, d.get("date"), rate,
                 kind=d.get("kind"), pct_of_headroom=_r(d.get("pct_of_headroom"))))
    bunker_desk = [f"F.sit.desk.bunker.{i}" for i, d in enumerate(desk, 1)
                   if d.get("side") == "bunker" and d.get("kind", "driver") == "driver"]
    high = STATUS_ORDER.get(sit["status"], 0) >= STATUS_ORDER["HIGH"]
    reason = ("liquidity risk status " + sit["status"].lower()) if high else ""
    if bunker_desk:
        reason = (reason + "; " if reason else "") + "large fuel loss flagged"
    add(fact("F.eng.bunker_exceptional", "Fuel marked as exceptional by the liquidity forecast (high or critical risk, or a large fuel loss)",
             bool(high or bunker_desk), None, "ENGINE", eng,
             reason=reason or f"liquidity risk status {sit['status'].lower()}; no large fuel loss flagged", cites=bunker_desk or None))

    # ---- trend continues (ASSUMPTION): the Observed moves run
    if "trend" in runs:
        tr = runs["trend"]
        tr_rows = row_amounts([l for l in tr.get("lines", []) if as_of.isoformat() < l["date"] <= cutoff])
        src = "Liquidity forecast if " + TREND_LABEL
        add(fact("F.eng.trend_continues.min_headroom", f"Lowest headroom if {TREND_LABEL}", _r(tr["summary"]["scenario_min_usd_m"]),
                 "USD m", "ASSUMPTION", src, tr["summary"]["scenario_min_date"], rate))
        add(fact("F.eng.trend_continues.headroom_change_vs_base", f"Change in lowest headroom if {TREND_LABEL}",
                 _r(tr["summary"]["scenario_min_usd_m"] - tr["summary"]["baseline_min_usd_m"]), "USD m", "ASSUMPTION", src, rate=rate))
        for rid in ROWS:
            add(fact(f"F.eng.trend_continues.row.{rid}", f"If {TREND_LABEL}: {_lower(NAMES[rid])}", _r(tr_rows[rid]),
                     "USD m", "ASSUMPTION", src, cutoff, rate))

    # ---- news: ticked Events, their inputs (origin), overlaps and conflicts
    sources = {s["article_id"]: s for s in pool.get("sources", [])}
    by_id = {e["event_id"]: e for e in pool.get("events", [])}
    for eid in ticked:
        e = by_id[eid]
        art = [sources[a] for a in e.get("article_ids", []) if a in sources]
        add(fact(f"F.news.{eid}", e["title"], (e.get("score") or {}).get("total"), "score", "NEWS",
                 art[0].get("publisher") if art else "News engine", event_published(e, sources),
                 category=e.get("category"), relevance=(e.get("score") or {}).get("relevance"),
                 summary=(e.get("summary") or "")[:SUMMARY_CHARS], origin=combined["origins"][eid]["origin"],
                 articles=[{"title": a.get("title"), "publisher": a.get("publisher"), "url": a.get("url")} for a in art],
                 article_text=" ".join((a.get("text") or "")[:ARTICLE_CHARS] for a in art) or None))
    for p in combined["inputs"]:
        fid = f"F.news.{p['event_id']}.input.{slot_name(_slot(p))}"
        title = by_id.get(p["event_id"], {}).get("title", "News Event")
        add(fact(fid, f"News forecast: {input_name(p['param_key'])} (from \"{title}\")", p["change"], p["unit"], "NEWS",
                 "News forecast (AI, saved)" if p["origin"] == "model_saved" else "News forecast (fixed rule)",
                 p["effective_date"], origin=p["origin"], used=p["used"], confidence=p["confidence"],
                 rationale=p["rationale"][:SUMMARY_CHARS], event_id=p["event_id"], param_key=p["param_key"]))
    for c in combined["conflicts"]:
        add(fact(f"F.news.conflict.{c['input']}", f"News Events disagree on {input_name(c['input'].split(':')[0])}",
                 [f"{x['event_id']} {x['direction']}" for x in c["events"]], None, "DERIVED", "News forecasts",
                 events=c["events"], used_event_id=c["used_event_id"]))

    # ---- ZIM acquisition: whether its financing is in the forecast, and the disclosed / assumed amounts (never switched on here)
    zim_on = bool(((sit.get("engine_params") or {}).get("rates") or {}).get("zim_tranche_on"))
    add(fact("F.zim.in_forecast", "ZIM acquisition financing included in the liquidity forecast", zim_on, None, "ENGINE", eng,
             note="Switched off in the company profile until the deal status is verified; not changed by the recommendations."))
    for fid, key, label, unit in (("F.zim.debt_tranche", "rates.zim_amount_usd", "ZIM acquisition debt tranche", "USD m"),
                                  ("F.zim.purchase_price", "rates.zim_consideration_usd", "ZIM acquisition purchase price", "USD m"),
                                  ("F.zim.draw_date", "rates.zim_draw_date", "ZIM tranche draw date", None),
                                  ("F.zim.spread_bp", "rates.zim_spread_bp", "ZIM tranche spread over SOFR", "bp"),
                                  ("F.zim.profile_switch", "rates.zim_tranche_on", "ZIM financing switch in the company profile", None)):
        e = params.get(key)
        if e is None:
            continue
        value = _r(float(e.value) / 1e6) if unit == "USD m" else (str(e.value) if unit is None else e.value)
        src = getattr(e, "source", None)
        source = (f"{src.report}, {src.page}" if src is not None and getattr(src, "report", None)
                  else "Company profile " + str(profile.profile_version))
        add(fact(fid, label, value, unit, e.label.value, source, rate=rate, rationale=e.rationale or None))

    # ---- engine playbook entries whose trigger is met (alert firing, situation status, preset type of a news input)
    for e in playbook or []:
        if e["trigger_type"] == "alert_rule":
            trig = [f"F.alert.{a['rule_id']}" for a in alerts
                    if a["rule_id"] == e["trigger"] or a["rule_id"].startswith(e["trigger"] + ".")]
        elif e["trigger_type"] == "status":
            trig = ["F.sit.status"] if sit["status"] == e["trigger"] else []
        else:
            trig = [f"F.news.{p['event_id']}.input.{slot_name(_slot(p))}" for p in combined["inputs"] if p["used"]
                    for prefix, sign, preset in PRESET_OF_INPUT
                    if preset == e["trigger"] and p["param_key"].startswith(prefix)
                    and (sign is None or (p["change"] > 0) == (sign > 0))]
        if trig:
            plain = PLAYBOOK_PLAIN.get(e["entry_id"])
            add(fact(f"F.playbook.{e['entry_id']}", "Standard treasury response" + (f": {plain[0].lower()} {plain[1]}" if plain else ""),
                     (f"{plain[0]} {plain[1]}" if plain else "; ".join(e["actions"])), None, "ENGINE",
                     "Standard treasury responses (calculation engine)", engine_actions=e["actions"], trigger_type=e["trigger_type"], trigger=e["trigger"],
                     trigger_facts=sorted(set(trig)), hedge_tool=e["hedge_tool"], review_owner=e["review_owner"]))

    # ---- policy and facilities
    for key, entry in sorted(params.items()):
        if not key.startswith("fx.hedge_ratio."):
            continue
        name = key.removeprefix("fx.hedge_ratio.")
        cap = entry.allowed_range[1] if entry.allowed_range else None
        closed = key in CLOSED_KEYS
        room = None if closed or cap is None else _r((cap - float(entry.value)) * 100, 0)
        add(fact(f"F.policy.{name}", f"Hedge ratio: {POLICY_NAMES.get(name, name.replace('_', ' '))}", _r(float(entry.value) * 100, 0), "%",
                 entry.label.value, f"Company profile {profile.profile_version}", cap_pct=None if cap is None else _r(cap * 100, 0),
                 room_pct_points=room, closed=closed or None, rationale=entry.rationale))
        if cap is not None and not closed:
            disclosed = "disclosed" in (entry.rationale or "").lower()
            add(fact(f"F.policy.{name}.cap", f"Hedge ratio cap: {POLICY_NAMES.get(name, name.replace('_', ' '))}", _r(cap * 100, 0), "%",
                     "DISCLOSED" if disclosed else "ASSUMPTION",
                     "Annual report (as cited in the company profile)" if disclosed else "Company profile allowed range",
                     rationale=entry.rationale))
    add(fact("F.policy.bunker", "Bunker hedging policy", BUNKER_POLICY["text"], None, "DISCLOSED", BUNKER_POLICY["source"]))
    unused = facts.get("F.liq.credit_lines_unused", {}).get("value")
    for c in registers.counterparties.counterparties:
        bank = "Bank " + c.id.rsplit("_", 1)[-1]
        add(fact(f"F.bank.{c.id}", f"Relationship bank ({bank}; names are not disclosed)", bank, None, "ASSUMPTION",
                 "Counterparty register (names not disclosed)", credit_line_share_pct=_r(c.credit_line_share * 100, 0),
                 implied_credit_line_usd_m=_r(c.credit_line_share * unused) if unused else None,
                 hedge_counterparty=c.hedge_counterparty))
    short = {"ASIA_EUROPE": "Asia–Europe", "ASIA_AMERICA": "Asia–America", "EUROPE_AMERICA": "Europe–America",
             "AFRICA_INTRA": "Africa/Intra"}
    for t in registers.trades.trades:
        add(fact(f"F.trade.{t.trade}", f"Trade lane {t.name}", short.get(t.trade, t.name), None, t.name_label.value
                 if hasattr(t.name_label, "value") else str(t.name_label),
                 f"{t.source.report}, p. {t.source.page}", aliases=sorted({t.name, short.get(t.trade, t.name)})))
    for role in OWNER_ROLES:
        add(fact(f"F.owner.{role.replace('/', '_').replace(' ', '_')}", role, role, None, "ASSUMPTION",
                 "Generic business function", kind="business function"))

    # ---- derived figures (so the model never computes)
    def der(fid, label, value, unit, of, when=None):
        # Source in words (input labels), never internal ids; the ids stay in "inputs" for the checks.
        source = "Calculated from " + "; ".join(facts[i]["label"] if i in facts else i for i in of)
        add(fact(fid, label, value, unit, "DERIVED", source, when, rate, inputs=of))
    der("F.der.materiality_threshold", f"Materiality threshold: {MATERIAL_PCT:g}% of the lowest headroom at today's prices",
        _r(bs["baseline_min_usd_m"] * MATERIAL_PCT / 100), "USD m", ["F.liq.base.min_headroom"])
    der("F.der.sit.consumed_pct", f"{NEWS}: share of the lowest headroom at today's prices that is used up", _r(ss.get("consumed_pct"), 1), "%",
        ["F.liq.base.min_headroom", "F.sit.min_headroom"])
    if bs["baseline_min_usd_m"]:
        der("F.der.sit.net_effect_pct_of_base_headroom",
            f"{NEWS}: net cash effect as % of the lowest headroom at today's prices (negative = adverse)",
            _r(net / bs["baseline_min_usd_m"] * 100, 1), "%", ["F.sit.net_cash_effect", "F.liq.base.min_headroom"])
    der("F.der.sit.days_to_min", f"{NEWS}: days from today to the low point",
        (date.fromisoformat(ss["scenario_min_date"]) - as_of).days, "days", ["F.sit.min_date"])
    der("F.der.base.days_to_min", "Today's prices: days from today to the low point",
        (date.fromisoformat(bs["baseline_min_date"]) - as_of).days, "days", ["F.liq.base.min_date"])
    total = sum(sit_rows.values())
    if abs(total) > 1e-9:
        for rid in ROWS:
            der(f"F.der.sit.share.{rid}", f"{NEWS}: {_lower(NAMES[rid])} as a share of the net cash effect", _r(sit_rows[rid] / total * 100, 1),
                "%", [f"F.sit.row.{rid}"])

    names = sorted({c.name for c in registers.counterparties.counterparties} | {c.id for c in registers.counterparties.counterparties}
                   | set(OWNER_ROLES))
    article_text = " ".join(" ".join([by_id[e]["title"], by_id[e].get("summary", "")] +
                                     [sources[a].get("title", "") for a in by_id[e].get("article_ids", []) if a in sources])
                            for e in ticked)
    pack = {"as_of": as_of.isoformat(), "horizon_months": months, "facts": facts, "names": names,
            "article_text": article_text, "events": events, "ticked": list(ticked),
            "overlaps": combined["overlaps"], "conflicts": combined["conflicts"]}
    pack["hash"] = hashlib.sha256(json.dumps(pack, sort_keys=True, default=str).encode()).hexdigest()[:20]
    return pack
