"""Hedge recommendation agent: news time window, engine hedge tools, effect grading, model tool loop and API.

Engine tests run hlag_engine read-only (write=False) against its default store; the model is a scripted mock
transport, so no provider is called."""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from treasury_gateway.recommendation_agent import grade_effect, select_events

AS_OF = date(2026, 11, 3)
FUEL_UP = {'bunker': {'vlsfo_shock_pct': .3, 'hsfo_shock_pct': .3}, 'name': 'Fuel +30%', 'start_date': AS_OF.isoformat()}
EXAMPLE = Path(__file__).resolve().parents[3] / 'news-engine-v8/examples/event-result.json'


# ---------------------------------------------------------------- pure functions (no engine)
def _payload():
    src = lambda aid, ts: {'article_id': aid, 'url': f'https://x/{aid}', 'title': aid, 'publisher': 'P', 'published_at': ts}
    ev = lambda eid, arts, total: {'event_id': eid, 'title': eid, 'category': 'industry', 'article_ids': arts,
                                   'summary': 's', 'score': {'total': total}, 'why_it_matters': [], 'what_to_do': []}
    return {'sources': [src('A1', '2026-10-01T08:00:00Z'), src('A2', '2026-10-20T08:00:00Z'), src('A3', '2026-10-28T08:00:00Z'),
                        src('A4', None)],
            'events': [ev('E1', ['A1'], 40), ev('E2', ['A2', 'A3'], 60), ev('E3', ['A4'], 90)]}


def test_time_window_uses_latest_article_and_skips_undated():
    events, notes = select_events(_payload(), date(2026, 10, 15), date(2026, 10, 31))
    assert [e['event_id'] for e in events] == ['E2'] and events[0]['latest_published_at'].startswith('2026-10-28')
    assert any('E3' in n for n in notes)
    events, _ = select_events(_payload(), None, date(2026, 10, 25))      # E2's latest article is after the window
    assert [e['event_id'] for e in events] == ['E1']
    events, notes = select_events(_payload(), date(2026, 10, 1), None, ['E1', 'E9'])
    assert [e['event_id'] for e in events] == ['E1'] and any('E9' in n for n in notes)
    events, _ = select_events(_payload(), date(2026, 9, 1), date(2026, 12, 1))
    assert [e['event_id'] for e in events] == ['E2', 'E1']             # ranked by v8 score


def test_grade_rules():
    ok = {'passed': True}
    assert grade_effect(10, 100, None, None, {'passed': False})[0] == 'counterproductive'
    assert grade_effect(-5, 100, None, None, ok)[0] == 'counterproductive'
    assert grade_effect(10, 100, '2027-01-31', None, ok)[0] == 'resolves_breach'
    assert grade_effect(10, 0, None, None, ok)[0] == 'not_needed'
    assert grade_effect(30, 100, None, None, ok)[0] == 'material'
    assert grade_effect(10, 100, None, None, ok)[0] == 'marginal'
    assert grade_effect(0, 100, None, None, ok)[0] == 'no_effect'
    # A shortfall the scenario causes on its own is not the hedge's fault when the hedge posts no margin.
    assert grade_effect(0, 100, '2027-02-28', '2027-02-28', {'passed': False, 'hedge_posts_margin': False})[0] == 'no_effect'
    assert grade_effect(0, 100, None, None, {'passed': False, 'hedge_posts_margin': True})[0] == 'counterproductive'


# ---------------------------------------------------------------- engine tools
@pytest.mark.parametrize('rec', [None, 'invalid', {'candidate_id': []},
    {'candidate_id': 'cand_1', 'conditions': None, 'alternatives': []},
    {'candidate_id': 'cand_1', 'conditions': [], 'alternatives': [42]}])
def test_model_rejects_malformed_recommendations(rec):
    from types import SimpleNamespace
    from treasury_gateway.recommendation_agent import RecommendationAgent
    final, problems = RecommendationAgent(None)._accept(
        SimpleNamespace(candidates={}), {'summary': 'Review hedges.', 'recommendations': [rec]}, [])
    assert problems and final == {}


spec = pytest.mark.spec_engine


@pytest.fixture(scope='module')
def adapter():
    from treasury_gateway.engine_adapter import EngineAdapter, default_store_path
    if not default_store_path().is_file():
        pytest.skip('hlag_engine market store not available')
    return EngineAdapter()


