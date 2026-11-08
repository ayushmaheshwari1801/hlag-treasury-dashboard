"""Fact pack for recommendation-agent/3: Event selection, the combined news forecast (saved model forecasts first,
rule-based for the rest, overlap and conflict rules) and the engine facts (read-only, EUR = USD / rate)."""
import copy
import os
import sqlite3
from datetime import date

import pytest

from treasury_gateway.fact_pack import (MAX_EVENTS, combine_event_forecasts, saved_model_forecast,
                                        select_active_events)

AS_OF = date(2026, 11, 3)
HORIZON = 180


def event(eid, title, score, published, relevance=3, summary=""):
    impact, urgency = 3, 3
    total = score  # scores are only ranked here; the v8 arithmetic is checked by the reader, not this module
    return ({"event_id": eid, "title": title, "category": "industry", "summary": summary or title,
             "score": {"impact": impact, "relevance": relevance, "urgency": urgency, "total": total,
                       "reasoning": {"impact": "x", "relevance": "x", "urgency": "x"}},
             "article_ids": [f"A_{eid}"], "why_it_matters": [], "what_to_do": []},
            {"article_id": f"A_{eid}", "title": title, "publisher": "Example Wire", "url": f"https://example.com/{eid}",
             "published_at": published})


def pool_of(*items, unscored=()):
    events = [e for e, _ in items]
    sources = [s for _, s in items]
    for eid in unscored:
        e, s = event(eid, f"Unscored {eid}", 0, "2026-10-30T00:00:00Z")
        e["score"] = None
        events.append(e), sources.append(s)
    return {"run_id": "pool_test", "events": events, "sources": sources}


def saved(eid, key, change, confidence=0.6, unit="pct", as_of=AS_OF, horizon=HORIZON, mode="model", pool="pool_test"):
    return {"forecast_id": f"forecast_{eid}_{key}", "selected_event_ids": [eid], "event_ids": [eid],
            "as_of": as_of.isoformat(), "horizon_days": horizon, "source_run_id": pool, "mode": mode,
            "predictions": [{"param_key": key, "change": change, "unit": unit, "effective_date": as_of.isoformat(),
                             "duration_days": horizon, "confidence": confidence, "rationale": f"{eid} says so",
                             "event_ids": [eid], "label": "ASSUMPTION"}]}


# ------------------------------------------------------------------------------------------- Event selection
def test_top_events_by_score_with_the_rest_not_included_and_unscored_counted():
    items = [event(f"E{i:02d}", f"Event {i}", 10 + i, f"2026-10-{10 + i:02d}T00:00:00Z") for i in range(12)]
    sel = select_active_events(pool_of(*items, unscored=["U1"]))
    assert MAX_EVENTS == 8
    assert [e["event_id"] for e in sel["included"]] == [f"E{i:02d}" for i in range(11, 3, -1)]
    assert [e["event_id"] for e in sel["not_included"]] == ["E03", "E02", "E01", "E00"]
    assert sel["unscored"] == ["U1"]


def test_equal_scores_rank_the_more_recent_event_first():
    a, b = event("EA", "Older", 50, "2026-10-01T00:00:00Z"), event("EB", "Newer", 50, "2026-10-20T00:00:00Z")
    assert [e["event_id"] for e in select_active_events(pool_of(a, b))["included"]] == ["EB", "EA"]


