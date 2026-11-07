"""Endpoint regression for v8 evidence -> forecast -> immutable deterministic cash run."""
import copy
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from treasury_gateway.assistant import Assistant
from treasury_gateway.news_v8 import NewsV8Reader
from treasury_gateway.risk_api import create_router as cash_router
from treasury_gateway.signals import IntelligenceStore, create_router as intelligence_router


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Fail loudly if an endpoint accidentally invokes a paid model in this offline test.
    monkeypatch.setattr(Assistant,'_chat',lambda *a,**k: (_ for _ in ()).throw(AssertionError('Unexpected paid call')))
    store=IntelligenceStore(tmp_path/'state.sqlite')
    reader=NewsV8Reader(tmp_path/'exports')
    app=FastAPI()
    app.include_router(intelligence_router(store,reader))
    app.include_router(cash_router(tmp_path/'market.sqlite',tmp_path/'state.sqlite',store,reader,assistant=Assistant(None)))
    with TestClient(app) as cli:
        yield cli


def import_red_sea_and_forecast(client):
    source=Path(__file__).resolve().parents[3]/'news-engine-v8/examples/event-result.json'
    payload=json.loads(source.read_text(encoding='utf-8'))
    payload['events'][0]['title']='Red Sea attacks resume; vessels reroute'
    payload['events'][0]['summary']='New attacks disrupt Red Sea shipping. Vessels reroute around the Cape of Good Hope.'
    original=payload['sources'][0]['text']
    response=client.post('/api/v1/intelligence/import-result',json=payload)
    assert response.status_code==200,response.text
    response=client.post('/api/v1/intelligence/forecast',json={'mode':'rule_based','as_of':'2026-11-02','horizon_days':90})
    assert response.status_code==200,response.text
    forecast=response.json()
    assert forecast['mode']=='rule_based' and forecast['model'] is None
    assert forecast['baseline_changed'] is False and forecast['label']=='ASSUMPTION'
    assert all(p['event_ids']==[payload['events'][0]['event_id']] for p in forecast['predictions'])
    assert original==payload['sources'][0]['text']
    return forecast


def test_v8_forecast_runs_cash_without_mutating_profile_or_baseline(client):
    profile_before=client.get('/api/v1/risk/profile').json()
    forecast=import_red_sea_and_forecast(client)
    assert forecast['scenario']['freight']['rate_shock_pct']['ASIA_EUROPE']==.15
    assert forecast['scenario']['freight']['transit_delay_days']['ASIA_EUROPE']==12
    assert forecast['scenario']['bunker']['consumption_uplift_pct']==.105
    response=client.post(f"/api/v1/risk/from-forecast/{forecast['forecast_id']}?demo=true")
    assert response.status_code==200,response.text
    run=response.json()
    assert run['flags']['demo'] and run['forecast_id']==forecast['forecast_id']
    assert run['forecast']==forecast
    assert run['scenario']['freight']['rate_shock_pct']==forecast['scenario']['freight']['rate_shock_pct']
    assert run['scenario']['bunker']['consumption_uplift_pct']==.105
    assert run['scenario_path']!=run['baseline_path']
    assert run['summary']['net_delta_usd_m']!=0
    assert client.get('/api/v1/risk/profile').json()==profile_before
    assert client.get('/api/v1/intelligence/adjustments').json()['adjustments']==[]
    saved=client.get('/api/v1/risk/runs/'+run['run_id']).json()
    assert saved==run
    replay=client.post('/api/v1/risk/runs/'+run['run_id']+'/replay').json()
    assert replay['result']==run and replay['replay_mode']=='immutable_saved_result'


def test_forecast_edits_are_explicit_and_date_cannot_drift(client):
    forecast=import_red_sea_and_forecast(client)
    altered=copy.deepcopy(forecast['scenario'])
    altered['freight']['rate_shock_pct']['ASIA_EUROPE']=-.5
    response=client.post('/api/v1/risk/run',json={'as_of':'2026-11-02','demo':True,
                        'forecast_id':forecast['forecast_id'],'scenario':altered})
    assert response.status_code==200,response.text[:500]
    run=response.json()
    assert run['forecast_modified'] is True
    assert run['forecast_overrides']['freight.rate_shock_pct.ASIA_EUROPE']=={'forecast':.15,'run':-.5}
    assert run['forecast']['scenario']['freight']['rate_shock_pct']['ASIA_EUROPE']==.15
    assert run['scenario']['freight']['rate_shock_pct']['ASIA_EUROPE']==-.5
    wrong_day=client.post('/api/v1/risk/run',json={'as_of':'2026-11-03','demo':True,
                         'forecast_id':forecast['forecast_id'],'scenario':forecast['scenario']})
    assert wrong_day.status_code==422,wrong_day.text[:500]


def test_pair_state_change_contract_and_no_scenario_delta(client):
    response=client.post('/api/v1/risk/pair',json={'as_of':'2026-11-02','demo':True,'scenario':{},
                        'state_change':{'overrides':{'bunker.scrubber_share':.3}}})
    assert response.status_code==200,response.text
    pair=response.json()
    assert pair['label']=='state comparison'
    for run in (pair['run_a'],pair['run_b']):
        assert run['baseline_path']==run['scenario_path'] and run['attribution']['total']==0
        assert client.get('/api/v1/risk/runs/'+run['run_id']).json()==run
    assert pair['run_b']['scenario']['overrides']['bunker.scrubber_share']==.3
    invalid=client.post('/api/v1/risk/pair',json={'as_of':'2026-11-02','demo':True,
                       'state_change':{'fx':{'move_pct':{'EUR':.1}}}})
    assert invalid.status_code==422


def test_empty_forecast_override_is_explicit_baseline_and_pair_rejects_reference(client):
    forecast=import_red_sea_and_forecast(client)
    response=client.post('/api/v1/risk/run',json={'as_of':'2026-11-02','demo':True,
                        'forecast_id':forecast['forecast_id'],'scenario':{}})
    assert response.status_code==200,response.text
    run=response.json()
    assert run['baseline_path']==run['scenario_path']
    assert run['forecast_modified'] and run['forecast_overrides']
    assert client.post('/api/v1/risk/pair',json={'forecast_id':forecast['forecast_id']}).status_code==422
