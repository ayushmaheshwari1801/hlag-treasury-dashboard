"""Original webpage contract keeps full news forecasts and explicit slider overrides."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

import market_feed
from treasury_gateway.app import create_app
from treasury_gateway.assistant import Assistant
from treasury_sensitivity.service import SensitivityService


def test_original_cards_use_forecast_and_csv_inputs(tmp_path):
    db_path = tmp_path / 'market.sqlite'
    db = market_feed.connect(db_path)
    with db:
        for sid, (unit, _, _) in market_feed.SERIES.items():
            value = 1.15 if unit == 'USD/unit' else 3 if unit == '%' else 2500 if unit == 'USD/FEU' else 600 if unit == 'USD/t' else 75
            for day in ('2025-12-31', '2026-01-02', '2026-06-30', '2026-10-23', '2026-10-30'):
                market_feed.save(db, sid, day, value, unit, 'Test CSV', 'test.csv', True, 'ASSUMPTION')
    db.close()
    app = create_app(service=SensitivityService(), market_db=db_path, state_db=tmp_path/'state.sqlite',
                     news_dir=tmp_path/'news', assistant=Assistant(None))
    with TestClient(app) as client:
        raw = json.loads((Path(__file__).resolve().parents[3] / 'news-engine-v8/examples/event-result.json').read_text(encoding='utf-8'))
        raw['events'][0]['title'] = 'Red Sea attacks and shipping rerouting'
        assert client.post('/api/v1/intelligence/import-result', json=raw).status_code == 200
        prediction = client.post('/api/v1/intelligence/forecast', json={'as_of':'2026-11-02','mode':'rule_based'}).json()
        response = client.post('/api/v1/dashboard', json={'as_of':'2026-11-02','horizon_months':3,
            'forecast_id':prediction['forecast_id'],'scenario':prediction['scenario'],'shocks':{'bunker':100}})
        assert response.status_code == 200, response.text
        body = response.json(); finance = body['finance']
        assert body['news']['total'] == 1 and len(body['news']['items'][0]['article_ids']) == 2
        assert finance['forecast']['forecast_id'] == prediction['forecast_id']
        assert finance['calculation']['horizon_end'] == '2027-02-28'
        saved = client.get('/api/v1/risk/runs/' + finance['calculation']['run_id']).json()
        assert saved['scenario']['freight']['rate_shock_pct']['ASIA_EUROPE'] == .15
        assert saved['scenario']['freight']['transit_delay_days']['ASIA_EUROPE'] == 12
        assert saved['scenario']['bunker']['consumption_uplift_pct'] == .105
        assert abs(saved['scenario']['bunker']['vlsfo_shock_pct'] - 100/600) < 1e-10
        assert saved['forecast_modified'] and saved['forecast_overrides']
        assert finance['scenario']['shocks']['bunker'] == 100
        assert sum(r['impact_eur_m'] for r in finance['cash_impact']['rows']) == finance['cash_impact']['net_eur_m']
        assert not finance['debt_maturity']['source'].startswith('Layout supplied')
