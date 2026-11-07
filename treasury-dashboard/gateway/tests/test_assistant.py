import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from treasury_gateway.app import DEMO_NEWS, create_app
from treasury_gateway.assistant import Assistant, check_numbers, provider_config
from treasury_sensitivity.audit import AuditStore
from treasury_sensitivity.service import SensitivityService

CFG = {"api_key": "k", "model": "test-model", "base_url": "https://llm.test/v1"}


def svc():
    return SensitivityService(audit=AuditStore(":memory:"), clock=lambda: datetime(2026, 10, 31, 9, tzinfo=ZoneInfo("Asia/Singapore")))


def provider(replies, seen=None):
    """Mock OpenAI-compatible endpoint returning the given assistant contents in order."""
    it = iter(replies)

    def handle(req: httpx.Request):
        body = json.loads(req.content)
        if seen is not None:
            seen.append(body)
        assert req.headers["authorization"] == "Bearer k" and body["model"] == "test-model"
        reply = next(it)
        if callable(reply):
            content = body['messages'][1]['content'].split('CONTEXT (JSON):\n', 1)[1].split('\n\nQUESTION:', 1)[0]
            reply = json.dumps(reply(json.loads(content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    return httpx.MockTransport(handle)


def client(assistant):
    return TestClient(create_app(service=svc(), news_dir=DEMO_NEWS, assistant=assistant))


def ask(c, **kw):
    return c.post("/api/v1/assistant/query", json={"question": "What is the biggest risk?", **kw})


@pytest.mark.parametrize('field', ['cited_rule_ids', 'cited_article_ids', 'cited_event_ids'])
@pytest.mark.parametrize('value', [None, 42, [{}], 'R1'])
def test_malformed_citation_arrays_are_not_accepted(field, value):
    assert Assistant._parse(json.dumps({'answer': 'Review liquidity.', field: value})) is None


@pytest.mark.parametrize('body', [None, [], {'choices': []}, {'choices': None},
    {'choices': [None]}, {'choices': [{'message': None}]},
    {'choices': [{'message': {'content': 42}}]}])
def test_malformed_provider_envelope_returns_fallback(body):
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(200, content=json.dumps(body))
    result = ask(client(Assistant(CFG, transport=httpx.MockTransport(reply)))).json()
    assert result['source'] == 'offline'
    assert len(calls) == 1


def test_offline_mode_answers_from_insights():
    c = client(Assistant(None))
    r = ask(c, question="How is liquidity?").json()
    from treasury_gateway.assistant import _eur
    page = c.get('/api/v1/dashboard').json()['finance']
    assert r["source"] == "offline" and _eur(page['liquidity']['scenario_eur_m']) in r["answer"]
    b = c.post("/api/v1/assistant/query", json={"mode": "briefing"}).json()
    assert b["mode"] == "briefing" and b["answer"].count("•") >= 1
    assert c.get("/api/v1/assistant/status").json()["mode"] == "offline"


def test_model_answer_passes_guard_and_filters_ids():
    seen = []
    def reply(ctx):
        value = ctx['liquidity_eur_m']['scenario_headroom']
        rules = [a['rule_id'] for a in ctx['recommended_actions'][:1]]
        return {'answer': f'Headroom is {value} EUR m.', 'cited_rule_ids': rules + ['FAKE-1'], 'cited_article_ids': ['nope']}
    c = client(Assistant(CFG, transport=provider([reply], seen)))
    r = ask(c).json()
    assert r["source"] == "model" and r["model"] == "test-model" and r["guard"]["passed"]
    assert 'FAKE-1' not in r['cited_rule_ids'] and r["cited_article_ids"] == []
    ctx = seen[0]["messages"][1]["content"]
    assert "untrusted" in seen[0]["messages"][0]["content"] and "cash_impact_eur_m" in ctx


def test_invented_number_is_repaired_once():
    bad = json.dumps({"answer": "Losses could reach 987.6 EUR m.", "cited_rule_ids": [], "cited_article_ids": []})
    def good(ctx):
        return {'answer': f"Headroom after impacts is {ctx['liquidity_eur_m']['scenario_headroom']} EUR m.",
                'cited_rule_ids': [], 'cited_article_ids': []}
    seen = []
    r = ask(client(Assistant(CFG, transport=provider([bad, good], seen)))).json()
    assert r["source"] == "model" and r["guard"]["repaired"] and "987.6" in seen[1]["messages"][-1]["content"]


def test_persistent_invention_falls_back_and_reports():
    bad = json.dumps({"answer": "Expect 987.6 EUR m.", "cited_rule_ids": [], "cited_article_ids": []})
    r = ask(client(Assistant(CFG, transport=provider([bad, bad])))).json()
    assert r["source"] == "offline" and not r["guard"]["passed"] and "987.6" in r["guard"]["problems"]
    assert "987.6" not in r["answer"]


def test_provider_error_degrades_gracefully():
    t = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    r = ask(client(Assistant(CFG, transport=t))).json()
    assert r["source"] == "offline" and "could not be reached" in r["guard"]["note"]


def test_non_json_reply_retried_then_fallback():
    r = ask(client(Assistant(CFG, transport=provider(["sure!", "still not json"])))).json()
    assert r["source"] == "offline"


def test_whatif_shocks_reach_the_context():
    seen = []
    reply = json.dumps({"answer": "Noted.", "cited_rule_ids": [], "cited_article_ids": []})
    ask(client(Assistant(CFG, transport=provider([reply], seen))), shocks={"bunker": 200})
    assert '"bunker": 200' in seen[0]["messages"][1]["content"].replace("200.0", "200")


def test_validation():
    c = client(Assistant(None))
    assert c.post("/api/v1/assistant/query", json={"question": ""}).status_code == 422
    assert c.post("/api/v1/assistant/query", json={"question": "x", "shocks": {"bunker": 9999}}).status_code == 422


def test_number_guard_rounding():
    ctx = {"a": -90.3, "b": 1526.0, "c": "fell 2.0%"}
    assert check_numbers("about −90 and 1,526, down 2.0% over 3 weeks", ctx) == []
    assert check_numbers("roughly 1,530", ctx) == []           # rounding of a large figure
    assert check_numbers("a loss of 412", ctx) == ["412"]


def test_headroom_percentage_cannot_be_rescaled_or_borrowed_from_a_market_price():
    ctx = {"monitoring": {"headroom_used_vs_baseline": "0.1%"}, "fuel_change": 14.75}
    assert check_numbers("Consumed 14.8% of headroom.", ctx)
    assert check_numbers("Headroom used versus baseline is 0.1%.", ctx) == []
    assert check_numbers("Freight headroom at risk is 2.7%.", {**ctx, "driver": 2.7}) == []


def test_assistant_monitoring_context_has_selected_date_and_explicit_percent():
    from treasury_gateway.assistant import build_context
    composite = client(Assistant(None)).get('/api/v1/dashboard').json()
    composite['finance']['calculation'] = {'consumed_pct': 0.147594, 'baseline_min_usd_m': 2906, 'status': 'LOW'}
    composite['finance']['liquidity']['scenario_min_date'] = '2026-12-31'
    ctx = build_context(composite)
    assert ctx['monitoring']['headroom_used_vs_baseline'] == '0.1%'
    assert ctx['monitoring']['minimum_date'] == '2026-12-31'
    assert 'baseline_min_usd_m' not in ctx['calculation']


def test_provider_config_from_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("OPENROUTER_API_KEY=abc\nLLM_MODEL=m1\n")
    assert provider_config({}, [f]) == {"api_key": "abc", "model": "m1", "base_url": "https://openrouter.ai/api/v1"}
    assert provider_config({"LLM_MODEL": "m2"}, [f])["model"] == "m2"          # environment wins
    assert provider_config({}, []) is None