# ---------------------------------------------------------------------------------- combined news forecast
def test_saved_model_forecast_is_reused_and_events_without_one_use_rules():
    fuel = event("E1", "Bunker prices surge after refinery outage", 60, "2026-10-30T00:00:00Z")
    sea = event("E2", "Red Sea attacks force carriers to reroute via the Cape", 50, "2026-10-29T00:00:00Z")
    pool = pool_of(fuel, sea)
    forecasts = [saved("E1", "bunker.vlsfo_shock_pct", 12, 0.7),
                 saved("E2", "bunker.vlsfo_shock_pct", 99, as_of=date(2026, 10, 1))]  # other date: not a match
    out = combine_event_forecasts([fuel[0], sea[0]], pool, forecasts, AS_OF, HORIZON)
    assert out["origins"]["E1"] == {"origin": "model_saved", "forecast_id": "forecast_E1_bunker.vlsfo_shock_pct"}
    assert out["origins"]["E2"]["origin"] == "rule_based"
    by = {(p["event_id"], p["param_key"]): p for p in out["inputs"]}
    assert by[("E1", "bunker.vlsfo_shock_pct")]["change"] == 12 and by[("E1", "bunker.vlsfo_shock_pct")]["origin"] == "model_saved"
    assert by[("E2", "freight.rate_shock_pct.ASIA_EUROPE")]["origin"] == "rule_based"
    assert out["scenario"]["bunker"]["vlsfo_shock_pct"] == pytest.approx(0.12)
    assert out["scenario"]["freight"]["rate_shock_pct"]["ASIA_EUROPE"] == pytest.approx(0.15)


def test_no_forecast_model_is_called(monkeypatch):
    import treasury_gateway.forecast as forecast
    monkeypatch.setattr(forecast.Assistant, "_chat", lambda *a, **k: pytest.fail("a forecast model was called"))
    e = event("E1", "Quiet news", 40, "2026-10-30T00:00:00Z")
    combine_event_forecasts([e[0]], pool_of(e), [], AS_OF, HORIZON)


def test_overlap_same_direction_higher_score_wins_and_no_conflict():
    a, b = event("E1", "A", 60, "2026-10-20T00:00:00Z"), event("E2", "B", 50, "2026-10-30T00:00:00Z")
    out = combine_event_forecasts([a[0], b[0]], pool_of(a, b),
                                  [saved("E1", "bunker.vlsfo_shock_pct", 20), saved("E2", "bunker.vlsfo_shock_pct", 10)],
                                  AS_OF, HORIZON)
    assert out["overlaps"] == [{"input": "bunker.vlsfo_shock_pct", "used_event_id": "E1",
                                "not_added": [{"event_id": "E2", "change": 10, "unit": "pct"}]}]
    assert out["conflicts"] == []
    assert out["scenario"]["bunker"]["vlsfo_shock_pct"] == pytest.approx(0.20)  # never summed


def test_overlap_opposite_direction_is_a_conflict_naming_both_events():
    a, b = event("E1", "A", 60, "2026-10-20T00:00:00Z"), event("E2", "B", 50, "2026-10-30T00:00:00Z")
    out = combine_event_forecasts([a[0], b[0]], pool_of(a, b),
                                  [saved("E1", "bunker.vlsfo_shock_pct", 20), saved("E2", "bunker.vlsfo_shock_pct", -10)],
                                  AS_OF, HORIZON)
    assert out["conflicts"] == [{"input": "bunker.vlsfo_shock_pct", "used_event_id": "E1", "events": [
        {"event_id": "E1", "direction": "up", "change": 20, "unit": "pct"},
        {"event_id": "E2", "direction": "down", "change": -10, "unit": "pct"}]}]
    assert out["scenario"]["bunker"]["vlsfo_shock_pct"] == pytest.approx(0.20)


def test_ties_go_to_higher_confidence_then_the_more_recent_event():
    a, b = event("E1", "A", 50, "2026-10-30T00:00:00Z"), event("E2", "B", 50, "2026-10-20T00:00:00Z")
    out = combine_event_forecasts([a[0], b[0]], pool_of(a, b), [saved("E1", "fx.move_pct.EUR", 3, 0.5),
                                                               saved("E2", "fx.move_pct.EUR", 4, 0.8)], AS_OF, HORIZON)
    assert out["overlaps"][0]["used_event_id"] == "E2"  # same score: higher confidence
    out = combine_event_forecasts([a[0], b[0]], pool_of(a, b), [saved("E1", "fx.move_pct.EUR", 3, 0.6),
                                                               saved("E2", "fx.move_pct.EUR", 4, 0.6)], AS_OF, HORIZON)
    assert out["overlaps"][0]["used_event_id"] == "E1"  # same score and confidence: more recent


