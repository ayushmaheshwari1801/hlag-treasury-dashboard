import pytest
from fastapi.testclient import TestClient

from treasury_sensitivity.api import create_app
from treasury_sensitivity.models import ValidationProblem


def test_dashboard_shape_and_reported_liquidity(service):
    d = service.dashboard(6)
    assert d["horizon_days"] == 182
    liq = d["liquidity"]
    assert liq["reported_usd_m"] == 5909 and liq["current_eur_m"] == pytest.approx(5185, abs=1)
    assert liq["headroom_eur_m"] == pytest.approx(5185 - 3500, abs=1)
    ids = [m["id"] for m in d["market_metrics"]]
    assert ids == ["fx_eur", "fx_gbp", "fx_cny", "interest", "bunker", "freight"]
    eur = next(m for m in d["market_metrics"] if m["id"] == "fx_eur")
    assert eur["change_pct"] == pytest.approx(-2.0, abs=0.01) and eur["comparison"] == "one_week"
    assert all(len(m["sparkline"]) > 5 for m in d["market_metrics"])


def test_totals_are_sums_of_cash_rows_and_interest_excluded(service):
    d = service.dashboard(6)["cash_impact"]
    cash = [r["impact_eur_m"] for r in d["rows"] if r["cash"] and r["impact_eur_m"] is not None]
    assert d["net_eur_m"] == pytest.approx(sum(cash) + (d["overlap_adjustment_eur_m"] or 0), abs=0.2)
    assert d["adverse_total_eur_m"] == pytest.approx(sum(x for x in cash if x < 0), abs=0.2)
    assert next(r for r in d["rows"] if r["id"] == "interest")["cash"] is False


def test_scenario_liquidity_is_reported_minus_adverse(service):
    d = service.dashboard(6)
    assert d["liquidity"]["scenario_eur_m"] == pytest.approx(d["liquidity"]["current_eur_m"] + d["cash_impact"]["adverse_total_eur_m"], abs=1)


def test_horizon_scales_cash_rows_but_not_interest(service):
    a, b = service.dashboard(3), service.dashboard(12)
    ra = {r["id"]: r["impact_eur_m"] for r in a["cash_impact"]["rows"]}
    rb = {r["id"]: r["impact_eur_m"] for r in b["cash_impact"]["rows"]}
    assert rb["bunker"] == pytest.approx(ra["bunker"] * 365 / 91, rel=0.01)
    assert ra["interest"] == rb["interest"]
    with pytest.raises(ValidationProblem):
        service.dashboard(5)


def test_actions_are_review_prompts_with_rule_ids(service):
    acts = service.dashboard(6)["recommended_actions"]
    assert 1 <= len(acts) <= 3 and all(a["rule_id"] and "not a trade" in a["kind"] for a in acts)
    assert acts[0]["severity"] in ("CRITICAL", "HIGH")


def test_freight_action_only_when_rates_fall(service):
    ids = {a["rule_id"] for a in service.dashboard(6)["all_actions"]}
    assert "FR-A1" not in ids       # SCFI rose in the fixture


def test_multi_currency_fx_direction_and_observed_basis(service):
    from treasury_sensitivity.engines.common import ScenarioRequest
    r = service.compute("fx", ScenarioRequest(options={"currency": "EUR", "basis": "observed_move"}))
    assert r.extra["direction"] == "receivable"
    assert r.headline.shock == pytest.approx(-2.0, abs=0.01)
    assert r.headline.impact.value < 0           # receivable loses when EUR falls
    with pytest.raises(ValidationProblem):
        service.compute("fx", ScenarioRequest(options={"currency": "JPY"}))


