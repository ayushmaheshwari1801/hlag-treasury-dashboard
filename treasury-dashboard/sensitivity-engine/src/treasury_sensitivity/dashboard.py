"""Composite view for the Treasury Decision Dashboard: one call, built only from engine results.

Nothing here invents numbers: every figure is an engine output, a reported figure from defaults.yaml, or a ratio of the two.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .engines.common import ScenarioRequest
from .models import D, Provenance, Status, ValidationProblem, quantize

SEVERITY = {Status.CRITICAL: 3, Status.HIGH: 2, Status.LOW: 1, Status.NOT_ASSESSABLE: 0}


def _num(x: Decimal | None, places: int = 1) -> float | None:
    if x is None:
        return None
    v = float(quantize(x, places))
    return 0.0 if v == 0 else v          # never emit negative zero


def horizon_days(defaults, months: int) -> int:
    table = defaults["dashboard"]["horizon_months_days"]
    if str(months) not in table:
        raise ValidationProblem(f"horizon_months must be one of {', '.join(table)}")
    return int(table[str(months)])


SLIDER_IDS = ("fx_eur", "fx_gbp", "fx_cny", "fx_cad", "interest", "bunker", "freight")


def sliders(d, ctx) -> list[dict]:
    """Slider specs: bounds are the engines' own shock bounds; 'observed' is this week's move (the default scenario)."""
    out = []
    fx_lo, fx_hi = d["fx"]["shock_bounds_pct"]
    for c, cfg in d["fx"]["currencies"].items():
        b = ctx.blocks[cfg["series"]]
        out.append({"id": f"fx_{c.lower()}", "label": f"{c} vs USD", "unit": "%", "min": fx_lo, "max": fx_hi, "step": 0.5,
                    "observed": None if b.change_pct is None else round(b.change_pct, 2)})
    i, bk, fr = d["interest"], d["bunker"], d["freight"]
    bi, bb, bf = ctx.blocks[i["series"]], ctx.blocks[bk["series"]], ctx.blocks[fr["series"]]
    out.append({"id": "interest", "label": "ECB deposit rate", "unit": "bps", "min": i["shock_bounds_bps"][0], "max": i["shock_bounds_bps"][1],
                "step": 5, "observed": round(bi.change) if bi.change is not None else float(i["fallback_shock_bps"])})
    out.append({"id": "bunker", "label": "Bunker (VLSFO)", "unit": "USD/t", "min": bk["shock_bounds_usd_t"][0], "max": bk["shock_bounds_usd_t"][1],
                "step": 10, "observed": round(bb.change, 1) if bb.change is not None else float(bk["fallback_shock_usd_t"])})
    out.append({"id": "freight", "label": "Freight (SCFI)", "unit": "%", "min": fr["shock_bounds_pct"][0], "max": fr["shock_bounds_pct"][1],
                "step": 1, "observed": round(bf.change_pct, 2) if bf.change_pct is not None else float(fr["fallback_shock_pct"])})
    return out


def compose(service, months: int | None = None, as_of: date | None = None, shocks: dict | None = None) -> dict:
    d = service.defaults
    months = months or int(d["dashboard"]["default_horizon_months"])
    days = horizon_days(d, months)
    ctx = service.context(as_of)
    fx_cfg = d["fx"]["currencies"]
    eurusd = ctx.eurusd
    shocks = dict(shocks or {})
    bad = set(shocks) - set(SLIDER_IDS)
    if bad:
        raise ValidationProblem("Unknown scenario key(s): " + ", ".join(sorted(bad)) + ". Use: " + ", ".join(SLIDER_IDS))
    spec = {s["id"]: s for s in sliders(d, ctx)}
    for k, v in shocks.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValidationProblem(f"{k} must be a number")
        if not spec[k]["min"] <= v <= spec[k]["max"]:
            raise ValidationProblem(f"{k}={v} is outside {spec[k]['min']} to {spec[k]['max']} {spec[k]['unit']}")
    hs = lambda k: None if k not in shocks else shocks[k]

    # ----- engine runs ------------------------------------------------------------------------------------------
    req_fx = lambda c: ScenarioRequest(overrides={"horizon_days": days}, options={"currency": c, "basis": "observed_move"},
                                       headline_shock=hs(f"fx_{c.lower()}"))
    runs: list[tuple[str, str, object]] = []           # (row id, label, ModuleResult)
    for c in fx_cfg:
        runs.append((f"fx_{c.lower()}", f"{c} FX", service.run(ctx, "fx", req_fx(c))))
    runs.append(("interest", "Interest rate", service.run(ctx, "interest", ScenarioRequest(headline_shock=hs("interest")))))
    runs.append(("bunker", "Bunker fuel", service.run(ctx, "bunker", ScenarioRequest(overrides={"horizon_days": days}, headline_shock=hs("bunker")))))
    runs.append(("freight", "Freight rates", service.run(ctx, "freight", ScenarioRequest(overrides={"horizon_days": days}, headline_shock=hs("freight")))))

    # ----- liquidity --------------------------------------------------------------------------------------------
    liq = d["liquidity"]
    liq_usd = D(liq["reported_total_usd_m"])
    bal_rate = D(liq["balance_date_eurusd"])
    liq_eur = liq_usd / bal_rate
    min_eur = D(liq["minimum_liquidity_eur_m"])
    headroom_eur = liq_eur - min_eur

    # ----- cash-impact rows (EUR m) -------------------------------------------------------------------------------
    rows, adverse_total, net_total = [], Decimal(0), Decimal(0)
    for rid, label, r in runs:
        h = r.headline
        cash = r.module != "interest"                  # Mode A is an earnings proxy: shown, never added to cash totals
        usd = None if h is None or h.impact_usd_m is None else D(h.impact_usd_m)
        eur = None if usd is None or not eurusd else usd / eurusd
        if r.module == "interest" and h is not None and h.impact.value is not None and h.impact.unit.startswith("EUR"):
            eur = D(h.impact.value)
        adverse = None if eur is None else min(eur, Decimal(0))
        if cash and eur is not None:
            net_total += eur
            adverse_total += min(eur, Decimal(0))
        rows.append({
            "id": rid, "module": r.module, "label": label, "scenario": h.label if h else None,
            "impact_eur_m": _num(eur), "cash": cash,
            "impact_type": r.impact_type.value, "provenance": (h.impact.provenance.value if h else Provenance.UNAVAILABLE.value),
            "headroom_at_risk_pct": (None if eur is None or headroom_eur <= 0 or not cash else _num(abs(adverse) / headroom_eur * 100, 1)),
            "status": r.assessment.status.value, "worst_case_status": r.worst_case_status.value, "rule_id": r.assessment.rule_id,
            "reliability": r.reliability.value, "horizon_days": r.horizon_days,
            "warnings": r.warnings[:2],
        })
    # ----- bunker surcharge overlap (compound engine rule): a freight gain partly re-counts surcharge recovery ----
    from .compound import CompoundRequest, aggregate
    by_mod = {r.module: r for _, _, r in runs if r.module in ("bunker", "freight")}
    comp = aggregate(ctx, by_mod, CompoundRequest(modules=["bunker", "freight"], target_horizon_days=days), ScenarioRequest())
    overlap_eur = None
    if comp.overlap_adjustment is not None and comp.overlap_adjustment.amount_usd_m and eurusd:
        overlap_eur = D(comp.overlap_adjustment.amount_usd_m) / eurusd
        net_total += overlap_eur
    # ----- market metrics -----------------------------------------------------------------------------------------
    metrics = []
    for rid, label, r in runs:
        if rid.startswith("fx_") and rid not in ("fx_eur", "fx_gbp", "fx_cny"):
            continue                                    # CAD shown in the modal; the grid mirrors the layout
        m = r.market
        s = ctx.series[m.series_id]
        spark = [{"date": p.date.isoformat(), "value": p.value} for p in s.history("3M")]
        impact = rows[[x[0] for x in runs].index(rid)]["impact_eur_m"]
        metrics.append({
            "id": rid, "module": r.module, "series_id": m.series_id, "name": m.name, "unit": m.unit,
            "value": m.latest_value, "date": m.latest_date.isoformat() if m.latest_date else None,
            "change": m.change, "change_unit": m.change_unit, "change_pct": m.change_pct,
            "comparison": m.comparison_mode, "freshness": m.freshness, "source": m.source, "source_kind": m.source_kind,
            "provenance": m.provenance.value, "sparkline": spark,
            "tone": None if impact is None else ("adverse" if impact < 0 else "favourable" if impact > 0 else "neutral"),
        })

    # ----- recommended actions: rule-derived, ordered by status severity then impact size --------------------------
    actions, seen = [], set()
    for rid, label, r in sorted(runs, key=lambda x: (-SEVERITY[x[2].assessment.status], -abs((x[2].headline.impact_usd_m or 0) if x[2].headline else 0))):
        for a in r.treasury_actions:
            if a.rule_id in seen:
                continue
            seen.add(a.rule_id)
            actions.append({"rule_id": a.rule_id, "module": r.module, "area": label, "text": a.action, "condition": a.condition,
                            "assumptions": a.assumptions, "severity": r.assessment.status.value,
                            "kind": "review prompt (not a trade instruction)"})
    # ----- debt ---------------------------------------------------------------------------------------------------
    dm = d["debt_maturity"]
    debt = {"unit": dm["unit"], "categories": dm["categories"], "years": dm["years"],
            "values": dm["values"], "totals": {y: sum(v) for y, v in dm["values"].items()},
            "provenance": dm["provenance"], "source": dm["source"],
            "reported_total_usd_m": dm["reported_total_usd_m"],
            "reconciliation_note": "Illustrative profile; its total does not equal the reported USD 7,153m debt and leases."}

    adverse_pct = None if headroom_eur <= 0 else _num(abs(adverse_total) / headroom_eur * 100, 1)
    insights = _insights(service, ctx, d, days, runs, rows, metrics, spec, shocks, liq_eur, min_eur, headroom_eur, adverse_total, debt)
    return {
        "insights": insights,
        "as_of": ctx.as_of, "horizon_months": months, "horizon_days": days,
        "horizons": [{"months": int(k), "days": v} for k, v in d["dashboard"]["horizon_months_days"].items()],
        "eurusd": {"value": float(eurusd) if eurusd else None, "date": ctx.eurusd_date, "provenance": ctx.eurusd_provenance.value},
        "market_metrics": metrics,
        "scenario": {"shocks": shocks, "custom": bool(shocks), "sliders": [{**v, "value": shocks.get(k, v["observed"])} for k, v in spec.items()],
                     "presets": d.raw.get("scenario_presets", []),
                     "note": "Sliders set each engine's selected shock. Unset sliders use this week's observed move. Presets are ILLUSTRATIVE."},
        "cash_impact": {"unit": "EUR m", "rows": rows, "overlap_adjustment_eur_m": _num(overlap_eur), "adverse_total_eur_m": _num(adverse_total), "net_eur_m": _num(net_total),
                        "adverse_pct_of_headroom": adverse_pct,
                        "note": "Total adds cash rows only (interest Mode A is an annualised earnings proxy). Adverse total counts adverse rows; net nets favourable ones and removes bunker surcharge recovery already counted in freight."},
        "liquidity": {
            "unit": "EUR m", "reported_usd_m": float(liq_usd), "balance_date": liq["balance_date"], "balance_date_eurusd": float(bal_rate),
            "current_eur_m": _num(liq_eur, 0), "minimum_eur_m": float(min_eur), "headroom_eur_m": _num(headroom_eur, 0),
            "scenario_eur_m": _num(liq_eur + adverse_total, 0), "scenario_headroom_eur_m": _num(headroom_eur + adverse_total, 0),
            "items_usd_m": liq["items_usd_m"], "provenance": liq["provenance"], "source": liq["source"],
            "minimum_note": "Minimum liquidity is ILLUSTRATIVE (taken from the supplied layout); the report states none.",
            "note": liq["note"], "warnings": ctx.warnings,
        },
        "debt_maturity": debt,
        "recommended_actions": actions[:3], "all_actions": actions,
    }


# ---------------------------------------------------------------------------------------------- insights
LEVEL = {"fx": ("pct", None), "bunker": ("add", "USD/t"), "freight": ("pct", "index points")}


def _insights(service, ctx, d, days, runs, rows, metrics, spec, shocks, liq_eur, min_eur, headroom_eur, adverse_total, debt) -> dict:
    from .insights import breakeven, debt_insight, eur_text, move_stats
    eurusd = ctx.eurusd
    row_by = {r["id"]: r for r in rows}
    res_by = {rid: r for rid, _, r in runs}

    # unusual moves, per market metric
    unusual = {}
    for m in metrics:
        unusual[m["id"]] = move_stats(ctx.series[m["series_id"]], in_bps=m["module"] == "interest")
    for m in metrics:
        m["unusual"] = unusual[m["id"]]

    # waterfall: reported reserve -> each adverse cash row -> scenario reserve; favourable rows listed, not counted
    steps = [{"label": "Reported reserve", "kind": "start", "eur_m": _num(liq_eur, 0)}]
    upside = []
    for r in sorted((r for r in rows if r["cash"] and r["impact_eur_m"] is not None), key=lambda r: r["impact_eur_m"]):
        if r["impact_eur_m"] < 0:
            steps.append({"label": r["label"], "id": r["id"], "kind": "down", "eur_m": r["impact_eur_m"]})
        elif r["impact_eur_m"] > 0:
            upside.append({"label": r["label"], "id": r["id"], "eur_m": r["impact_eur_m"]})
    steps.append({"label": "Scenario reserve", "kind": "end", "eur_m": _num(liq_eur + adverse_total, 0)})
    waterfall = {"unit": "EUR m", "steps": steps, "minimum_eur_m": float(min_eur), "upside_not_counted": upside,
                 "note": "Adverse cash rows only. Favourable rows are listed but not counted, so the reserve is not flattered by gains."}

    # breakevens: impact is linear in the shock, so one extra engine run per row gives the slope
    probe = {"%": 10, "bps": 10, "USD/t": 10}
    bes = []
    for sid, sp in spec.items():
        r = row_by.get(sid)
        if r is None or not r["cash"]:
            continue
        unit = sp["unit"]
        req_shock = probe[unit]
        mod = "fx" if sid.startswith("fx_") else sid
        from .engines.common import ScenarioRequest
        req = (ScenarioRequest(overrides={"horizon_days": days}, options={"currency": sid[3:].upper(), "basis": "observed_move"}, headline_shock=req_shock)
               if mod == "fx" else ScenarioRequest(overrides={"horizon_days": days}, headline_shock=req_shock))
        pr = service.run(ctx, mod, req)
        k = None
        if pr.headline is not None and pr.headline.impact_usd_m is not None and eurusd:
            k = D(pr.headline.impact_usd_m) / eurusd / D(req_shock)
        own_adverse = D(min(r["impact_eur_m"] or 0, 0))
        remaining = headroom_eur + (adverse_total - own_adverse)          # headroom left after the OTHER adverse rows
        base = res_by[sid]
        kind, lvl_unit = LEVEL[mod]
        if mod == "fx":
            spot = next((i.quantity.value for i in base.inputs if i.key == "spot"), None)
            lvl_unit = base.labels.get("quote", "")
            base_level = spot
        else:
            base_level = base.market.previous_value
        bes.append(breakeven(sid, r["label"], unit, k, remaining, (sp["min"], sp["max"]), base_level, kind, lvl_unit or ""))
    bes.sort(key=lambda b: (not b.get("available"), abs(b.get("shock") or 1e9) / (max(abs(spec[b["id"]]["min"]), abs(spec[b["id"]]["max"])) or 1)))

    # what changed: biggest move (by unusualness), largest adverse row, headroom change
    bullets = []
    scored = [(m, unusual[m["id"]]) for m in metrics if unusual[m["id"]].get("available") and unusual[m["id"]].get("latest_change")]
    if scored:
        m, u = max(scored, key=lambda x: abs(x[1]["z"]) if x[1]["z"] is not None else 99)
        from .insights import _f
        name = {"fx_eur": "EUR/USD", "fx_gbp": "GBP/USD", "fx_cny": "CNY/USD", "interest": "The ECB deposit rate", "bunker": "Bunker (VLSFO)", "freight": "Freight (SCFI)"}[m["id"]]
        ch = u["latest_change"]
        verb = "rose" if ch > 0 else "fell"
        size = f"{abs(ch):.0f} bps" if u["unit"] == "bps" else f"{abs(ch):.1f}%"
        bullets.append({"kind": "move", "id": m["id"], "tone": m["tone"], "text": f"{name} {verb} {size} this week, the most unusual move ({u['text'].split(';')[0]})."})
    adverse = [r for r in rows if r["cash"] and (r["impact_eur_m"] or 0) < 0]
    if adverse:
        w = min(adverse, key=lambda r: r["impact_eur_m"])
        bullets.append({"kind": "impact", "id": w["id"], "tone": "adverse",
                        "text": f"{w['label']} is the largest adverse cash impact ({eur_text(w['impact_eur_m'], 1)}"
                                + (f", {w['headroom_at_risk_pct']:.1f}% of headroom" if w["headroom_at_risk_pct"] else "") + ")."})
    after = headroom_eur + adverse_total
    bullets.append({"kind": "headroom", "id": "liquidity", "tone": "adverse" if adverse_total < 0 else "neutral",
                    "text": f"Headroom above the minimum falls from {eur_text(_num(headroom_eur, 0))} to {eur_text(_num(after, 0))} after the modelled "
                            f"{'what-if' if shocks else ''}{' ' if shocks else ''}impacts (" + (f"{float(-adverse_total / headroom_eur * 100):.1f}% used)." if headroom_eur > 0 else "no headroom).")})
    near = next((b for b in bes if b.get("available") and b.get("within_range")), None)
    if near:
        bullets.append({"kind": "breakeven", "id": near["id"], "tone": "adverse", "text": "Closest to trouble: " + near["text"]})

    dbt = debt_insight(debt, ctx.as_of.date(), _num(liq_eur + adverse_total, 0), _num(after, 0))
    return {"what_changed": bullets, "waterfall": waterfall, "breakevens": bes, "debt": dbt,
            "note": "Built only from the figures on this page. Breakevens assume each impact stays linear in its shock and the other rows stay as shown."}