def test_saved_forecast_match_is_the_same_as_startup():
    """startup.prepare_event_impacts and the fact pack pick the same saved forecast (and neither predicts again)."""
    from treasury_gateway.startup import prepare_event_impacts
    e = event("E1", "A", 50, "2026-10-30T00:00:00Z")
    pool = pool_of(e)
    candidates = [saved("E1", "fx.move_pct.EUR", 1, mode="rule_based"), saved("E1", "fx.move_pct.EUR", 2, pool="other"),
                  saved("E1", "fx.move_pct.EUR", 3, horizon=90), saved("E1", "fx.move_pct.EUR", 4)]
    used = []

    class Agent:
        reader = type("R", (), {"cumulative": staticmethod(lambda: (pool, "pools/x.json"))})()
        forecasts = staticmethod(lambda: candidates)
        predict = staticmethod(lambda *_: pytest.fail("startup would forecast again"))

    def dashboard(forecast=None):
        if forecast is None:
            return {"finance": {"as_of": AS_OF.isoformat(), "horizon_days": HORIZON}}
        used.append(forecast)
        return {"finance": {"calculation": {"run_id": "run"}}}
    prepare_event_impacts(Agent(), dashboard)
    assert used == [saved_model_forecast(candidates, "E1", AS_OF.isoformat(), HORIZON, "pool_test")]
    assert used[0]["predictions"][0]["change"] == 4


# ------------------------------------------------------------------------------- engine facts (read-only)



@pytest.fixture(scope="module")
def built():
    pytest.importorskip("treasury_gateway.engine_adapter", reason="hlag_engine is not installed")
    from hlag_engine.company_profile.registers import load_registers
    from treasury_gateway.engine_adapter import EngineAdapter, page_market
    from treasury_gateway.fact_pack import build_fact_pack, market_alerts, market_moves_7d, situation_runs
    adapter = EngineAdapter()
    store = adapter.store.path
    before = (os.path.getmtime(store), sqlite3.connect(store).execute("SELECT count(*) FROM audit_log").fetchone()[0])
    fuel = event("E1", "Bunker prices surge after refinery outage", 60, "2026-10-30T00:00:00Z")
    sea = event("E2", "Red Sea attacks force carriers to reroute via the Cape", 50, "2026-10-29T00:00:00Z")
    rival = event("E3", "Fuel glut expected", 40, "2026-10-28T00:00:00Z")
    pool = pool_of(fuel, sea, rival)
    forecasts = [saved("E1", "bunker.vlsfo_shock_pct", 20), saved("E3", "bunker.vlsfo_shock_pct", -10)]
    events = select_active_events(pool)
    ticked = [e["event_id"] for e in events["included"]]
    combined = combine_event_forecasts([e for e in pool["events"] if e["event_id"] in ticked], pool, forecasts, AS_OF, HORIZON)
    market = page_market(adapter.shared, AS_OF)
    runs = situation_runs(adapter, AS_OF, combined["scenario"], [], market=market)
    alerts = market_alerts(adapter, AS_OF)
    moves = market_moves_7d(adapter, AS_OF, [])
    kw = dict(market_moves=moves, as_of=AS_OF, months=6, runs=runs, market=market, events=events, ticked=ticked, combined=combined,
              pool=pool, profile=adapter.shared.profile, registers=load_registers(), alerts=alerts)
    pack = build_fact_pack(**kw)
    after = (os.path.getmtime(store), sqlite3.connect(store).execute("SELECT count(*) FROM audit_log").fetchone()[0])
    return {"pack": pack, "kw": kw, "moves": moves, "runs": runs, "market": market, "before": before, "after": after,
            "build": build_fact_pack}


@pytest.mark.spec_engine
def test_nothing_is_written_to_the_engine_store(built):
    assert built["before"] == built["after"]


@pytest.mark.spec_engine
def test_fact_ids_are_stable(built):
    again = built["build"](**built["kw"])
    assert again["hash"] == built["pack"]["hash"] and list(again["facts"]) == list(built["pack"]["facts"])