# ---------------------------------------------------------------- connected data
def test_liquidity_override_changes_dashboard_audits_and_reverts(service):
    base = service.dashboard(6)["liquidity"]["reported_usd_m"]
    service.set_source("liquidity", {"reported_total_usd_m": 4000, "balance_date": "2026-10-15", "source": "TMS export"}, "jenna")
    d = service.dashboard(6)
    assert d["liquidity"]["reported_usd_m"] == 4000 and d["data_sources"]["liquidity"]["source"] == "connected"
    assert any(e["event_type"] == "data_source_set" and e["actor"] == "jenna" for e in service.audit.entries(limit=20))
    service.clear_source("liquidity", "jenna")
    assert service.dashboard(6)["liquidity"]["reported_usd_m"] == base


@pytest.mark.parametrize("kind,data", [
    ("liquidity", {"reported_total_usd_m": -1, "balance_date": "2026-10-15", "source": "x"}),
    ("liquidity", {"reported_total_usd_m": 100, "balance_date": "2030-01-01", "source": "x"}),
    ("liquidity", {"reported_total_usd_m": 100, "balance_date": "2026-10-15", "source": "x", "extra": 1}),
    ("debt_maturity", {"categories": ["a"], "years": ["2027"], "values": {"2028": [1]}, "source": "x"}),
    ("limits", {"fx": {"name": "n", "amount": 0}}),
    ("exposures", {"positions": [{"id": "p"}]}),
])
def test_invalid_overrides_rejected(service, kind, data):
    with pytest.raises(ValidationProblem):
        service.set_source(kind, data, "jenna")


def test_limit_override_is_injected(service):
    service.set_source("limits", {"bunker": {"name": "Bunker limit", "amount": 100, "currency": "USD"}}, "jenna")
    r = service.compute("bunker")
    assert r.assessment.limit_utilisation is not None


def test_actor_required(service):
    with pytest.raises(ValidationProblem):
        service.set_source("liquidity", {"reported_total_usd_m": 1, "balance_date": "2026-10-15", "source": "x"}, " ")


def test_http_dashboard_and_sources(service):
    c = TestClient(create_app(service))
    assert c.get("/api/v1/sensitivity/dashboard?horizon_months=3").json()["horizon_months"] == 3
    assert c.get("/api/v1/sensitivity/dashboard?horizon_months=7").status_code == 422
    r = c.put("/api/v1/sensitivity/data-sources/liquidity", json={"actor": "j", "data": {"reported_total_usd_m": 5000, "balance_date": "2026-10-01", "source": "s"}})
    assert r.status_code == 200 and r.json()["source"] == "connected"
    assert c.put("/api/v1/sensitivity/data-sources/bogus", json={"actor": "j", "data": {}}).status_code == 404
    assert c.put("/api/v1/sensitivity/data-sources/liquidity", json={"actor": "j", "data": {"x": 1}}).status_code == 422
    assert c.delete("/api/v1/sensitivity/data-sources/liquidity?actor=j").json()["source"] == "demo"
    assert c.get("/api/v1/sensitivity/data-sources").json()["sources"]["liquidity"]["source"] == "demo"


# ---------------------------------------------------------------- what-if
def test_whatif_shocks_change_rows_and_liquidity(service):
    base = service.dashboard(6)
    d = service.dashboard(6, shocks={"fx_eur": -5, "bunker": 100})
    rows = {r["id"]: r for r in d["cash_impact"]["rows"]}
    brow = {r["id"]: r for r in base["cash_impact"]["rows"]}
    assert rows["fx_eur"]["impact_eur_m"] == pytest.approx(brow["fx_eur"]["impact_eur_m"] * 5 / 2, rel=0.01)   # linear in shock
    assert rows["bunker"]["impact_eur_m"] == pytest.approx(brow["bunker"]["impact_eur_m"] * 2, rel=0.01)
    assert rows["fx_gbp"]["impact_eur_m"] == brow["fx_gbp"]["impact_eur_m"]          # untouched slider keeps observed move
    assert d["liquidity"]["scenario_eur_m"] < base["liquidity"]["scenario_eur_m"]
    assert d["scenario"]["custom"] and next(s for s in d["scenario"]["sliders"] if s["id"] == "bunker")["value"] == 100