@pytest.fixture(scope='module')
def fuel_lab(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab
    lab = HedgeLab(adapter, AS_OF, FUEL_UP, max_runs=20)
    lab.diag = lab.diagnose()
    lab.c25 = lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 12)
    lab.c50 = lab.test_hedge('bunker', 'swap', 'fuel_all', 50, 12)
    return lab


@spec
def test_diagnosis_finds_binding_date_and_module(fuel_lab):
    d = fuel_lab.diag
    assert d['adverse_modules'] == ['bunker'] and d['headroom_consumed_usd_m'] > 0
    assert d['scenario_min_date'] and d['binding_date_precision'] in {'day', 'month (accurate to the month)'}
    assert d['largest_adverse_lines'][0]['module'] == 'bunker' and d['largest_adverse_lines'][0]['delta_usd_m'] < 0
    assert d['run_id'].startswith('spec_')


@spec
def test_hedge_effect_comes_from_engine_runs(fuel_lab):
    c = fuel_lab.c25
    e = c['effect']
    assert e['min_headroom_change_usd_m'] == pytest.approx(e['min_headroom_with_usd_m'] - e['min_headroom_without_usd_m'], abs=2e-3)
    assert e['min_headroom_change_usd_m'] > 0 and c['grade'] in {'material', 'marginal', 'resolves_breach'}
    assert e['min_headroom_without_usd_m'] == pytest.approx(fuel_lab.diag['scenario_min_headroom_usd_m'])
    assert len(set(c['run_ids'].values())) == 4                       # four distinct, traceable runs
    assert c['timing']['binding_date_without'] == fuel_lab.diag['scenario_min_date']
    cover = c['timing']['cover_needed_from']
    assert cover == fuel_lab.diag['first_adverse_cash_date_by_module']['bunker'] <= c['timing']['binding_date_without']
    assert c['timing']['starts_after_first_adverse_cash'] is False and 'act_by' not in c['timing']
    assert c['margin_test']['applicable'] is True                     # a swap posts margin
    # a swap is symmetric: what it saves over the horizon if fuel rises, it gives up if fuel falls
    assert e['cost_if_reverses_over_horizon_usd_m'] == pytest.approx(e['net_cash_change_over_horizon_usd_m'], rel=1e-3)
    assert e['cost_if_reverses_over_horizon_usd_m'] > 0
    # a bigger hedge offsets more of the bunker bar; the larger one is flagged when it exceeds the net loss
    assert fuel_lab.c50['effect']['driver_bar_change_usd_m'] > e['driver_bar_change_usd_m'] > 0
    for cand in (c, fuel_lab.c50):
        assert cand['effect']['over_hedge'] == (cand['effect']['offset_of_driver_loss_pct'] > 100)


@spec
def test_same_candidate_is_not_rerun_and_bad_calls_cost_nothing(fuel_lab):
    from treasury_gateway.recommendation_agent import ToolError
    used = fuel_lab.runs_used
    assert fuel_lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 12)['candidate_id'] == fuel_lab.c25['candidate_id']
    for call in (lambda: fuel_lab.test_hedge('bunker', 'swap', 'freight', 25, 12),
                 lambda: fuel_lab.test_hedge('bunker', 'swap', 'fuel_all', 150, 12),
                 lambda: fuel_lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 48),
                 lambda: fuel_lab.test_hedge('rates', 'fixed_price_supply', 'floating_debt_usd', 25, 12),
                 lambda: fuel_lab.test_hedge('fx', 'forward', 'x', 25, 12),
                 lambda: fuel_lab.test_hedge_ratio('fx.hedge_ratio.cad', 0.95),      # above the 80% cap
                 lambda: fuel_lab.test_hedge_ratio('bunker.mfr_share', 0.5),
                 lambda: fuel_lab.liquidity_path(candidate_id='cand_unknown')):
        with pytest.raises(ToolError):
            call()
    assert fuel_lab.runs_used == used


@spec
def test_liquidity_path_is_daily_near_term(fuel_lab):
    p = fuel_lab.liquidity_path(candidate_id=fuel_lab.c25['candidate_id'])
    days = [date.fromisoformat(x['date']) for x in p['points']]
    assert days and (days[1] - days[0]).days == 1


