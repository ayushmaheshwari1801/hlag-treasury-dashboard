import json

from treasury_sensitivity.explain import ExplanationProvider, explain, guard
from treasury_sensitivity.news_context import news_for_module


class Fake:
    def __init__(self, text):
        self.text = text

    def explain(self, result_json):
        return self.text


class Boom:
    def explain(self, result_json):
        raise RuntimeError("model down")


def test_deterministic_explanation_restates_only_computed_numbers(service):
    for m in ("fx", "interest", "bunker", "freight"):
        r = service.compute(m)
        e = explain(r)
        assert e["guard"]["passed"] and guard(e["text"], r)[0]
        assert r.assessment.status.value in e["text"]


def test_guard_rejects_invented_numbers_and_wrong_status(service):
    r = service.compute("bunker")                       # HIGH, -96
    assert guard("Status HIGH under MAT-H1; the cash cost is -96 USD m (1.92% of liquidity).", r)[0]
    ok, problems = guard("The cash cost is -97 USD m.", r)
    assert not ok and "-97" in problems[0]
    ok, problems = guard("Status is CRITICAL because the cost is -96.", r)
    assert not ok and any("CRITICAL" in p for p in problems)
    ok, _ = guard("Status LOW; cost -96.", r)
    assert not ok


def test_bad_provider_text_falls_back_and_cannot_change_status(service):
    r = service.compute("bunker")
    e = explain(r, Fake("Risk is CRITICAL: expected loss USD 500m."))
    assert e["source"] == "deterministic_fallback" and not e["guard"]["passed"] and "HIGH" in e["text"] and "500" not in e["text"]
    e = explain(r, Fake("Status HIGH under MAT-H1: net cost -96 USD m."))
    assert e["source"] == "provider" and e["guard"]["passed"]
    e = explain(r, Boom())
    assert e["source"] == "deterministic_fallback" and "provider error" in e["guard"]["problems"][0]


EXPORT = {"as_of": "2026-10-30T08:00:00+08:00", "status": "partial", "coverage_gaps": ["Latest collection is partial"], "articles": [
    {"article_id": "a1", "title": "ECB raises deposit facility rate by 25 bps", "url": "https://x/1", "published_at": "2026-10-29T10:00:00+00:00",
     "category": "Macroeconomic", "summary": "Policy rate rise.", "facts": [], "score": 82, "review_status": "confirmed",
     "signals": [{"indicator": "ECB deposit facility rate", "level": "numeric", "value": 2.25, "unit": "%",
                  "change": {"previous": 2.0, "delta": 25, "unit": "bps"}, "direction": None, "source_ids": ["a1"]}]},
    {"article_id": "a2", "title": "Unconfirmed report: Rotterdam VLSFO jumps", "url": "https://x/2", "published_at": "2026-10-30T10:00:00+00:00",
     "category": "Industry", "summary": "Bunker prices.", "facts": [], "score": 60, "review_status": "pending",
     "signals": [{"indicator": "Rotterdam VLSFO", "level": "numeric", "value": 700, "unit": "USD/t",
                  "change": {"previous": 650, "delta": 50, "unit": "USD/t"}, "direction": None, "source_ids": ["a2"]}]},
    {"article_id": "a3", "title": "Rejected rumour about fuel", "url": "https://x/3", "published_at": None, "category": "Industry",
     "summary": "bunker", "facts": [], "score": 90, "review_status": "rejected", "signals": []},
    {"article_id": "a4", "title": "SCFI rises 8% on Red Sea diversions", "url": "https://x/4", "published_at": "2026-10-30T12:00:00+00:00",
     "category": "Industry", "summary": "Spot rates up.", "facts": [], "score": 70, "review_status": "confirmed",
     "signals": [{"indicator": "SCFI composite", "level": "numeric", "value": 1728, "unit": "points",
                  "change": {"previous": 1600, "delta": 128, "unit": "points"}, "direction": None, "source_ids": ["a4"]}]}]}


def export(tmp_path):
    p = tmp_path / "articles_1.json"
    p.write_text(json.dumps(EXPORT))
    return p


def test_news_is_context_only_and_candidates_are_never_applied(tmp_path):
    r = news_for_module("interest", export(tmp_path))
    assert r["context_only"] and [i["article_id"] for i in r["items"]] == ["a1"]
    c = r["items"][0]["candidate_assumptions"][0]
    assert c["override_key"] == "shock_bps" and c["suggested_value"] == 25 and c["applied"] is False and c["requires_user_confirmation"]
    f = news_for_module("freight", export(tmp_path))["items"][0]["candidate_assumptions"][0]
    assert f["override_key"] == "scfi_change_pct" and f["suggested_value"] == 8.0


def test_pending_articles_are_marked_unverified_with_no_candidates_and_rejected_are_hidden(tmp_path):
    r = news_for_module("bunker", export(tmp_path))
    assert [i["article_id"] for i in r["items"]] == ["a2"]
    assert r["items"][0]["unverified"] and r["items"][0]["candidate_assumptions"] == []


def test_news_never_changes_the_numbers(service, tmp_path):
    before = service.compute("interest").model_dump(mode="json")
    service.news_dir = tmp_path
    export(tmp_path)
    assert service.news("interest")["items"]
    assert service.compute("interest").model_dump(mode="json") == before


def test_missing_or_broken_export_is_not_an_error(tmp_path):
    assert "No saved news export" in news_for_module("fx", None)["message"]
    bad = tmp_path / "articles_bad.json"
    bad.write_text("{not json")
    assert "could not be read" in news_for_module("fx", bad)["message"]
    assert "not evidence of no risk" in news_for_module("fx", export(tmp_path))["message"]


def test_real_export_shape_size_in_value_sign_in_direction(tmp_path):
    """Shape produced by the news engine's offline demo: no 'change', value in basis points, direction 'increase'."""
    art = {"article_id": "r1", "title": "Market B raises policy rate", "url": "https://x/r", "published_at": None, "category": "Macroeconomic",
           "summary": "Central bank raised the policy rate.", "facts": [], "score": 80, "review_status": "confirmed",
           "signals": [{"indicator": "policy_rate_adjustment", "level": "numeric", "value": 50, "unit": "basis points",
                        "change": None, "direction": "increase", "source_ids": ["r1"]}]}
    p = tmp_path / "articles_real.json"
    p.write_text(json.dumps({"as_of": "2026-10-30T00:00:00+00:00", "status": "partial", "coverage_gaps": [], "articles": [art]}))
    c = news_for_module("interest", p)["items"][0]["candidate_assumptions"]
    assert [(x["override_key"], x["suggested_value"]) for x in c] == [("shock_bps", 50.0)]
    art["signals"][0]["direction"] = "decrease"
    p.write_text(json.dumps({"articles": [art]}))
    assert news_for_module("interest", p)["items"][0]["candidate_assumptions"][0]["suggested_value"] == -50.0
    art["signals"][0]["direction"] = "tightening"                     # ambiguous: no candidate
    p.write_text(json.dumps({"articles": [art]}))
    assert news_for_module("interest", p)["items"][0]["candidate_assumptions"] == []
    art["signals"][0].update(unit="USD per tonne", direction=None, value=600, indicator="bunker_fuel_price")
    p.write_text(json.dumps({"articles": [art]}))
    assert news_for_module("bunker", p)["items"] == [] or news_for_module("bunker", p)["items"][0]["candidate_assumptions"] == []   # a level is not a shock