@pytest.mark.spec_engine
def test_every_fact_has_its_fields_and_eur_is_usd_over_the_t0_rate(built):
    f = built["pack"]["facts"]
    rate = f["F.fx.t0_usd_per_eur"]["value"]
    for fid, x in f.items():
        assert {"id", "label", "value", "unit", "date", "source", "provenance"} <= set(x), fid
        assert x["provenance"] in {"DISCLOSED", "MARKET", "DERIVED", "PROXY", "ASSUMPTION", "ENGINE", "NEWS"}, fid
        if x["unit"] == "USD m" and isinstance(x["value"], (int, float)):
            assert x["eur_value"] == pytest.approx(x["value"] / rate, abs=0.06), fid


@pytest.mark.spec_engine
def test_situation_is_baseline_plus_news_and_derived_figures_match(built):
    f, runs = built["pack"]["facts"], built["runs"]
    sit, base = runs["situation"]["summary"], runs["baseline"]["summary"]
    assert runs["situation"]["engine_params"]["bunker"]["vlsfo_shock_pct"] == pytest.approx(20)  # E1 wins over E3
    assert "fx" not in runs["situation"]["scenario"] or not runs["situation"]["scenario"]["fx"].get("move_pct")  # no observed moves
    assert f["F.sit.headroom_change_vs_base"]["value"] == pytest.approx(sit["scenario_min_usd_m"] - sit["baseline_min_usd_m"], abs=0.06)
    assert f["F.liq.base.min_headroom"]["value"] == pytest.approx(base["baseline_min_usd_m"], abs=0.06)
    assert f["F.der.sit.consumed_pct"]["value"] == pytest.approx(sit["consumed_pct"], abs=0.06)
    assert f["F.der.sit.days_to_min"]["value"] == (date.fromisoformat(sit["scenario_min_date"]) - AS_OF).days


@pytest.mark.spec_engine
def test_market_moves_use_real_prints(built):
    f, market = built["pack"]["facts"], built["market"]
    pts = market["history"]["VLSFO_RTM"]
    level, chg4 = f["F.mkt.VLSFO_RTM.level"], f["F.mkt.VLSFO_RTM.chg_4w"]
    prev = next(p for p in reversed(pts[:-1]) if (date.fromisoformat(pts[-1]["date"]) - date.fromisoformat(p["date"])).days >= 28)
    assert chg4["compared_with"] == prev["date"] and chg4["value"] == pytest.approx((level["value"] / prev["value"] - 1) * 100, abs=0.006)
    assert f["F.mkt.ESTR.chg_1w"]["unit"] == "bp"
    assert all(date.fromisoformat(p["date"]).weekday() < 5 for p in market["history"]["FX_USD_PER_EUR"])


@pytest.mark.spec_engine
def test_news_inputs_carry_origin_overlap_and_conflict(built):
    f = built["pack"]["facts"]
    assert f["F.news.E1.input.bunker.vlsfo_shock_pct"]["origin"] == "model_saved"
    assert f["F.news.E1.input.bunker.vlsfo_shock_pct"]["used"] is True
    assert f["F.news.E3.input.bunker.vlsfo_shock_pct"]["used"] is False
    assert f["F.news.E2.input.freight.rate_shock_pct.ASIA_EUROPE"]["origin"] == "rule_based"
    c = f["F.news.conflict.bunker.vlsfo_shock_pct"]
    assert c["value"] == ["E1 up", "E3 down"] and c["used_event_id"] == "E1"


@pytest.mark.spec_engine
def test_policy_room_closed_keys_and_bunker_disclosure(built):
    f = built["pack"]["facts"]
    assert (f["F.policy.cad"]["value"], f["F.policy.cad"]["cap_pct"], f["F.policy.cad"]["room_pct_points"]) == (70, 80, 10)
    assert f["F.policy.eur_debt"]["room_pct_points"] == 0 and f["F.policy.inr_brl"]["room_pct_points"] == 0
    assert f["F.policy.eur_operating_costs"]["closed"] is True and f["F.policy.eur_dividend"]["closed"] is True
    assert "exceptional" in f["F.policy.bunker"]["value"] and f["F.policy.bunker"]["provenance"] == "DISCLOSED"