def test_whatif_validation(service):
    with pytest.raises(ValidationProblem):
        service.dashboard(6, shocks={"fx_jpy": 1})
    with pytest.raises(ValidationProblem):
        service.dashboard(6, shocks={"bunker": 999})


def test_presets_are_valid_and_overlap_reduces_net(service):
    d = service.dashboard(6)
    for p in d["scenario"]["presets"]:
        service.dashboard(6, shocks=p["shocks"])                    # every preset passes validation
    red = service.dashboard(6, shocks=next(p for p in d["scenario"]["presets"] if p["id"] == "red_sea")["shocks"])
    ci = red["cash_impact"]
    assert ci["overlap_adjustment_eur_m"] < 0
    cash = sum(r["impact_eur_m"] for r in ci["rows"] if r["cash"])
    assert ci["net_eur_m"] == pytest.approx(cash + ci["overlap_adjustment_eur_m"], abs=0.3)


def test_observed_sliders_match_default_rows(service):
    d = service.dashboard(6)
    obs = {s["id"]: s["observed"] for s in d["scenario"]["sliders"]}
    assert obs["interest"] == 25 and obs["bunker"] == 50 and obs["freight"] == pytest.approx(8)
    same = service.dashboard(6, shocks={"bunker": 50})
    assert {r["id"]: r["impact_eur_m"] for r in same["cash_impact"]["rows"]}["bunker"] == {r["id"]: r["impact_eur_m"] for r in d["cash_impact"]["rows"]}["bunker"]


def test_http_whatif(service):
    c = TestClient(create_app(service))
    r = c.post("/api/v1/sensitivity/dashboard", json={"horizon_months": 6, "shocks": {"interest": 50}})
    assert r.status_code == 200 and r.json()["scenario"]["shocks"] == {"interest": 50}
    assert c.post("/api/v1/sensitivity/dashboard", json={"shocks": {"bunker": 1000}}).status_code == 422


# ---------------------------------------------------------------- insights
def test_waterfall_reconciles_to_scenario_reserve(service):
    d = service.dashboard(6)
    w = d["insights"]["waterfall"]["steps"]
    assert w[0]["eur_m"] == d["liquidity"]["current_eur_m"] and w[-1]["eur_m"] == d["liquidity"]["scenario_eur_m"]
    assert w[0]["eur_m"] + sum(s["eur_m"] for s in w[1:-1]) == pytest.approx(w[-1]["eur_m"], abs=1)
    assert all(s["eur_m"] < 0 for s in w[1:-1])


def test_breakeven_shock_actually_uses_up_headroom(service):
    d = service.dashboard(6)
    be = next(b for b in d["insights"]["breakevens"] if b["id"] == "freight")
    assert be["direction"] == "fall" and be["within_range"]
    at = service.dashboard(6, shocks={"freight": be["shock"]})
    assert at["liquidity"]["scenario_headroom_eur_m"] == pytest.approx(0, abs=2)       # linear model: breakeven lands on the minimum


def test_what_changed_is_built_from_payload(service):
    d = service.dashboard(6)
    texts = [b["text"] for b in d["insights"]["what_changed"]]
    assert len(texts) >= 3
    assert any("1,685" in t and "1,526" in t for t in texts)        # reported headroom -> after impacts
    assert any("Bunker" in t and "90.3" in t for t in texts)


def test_unusual_move_stats(service):
    d = service.dashboard(6)
    eur = next(m for m in d["market_metrics"] if m["id"] == "fx_eur")["unusual"]
    assert eur["available"] and eur["weeks"] >= 40 and eur["z"] is not None
    assert eur["latest_change"] == pytest.approx(-2.0, abs=0.05)


def test_debt_insight(service):
    dbt = service.dashboard(6)["insights"]["debt"]
    assert dbt["peak_year"] == "2027" and dbt["coverage"] > 1
    assert dbt["next12_eur_m"] == round(1048 + 1618 * 10 / 12)
