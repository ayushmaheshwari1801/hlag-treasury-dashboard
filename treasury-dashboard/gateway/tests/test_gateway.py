"""Contracts for the authorized v8 replacement and cash engine integration."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from treasury_gateway.app import create_app
from treasury_gateway.assistant import Assistant
from treasury_gateway.news_v8 import NewsV8Reader
from treasury_sensitivity.audit import AuditStore
from treasury_sensitivity.service import SensitivityService


@pytest.fixture
def client(tmp_path):
    source = Path(__file__).resolve().parents[3] / 'news-engine-v8/examples/event-result.json'
    payload = json.loads(source.read_text(encoding='utf-8'))
    payload['events'][0]['title'] = 'Fuel and freight supply disruption'
    NewsV8Reader(tmp_path / 'news').import_result(payload)
    svc = SensitivityService(audit=AuditStore(':memory:'),
        clock=lambda: datetime(2026, 10, 31, 9, tzinfo=ZoneInfo('Asia/Singapore')))
    return TestClient(create_app(service=svc, news_dir=tmp_path / 'news', state_db=tmp_path / 'state.sqlite',
                                 market_db=tmp_path / 'missing.sqlite', assistant=Assistant(None)))


def test_composite_uses_v8_only(client):
    d = client.get('/api/v1/dashboard').json()
    assert d['finance']['liquidity']['reported_usd_m'] == 3150 + 725 + 2035
    assert d['news']['available'] and d['news']['source'] == 'v8'
    assert all(item['schema_version'] == 8 for item in d['news']['items'])


def test_original_articles_and_event_score_scope(client):
    items = client.get('/api/v1/news/articles?module=bunker').json()['items']
    assert items
    article = client.get('/api/v1/news/articles/' + items[0]['id']).json()
    assert article['original_article']['text'] and article['score_scope'] == 'event'
    assert article['candidate_assumptions'] == {}
    assert client.get('/api/v1/news/articles/unknown').status_code == 404


def test_context_import_preserves_facts_and_forecasts_separately(client):
    signals = client.get('/api/v1/intelligence/signals').json()['signals']
    assert signals and all(s['confidence'] == 0 and s['scenario'] is None for s in signals)
    assert client.get('/api/v1/intelligence/adjustments').json() == {'adjustments': []}
    brief = client.get('/api/v1/news/brief').json()
    assert brief['available'] and brief['version'] == 8 and brief['week_start'] is None


def test_missing_sources_are_honest(client):
    snap = client.get('/api/v1/market/snapshot').json()
    assert snap['total'] == 20 and snap['available'] == 0
    assert all(s['status'] == 'MISSING' for s in snap['series'])
    run = client.get('/api/v1/risk/dashboard?as_of=2026-11-02').json()
    assert run['flags']['incomplete_data'] and not run['flags']['demo']
    assert run['missing_inputs']


def test_demo_zero_shock_record_and_playbook(client):
    body = {'as_of': '2026-11-02', 'demo': True}
    first = client.post('/api/v1/risk/run', json=body)
    assert first.status_code == 200, first.text
    r = first.json()
    assert r['flags']['demo'] and r['baseline_path'] == r['scenario_path']
    assert all(line['delta_usd_m'] == 0 for line in r['lines'])
    assert client.post('/api/v1/risk/run', json=body).status_code == 200
    assert client.get('/api/v1/risk/runs/' + r['run_id']).json() == r
    assert client.post('/api/v1/risk/runs/' + r['run_id'] + '/replay').json()['result'] == r
    advice = client.post('/api/v1/risk/recommendations', json={'run_id': r['run_id']}).json()
    assert advice['source'] == 'deterministic_playbook' and advice['model'] is None


def test_manual_forecast_shock_changes_liquidity(client):
    r = client.post('/api/v1/risk/run', json={'as_of': '2026-11-02', 'demo': True,
        'scenario': {'bunker': {'vlsfo_shock_pct': .3}}}).json()
    assert r['attribution']['bunker'] < 0 and r['baseline_path'] != r['scenario_path']
    assert abs(sum(r['attribution'][d] for d in ('freight','bunker','rates','fx')) - r['attribution']['total']) < 1e-5


def test_profile_versions_and_no_unknown_keys(client):
    original = client.get('/api/v1/risk/profile').json()
    r = client.put('/api/v1/risk/profile', json={'actor': 'Treasury admin', 'values': {
        'liquidity.capex_monthly_usd_m': {'value': 100, 'label': 'ASSUMPTION', 'source': 'User test estimate'}}})
    assert r.status_code == 200, r.text
    assert r.json()['profile_version'] != original['profile_version']
    assert client.get('/api/v1/risk/profile').json() == r.json()
    assert client.put('/api/v1/risk/profile', json={'actor': 'admin', 'values': {'unknown': {'value': 1}}}).status_code == 422


def test_legacy_exports_are_not_read(tmp_path):
    (tmp_path / 'articles_old.json').write_text('{"articles":[]}', encoding='utf-8')
    assert not NewsV8Reader(tmp_path).articles()['available']


def test_auth_covers_new_routes(tmp_path):
    app = create_app(service=SensitivityService(), state_db=tmp_path/'state.sqlite', token='secret')
    c = TestClient(app)
    for endpoint in ('/api/v1/risk/presets','/api/v1/market/snapshot','/api/v1/intelligence/events'):
        assert c.get(endpoint).status_code == 401
        assert c.get(endpoint, headers={'Authorization':'Bearer secret'}).status_code == 200
    assert c.get('/healthz').status_code == 200


def test_integrations_and_openapi(client):
    ids = {i['id']: i for i in client.get('/api/v1/integrations').json()['integrations']}
    assert ids['news_engine']['name'] == 'News engine v8'
    assert ids['execution']['status'] == 'none by design'
    assert '/api/v1/risk/run' in client.get('/openapi.json').json()['paths']


def test_unknown_shocks_rejected(client):
    assert client.post('/api/v1/risk/run', json={'scenario': {'unknown': 1}}).status_code == 422


def test_wildcard_origin_refused():
    with pytest.raises(ValueError):
        create_app(allowed_origins=['*'], service=SensitivityService())