@pytest.mark.spec_engine
def test_bunker_exceptional_comes_from_the_engine(built):
    f = built["pack"]["facts"]
    assert f["F.eng.bunker_exceptional"]["value"] is (built["runs"]["situation"]["status"] in ("HIGH", "CRITICAL"))
    kw = copy.deepcopy(built["kw"])
    kw["runs"]["situation"]["status"] = "HIGH"
    assert built["build"](**kw)["facts"]["F.eng.bunker_exceptional"]["value"] is True
    kw = copy.deepcopy(built["kw"])
    kw["runs"]["situation"]["status"] = "WATCH"
    kw["runs"]["situation"]["desk_exceptions"] = [{"kind": "driver", "driver": "bunker", "side": "bunker",
                                                   "gross_adverse_usd_m": 300.0, "pct_of_headroom": 12.0, "date": "2027-01-31"}]
    pack = built["build"](**kw)
    assert pack["facts"]["F.eng.bunker_exceptional"]["value"] is True
    assert pack["facts"]["F.eng.bunker_exceptional"]["cites"] == ["F.sit.desk.bunker.1"]
    kw["runs"]["situation"]["desk_exceptions"][0].update(driver="freight", side="freight")
    assert built["build"](**kw)["facts"]["F.eng.bunker_exceptional"]["value"] is False


@pytest.mark.spec_engine
def test_assumption_facts_are_labelled(built):
    f = built["pack"]["facts"]
    assert all(x["provenance"] == "ASSUMPTION" for k, x in f.items() if k.startswith("F.eng.trend_continues."))
    assert any(k.startswith("F.eng.trend_continues.row.") for k in f)
    assert all(x["provenance"] == "ASSUMPTION" for k, x in f.items() if k.startswith("F.bank."))
    assert f["F.owner.Commercial_Pricing"]["kind"] == "business function"
    occ = f["F.occ.baseline_change_1w"]
    assert occ["available"] is False and "scheduled baseline" in occ["reason"]


@pytest.mark.spec_engine
def test_market_moves_7d_measures_each_series_as_the_tiles(built):
    """F.eng.market_moves_7d: weekly series latest vs previous print, daily series latest vs 7 days earlier, exactly the
    tiles' weekly comparison; a weekly series with a real week-on-week move gives a nonzero effect."""
    m, f, market = built["moves"], built["pack"]["facts"], built["market"]
    for sid, c in m["comparisons"].items():
        week = market["weekly"][sid]
        assert (c["previous"], c["previous_value"]) == (week["date"], week["value"]), sid
        assert (c["latest"], c["latest_value"]) == (week["last_date"], week["last_value"]), sid
    vlsfo = m["comparisons"]["VLSFO_RTM"]
    assert vlsfo["rule"] == "previous print" and vlsfo["latest_value"] != vlsfo["previous_value"]
    assert m["comparisons"]["ESTR"]["rule"].startswith("7 days earlier")
    assert abs(m["by_driver_usd_m"]["bunker"]) > 0.5                       # the weekly fuel move reaches the baseline
    assert sum(m["by_driver_usd_m"].values()) + m["other_usd_m"] == pytest.approx(m["total_usd_m"], abs=1e-6)
    x = f["F.eng.market_moves_7d"]
    assert x["provenance"] == "DERIVED" and x["label"] == "Effect of market moves over the last 7 days on the baseline forecast"
    assert x["comparisons"]["WCI_COMPOSITE"] == m["comparisons"]["WCI_COMPOSITE"]["text"]
    assert x["comparisons"]["WCI_COMPOSITE"].startswith("WCI composite (Drewry): ") and " vs " in x["comparisons"]["WCI_COMPOSITE"]
    assert f["F.eng.market_moves_7d.bunker"]["value"] == pytest.approx(m["by_driver_usd_m"]["bunker"], abs=0.06)