@spec
def test_run_budget_is_enforced(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab, ToolError
    lab = HedgeLab(adapter, AS_OF, FUEL_UP, max_runs=4)
    lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 12)              # 2 base + 2
    with pytest.raises(ToolError, match='budget'):
        lab.test_hedge('bunker', 'swap', 'fuel_all', 50, 12)
    assert lab.runs_used == 4


# ---------------------------------------------------------------- model tool loop (scripted provider)
CONFIG = {'api_key': 'k', 'model': 'test-model', 'base_url': 'https://llm.test/v1'}


@pytest.mark.parametrize('message', [None, {'content': 42}, {'content': None, 'tool_calls': [None]},
    {'content': None, 'tool_calls': [{'id': 'c1', 'function': {'name': 'test_hedge', 'arguments': {}}}]}])
def test_malformed_tool_message_fails_with_validation_error(message):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': [{'message': message}]}))
    with pytest.raises(ValueError):
        RecommendationAgent(CONFIG, transport=transport)._chat([])


def _call(i, name, args):
    return {'id': f'c{i}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


def _scripted(steps):
    """Each step receives the request messages and returns the assistant message."""
    seen = []

    def handler(request):
        body = json.loads(request.content)
        assert body['tools'] and body['model'] == 'test-model'
        step = steps[len(seen)]
        seen.append(body['messages'])
        return httpx.Response(200, json={'choices': [{'message': step(body['messages'])}]})
    return httpx.MockTransport(handler), seen


def _last_tool(messages):
    return json.loads([m for m in messages if m['role'] == 'tool'][-1]['content'])


@spec
def test_model_tests_hedges_and_numbers_are_attached_by_the_engine(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab, RecommendationAgent

    def submit(text, cid):
        return {'summary': text, 'recommendations': [{
            'candidate_id': cid, 'title': 'Review a fuel swap', 'rationale': 'Fuel purchases drive the headroom loss.',
            'why_now': 'Headroom is lowest at the binding date.', 'conditions': ['Verify fuel commitments.'],
            'alternatives': ['Keep monitoring.']}]}

    steps = [
        lambda m: {'content': None, 'tool_calls': [_call(1, 'test_hedge', {'module': 'bunker', 'instrument': 'swap',
                                                                           'target': 'fuel_all', 'coverage_pct': 25, 'tenor_months': 12}),
                                                   _call(2, 'test_hedge', {'module': 'bunker', 'instrument': 'swap',
                                                                           'target': 'freight', 'coverage_pct': 25, 'tenor_months': 12})]},
        # an invented figure is refused and sent back
        lambda m: {'content': None, 'tool_calls': [_call(3, 'submit_recommendations',
                                                         submit('The swap saves USD 987.6m.', json.loads(
                                                             [x for x in m if x['role'] == 'tool'][0]['content'])['candidate_id']))]},
        lambda m: {'content': None, 'tool_calls': [_call(4, 'submit_recommendations',
                                                         submit('One fuel swap is worth a review.', json.loads(
                                                             [x for x in m if x['role'] == 'tool'][0]['content'])['candidate_id']))]},
    ]
    transport, seen = _scripted(steps)
    lab = HedgeLab(adapter, AS_OF, FUEL_UP)
    out = RecommendationAgent(CONFIG, transport=transport).run(lab, {'events': [{'event_id': 'E1'}]}, 'model')
    assert out['origin'] == 'hybrid' and out['source'] == 'model' and out['model'] == 'test-model'
    assert out['guard'] == {'checked': True, 'passed': True, 'repaired': True}
    assert 'error' in _last_tool(seen[2]) and '987.6' in _last_tool(seen[2])['error']
    assert any(not c['ok'] and 'target' in c['error'] for c in out['tool_calls'])      # the bad target was refused
    [rec] = out['recommendations']
    cand = lab.candidates[rec['candidate_id']]
    assert rec['effect'] == cand['effect'] and rec['run_ids'] == cand['run_ids']
    assert rec['record']['expected_effect']['min_headroom_change_usd_m'] == cand['effect']['min_headroom_change_usd_m']
    assert rec['record']['trigger'] == {'kind': 'event', 'id': 'E1'} and rec['record']['status'] == 'proposed'
    assert rec['record']['lever'] == 'hypothetical_hedge' and out['runs_used'] == 4
    # the model saw the diagnosis and options in its first message, and news is labelled untrusted in the prompt
    first = seen[0]
    assert 'untrusted' in first[0]['content'] and '"diagnosis"' in first[1]['content']


@spec
def test_provider_failure_falls_back_to_rule_planner(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab, RecommendationAgent
    transport = httpx.MockTransport(lambda r: httpx.Response(500, json={}))
    lab = HedgeLab(adapter, AS_OF, FUEL_UP)
    out = RecommendationAgent(CONFIG, transport=transport).run(lab, {'events': []}, 'auto')
    assert out['origin'] == 'rule_based' and 'RuntimeError' in out['guard']['note']
    assert out['recommendations'] and all(r['module'] == 'bunker' for r in out['recommendations'])
    assert out['recommendations'][0]['record']['trigger']['kind'] == 'run'
    assert [r['rank'] for r in out['recommendations']] == list(range(1, len(out['recommendations']) + 1))
    assert out['runs_used'] <= out['run_budget']


# ---------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path, adapter):
    from fastapi.testclient import TestClient
    from treasury_gateway.app import create_app
    from treasury_gateway.assistant import Assistant
    from treasury_gateway.news_v8 import NewsV8Reader
    from treasury_sensitivity.audit import AuditStore
    from treasury_sensitivity.service import SensitivityService
    NewsV8Reader(tmp_path / 'news').import_result(json.loads(EXAMPLE.read_text(encoding='utf-8')))
    svc = SensitivityService(audit=AuditStore(':memory:'),
                             clock=lambda: datetime(2026, 11, 3, 9, tzinfo=ZoneInfo('Europe/Berlin')))
    app = create_app(service=svc, news_dir=tmp_path / 'news', state_db=tmp_path / 'state.sqlite', assistant=Assistant(None))
    return TestClient(app), tmp_path / 'state.sqlite'


@spec
def test_api_session_is_saved_and_has_no_look_ahead(client):
    c, state = client
    assert c.post('/api/v1/recommendations/agent', json={'news_from': '2026-11-01', 'news_to': '2026-11-02'}).status_code == 422
    assert c.post('/api/v1/recommendations/agent', json={'news_to': '2026-11-05', 'as_of': '2026-11-03'}).status_code == 422
    assert c.post('/api/v1/recommendations/agent', json={'news_from': '2026-11-02', 'news_to': '2026-11-01'}).status_code == 422
    r = c.post('/api/v1/recommendations/agent', json={'news_from': '2026-10-25', 'news_to': '2026-10-31',
                                                      'as_of': '2026-11-03', 'shocks': {'bunker': 200}})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s['origin'] == 'rule_based' and s['news_window'] == {'from': '2026-10-25', 'to': '2026-10-31'}
    assert s['event_ids'] and s['forecast_id'].startswith('forecast_') and s['read_only'] is True
    assert s['diagnosis']['adverse_modules'] == ['bunker'] and s['candidates']
    assert c.get(f"/api/v1/recommendations/agent/{s['session_id']}").json()['recommendations'] == s['recommendations']
    assert c.get('/api/v1/recommendations/agent').json()['sessions'][0]['session_id'] == s['session_id']
    assert s['news_run_id'] and c.get('/api/v1/recommendations/agent').json()['sessions'][0]['news_run_id'] == s['news_run_id']
    # the page reuses saved advice only when this equals the dashboard's news.run_id
    assert s['news_run_id'] == c.post('/api/v1/dashboard', json={}).json()['news']['run_id']
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT COUNT(*) FROM intelligence_audit WHERE action='recommendation_session'").fetchone()[0] == 1


def test_api_refused_with_mock_engine(tmp_path):
    from fastapi.testclient import TestClient
    from treasury_gateway.app import create_app
    from treasury_gateway.assistant import Assistant
    app = create_app(news_dir=tmp_path / 'news', state_db=tmp_path / 'state.sqlite', assistant=Assistant(None),
                     cash_engine='mock', market_db=Path(__file__).resolve().parents[3] / 'market_data.sqlite')
    assert TestClient(app).post('/api/v1/recommendations/agent', json={}).status_code == 409


@spec
def test_saved_forecast_uses_original_news_and_rejects_backdating(client, monkeypatch):
    from treasury_gateway.forecast import ForecastAgent
    from treasury_gateway.news_v8 import NewsV8Reader
    from treasury_gateway.recommendation_agent import RecommendationAgent, RecommendationSessions
    c, _ = client
    payload = _payload()
    forecast = {'forecast_id': 'forecast_saved', 'source_run_id': 'original_snapshot',
                'as_of': AS_OF.isoformat(), 'selected_event_ids': ['E1'], 'scenario': {}}
    monkeypatch.setattr(ForecastAgent, 'get', lambda self, fid: forecast)
    def snapshot(self, run_id):
        assert run_id == 'original_snapshot'
        return payload
    monkeypatch.setattr(NewsV8Reader, 'saved_run', snapshot)
    def no_current_pool(self):
        raise AssertionError('A saved forecast must not read the current news pool')
    monkeypatch.setattr(NewsV8Reader, 'cumulative', no_current_pool)
    monkeypatch.setattr(RecommendationAgent, 'run', lambda self, lab, context, mode: {})
    monkeypatch.setattr(RecommendationSessions, 'save', lambda self, session: session)
    response = c.post('/api/v1/recommendations/agent', json={'forecast_id': 'forecast_saved'})
    assert response.status_code == 200, response.text
    assert response.json()['events'][0]['title'] == 'E1'
    assert response.json()['forecast']['source_run_id'] == 'original_snapshot'
    assert c.post('/api/v1/recommendations/agent', json={
        'forecast_id': 'forecast_saved', 'as_of': '2026-11-02'}).status_code == 422
    assert c.post('/api/v1/recommendations/agent', json={
        'forecast_id': 'forecast_saved', 'news_to': '2026-11-04'}).status_code == 422
    monkeypatch.setattr(NewsV8Reader, 'saved_run', lambda self, rid: None)
    assert c.post('/api/v1/recommendations/agent', json={'forecast_id': 'forecast_saved'}).status_code == 409


# Broader actions share the same model loop and persistence format as tested hedges.
def review_action(**changes):
    return {"kind": "working_capital", "candidate_id": None, "event_ids": ["E1"],
            "title": "Review collection terms", "rationale": "Freight receipts may be delayed.",
            "why_now": "Review before affected invoices become due.",
            "conditions": ["Verify actual invoices and customer terms."],
            "alternatives": ["Monitor receipts if no exposure is confirmed."], **changes}


@pytest.mark.parametrize("changes", [
    {"event_ids": ["unknown"]}, {"event_ids": []}, {"event_ids": [["E1"]]},
    {"candidate_id": "unproven"}, {"effect": {"saving": 100}},
    {"kind": "execute_payment"}, {"conditions": []}, {"title": ""},
])
def test_review_action_rejects_unverified_evidence_and_effects(changes):
    from types import SimpleNamespace
    from treasury_gateway.recommendation_agent import RecommendationAgent
    _, errors = RecommendationAgent(None)._accept(SimpleNamespace(candidates={}),
        {"summary": "Review collections.", "recommendations": [review_action(**changes)]},
        [{"events": [{"event_id": "E1"}]}])
    assert errors


@spec
def test_model_can_recommend_collection_review_without_testing_a_hedge(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab, RecommendationAgent
    transport, _ = _scripted([lambda m: {"content": None, "tool_calls": [
        _call(1, "submit_recommendations", {"summary": "Review collection terms before choosing a hedge.",
                                          "recommendations": [review_action()]})]}])
    lab = HedgeLab(adapter, AS_OF, FUEL_UP)
    result = RecommendationAgent(CONFIG, transport=transport).run(lab, {"events": [{"event_id": "E1"}]}, "model")
    assert result["source"] == "model" and result["guard"]["passed"]
    assert result["candidates"] == []
    [rec] = result["recommendations"]
    assert rec["kind"] == "working_capital" and rec["rank"] == 1
    assert rec["effect"] is None and rec["grade"] == "unquantified"
    assert "record" not in rec and rec["event_ids"] == ["E1"]


@spec
def test_preset_and_edited_forecast_reach_recommendation_engine(client, monkeypatch):
    from treasury_gateway.forecast import ForecastAgent
    from treasury_gateway.recommendation_agent import RecommendationAgent, RecommendationSessions
    c, _ = client
    def no_forecast(*args, **kwargs):
        raise AssertionError('An explicit scenario must not be replaced by a news forecast')
    monkeypatch.setattr(ForecastAgent, 'predict', no_forecast)
    monkeypatch.setattr(RecommendationAgent, 'run', lambda self, lab, context, mode: {'received':context['scenario_inputs']})
    monkeypatch.setattr(RecommendationSessions, 'save', lambda self, session: session)
    response = c.post('/api/v1/recommendations/agent', json={
        'news_from':'2026-10-25','news_to':'2026-10-31','as_of':'2026-11-03',
        'scenario':{'name':'Oil spike QA', 'start_date':'2026-11-03', 'bunker':{'vlsfo_shock_pct':0.3}}})
    assert response.status_code == 200, response.text
    assert response.json()['received']['bunker']['vlsfo_shock_pct'] == 0.3
    assert response.json()['forecast']['mode'] == 'manual'


@spec
def test_fully_hedged_operating_costs_are_not_ranked_as_residual_losses(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab
    lab = HedgeLab(adapter, AS_OF, {'name': 'EUR coverage', 'fx': {'move_pct': {'EUR': .1}},
        'overrides': {'fx.hedge_ratio.eur_operating_costs': 1.0}})
    result = lab.diagnose()
    assert not any(r['line_code'] == 'FX_OPERATING_COST' for r in result['largest_adverse_lines'])
    assert any(r['line_code'] == 'DIVIDEND' for r in result['largest_adverse_lines'])
    assert 'Residual cash' in result['cash_basis']


@spec
def test_consumption_only_fuel_scenario_does_not_test_price_hedges(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab, RecommendationAgent, ToolError
    scenario = {'bunker': {'consumption_uplift_pct': .2}, 'name': 'More fuel burn', 'start_date': AS_OF.isoformat()}
    lab = HedgeLab(adapter, AS_OF, scenario, max_runs=20)
    assert lab.diagnose()['bunker_driver'] == 'consumption'
    with pytest.raises(ToolError, match='consumption'):
        lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 12)
    assert lab.runs_used == 2  # only the shared base pair
    out = RecommendationAgent(None).run(HedgeLab(adapter, AS_OF, scenario, max_runs=20), {'events': []})
    assert not [c for c in out['candidates'] if c['module'] == 'bunker']
    assert any(r['kind'] == 'operations' for r in out['recommendations'])
    assert HedgeLab(adapter, AS_OF, FUEL_UP).bunker_driver() == 'price'


@spec
def test_identical_requests_in_flight_share_one_run(client, monkeypatch):
    """Two open pages preparing the same advice must not start two paid runs."""
    import threading, time
    from treasury_gateway import recommendation_agent as ra
    calls, original = [], ra.RecommendationAgent.run
    def slow_run(self, *args, **kwargs):
        calls.append(1)
        time.sleep(1.5)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ra.RecommendationAgent, 'run', slow_run)
    c, _ = client
    body = {'news_from': '2026-10-25', 'news_to': '2026-10-31', 'as_of': '2026-11-03'}
    out = []
    threads = [threading.Thread(target=lambda: out.append(c.post('/api/v1/recommendations/agent', json=body).json())) for _ in range(2)]
    for t in threads:
        t.start(); time.sleep(0.2)
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert out[0]['session_id'] == out[1]['session_id']
    assert len(c.get('/api/v1/recommendations/agent').json()['sessions']) == 1


@spec
def test_tracker_events_request_uses_exactly_those_events(client):
    """The advice card sends the News Tracker Events (top six plus pins) without a start date."""
    c, _ = client
    window = c.post('/api/v1/recommendations/agent', json={'news_from': '2026-10-25', 'news_to': '2026-10-31', 'as_of': '2026-11-03'})
    assert window.status_code == 200, window.text
    chosen = window.json()['event_ids'][:1]
    r = c.post('/api/v1/recommendations/agent', json={'news_to': '2026-11-03', 'as_of': '2026-11-03', 'event_ids': chosen})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s['event_ids'] == chosen and s['news_window'] == {'from': None, 'to': '2026-11-03'}
    listed = c.get('/api/v1/recommendations/agent').json()['sessions'][0]
    assert listed['event_ids'] == chosen and listed['news_run_id'] == s['news_run_id']