@pytest.mark.spec_engine
def test_net_cash_effect_and_its_share_of_baseline_headroom(built):
    f = built["pack"]["facts"]
    net, base = f["F.sit.net_cash_effect"]["value"], f["F.liq.base.min_headroom"]["value"]
    assert f["F.der.sit.net_effect_pct_of_base_headroom"]["value"] == pytest.approx(net / base * 100, abs=0.06)


@pytest.mark.spec_engine
def test_trade_lanes_are_facts(built):
    f = built["pack"]["facts"]
    names = {f[k]["value"] for k in f if k.startswith("F.trade.")}
    assert names == {"Asia–Europe", "Asia–America", "Europe–America", "Africa/Intra"}
    assert all(f[k]["provenance"] == "DISCLOSED" and "Investor Report H1 2026" in f[k]["source"] for k in f if k.startswith("F.trade."))


@pytest.mark.spec_engine
def test_fact_sources_and_labels_never_show_internal_ids(built):
    import re
    for fid, f in built["pack"]["facts"].items():
        shown = f.get("value_text", f["value"])                              # the page shows value_text when present
        text = f"{f['label']} {f['source']} " + (shown if isinstance(shown, str) else "")
        assert not re.search(r"\bF\.[a-z]+|\bcand_|hlag_engine|\bSituation\b", text), (fid, text)
        assert not re.search(r"\b[a-z]+_[a-z_]+\b|\b[a-z]+\.[a-z_]+\.[a-z_]+\b|\b[a-z_]+\.[a-z_]*_[a-z_]+\b", text), (fid, text)
        assert not re.search(r"\b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b", text), (fid, text)          # series or bank ids


@pytest.mark.spec_engine
def test_zim_acquisition_facts_and_it_stays_switched_off(built):
    f = built["pack"]["facts"]
    assert f["F.zim.in_forecast"]["value"] is False and f["F.zim.in_forecast"]["provenance"] == "ENGINE"
    assert (f["F.zim.debt_tranche"]["value"], f["F.zim.debt_tranche"]["provenance"]) == (2500.0, "DISCLOSED")
    assert (f["F.zim.purchase_price"]["value"], f["F.zim.purchase_price"]["provenance"]) == (4200.0, "DISCLOSED")
    assert "ad hoc release" in f["F.zim.purchase_price"]["source"]
    assert f["F.zim.draw_date"]["provenance"] == "ASSUMPTION" and f["F.zim.profile_switch"]["value"] == "False"
    assert not built["runs"]["situation"]["engine_params"]["rates"].get("zim_tranche_on")


def test_alert_labels_name_the_series_and_the_move():
    from treasury_gateway.fact_pack import alert_label
    div = {"severity": "severe", "window": "4w", "series": ["VLSFO_RTM", "BRENT"], "type": "divergence", "observed": 9.1}
    assert alert_label({**div, "moves": {"VLSFO_RTM": 12.2, "BRENT": 3.1}}) == \
        "Severe: VLSFO Rotterdam rose faster than Brent crude over 4 weeks (VLSFO Rotterdam +12.2%, Brent crude +3.1%)"
    assert alert_label({**div, "observed": 28.1, "moves": {"VLSFO_RTM": 13.4, "BRENT": -14.7}}) == \
        "Severe: VLSFO Rotterdam rose while Brent crude fell over 4 weeks (VLSFO Rotterdam +13.4%, Brent crude −14.7%)"
    vol = {"severity": "severe", "window": "20d", "series": ["FX_USD_PER_BRL"], "type": "vol_regime", "observed": 2.71}
    assert alert_label(vol) == "Severe: BRL volatility is stressed (short-term vs long-term ratio 2.71)"
    bp = {"severity": "warning", "window": "1w", "series": ["UST_2Y"], "type": "bp_move", "observed": -15}
    assert alert_label(bp) == "Warning: US Treasury 2Y fell 15 bp over 1 week"


def test_labels_keep_currency_codes_upper_case():
    from treasury_gateway.fact_pack import _lower
    assert _lower("CAD FX") == "CAD FX" and _lower("Bunker fuel and MFR") == "bunker fuel and MFR"
