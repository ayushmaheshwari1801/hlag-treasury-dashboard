"""CASH_ENGINE=spec: the gateway on hlag_engine (read-only), checked against the mock RiskEngine's result contract."""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

engine_adapter = pytest.importorskip('treasury_gateway.engine_adapter', reason='hlag_engine is not installed')
from fastapi.testclient import TestClient  # noqa: E402

from treasury_gateway.app import create_app  # noqa: E402
from treasury_gateway.assistant import Assistant  # noqa: E402
from treasury_gateway.engine_adapter import (  # noqa: E402
    EngineAdapter, default_store_path, map_missing, to_dashboard_scenario, to_engine_adjustments, to_engine_params)
from treasury_gateway.news_v8 import NewsV8Reader  # noqa: E402
from treasury_gateway.risk_engine import RiskEngine, scenario_params  # noqa: E402
from treasury_sensitivity.audit import AuditStore  # noqa: E402
from treasury_sensitivity.service import SensitivityService  # noqa: E402

STORE = default_store_path()
AS_OF = date(2026, 11, 3)


@pytest.mark.parametrize('value', [0.0, 1.0, 24.0])
def test_whole_payment_shift_override_is_normalized_for_calendar(value):
    params = to_engine_params({'start_date': AS_OF.isoformat(), 'overrides': {'fx.obligation_shift_months': value}}, AS_OF)
    assert type(params.overrides['fx.obligation_shift_months']) is int
    assert params.overrides['fx.obligation_shift_months'] == value


def test_fractional_payment_shift_override_is_rejected():
    with pytest.raises(ValueError, match='whole number'):
        to_engine_params({'start_date': AS_OF.isoformat(), 'overrides': {'fx.obligation_shift_months': 0.5}}, AS_OF)

@pytest.mark.parametrize('key', ['rates.floating_tranches_per_currency', 'rates.deposit_cohorts_per_currency'])
def test_whole_rate_counts_are_normalized_and_fractions_rejected(key):
    params = to_engine_params({'start_date': AS_OF.isoformat(), 'overrides': {key: 3.0}}, AS_OF)
    assert type(params.overrides[key]) is int
    with pytest.raises(ValueError, match='whole number'):
        to_engine_params({'start_date': AS_OF.isoformat(), 'overrides': {key: 3.5}}, AS_OF)

MOCK_MARKET = Path(__file__).resolve().parents[3] / 'market_data.sqlite'
pytestmark = [pytest.mark.spec_engine,
              pytest.mark.skipif(not STORE.is_file(), reason=f'hlag_engine market store not found at {STORE}')]


def _store_state():
    with sqlite3.connect(STORE.resolve().as_uri() + '?mode=ro', uri=True) as db:
        runs = db.execute('SELECT COUNT(*) FROM run_records').fetchone()[0]
        lines = db.execute('SELECT COUNT(*) FROM cash_lines').fetchone()[0]
    return runs, lines, STORE.stat().st_mtime_ns


@pytest.fixture(scope='module')
def store_before():
    return _store_state()


@pytest.fixture
def client(tmp_path, store_before):
    source = Path(__file__).resolve().parents[3] / 'news-engine-v8/examples/event-result.json'
    NewsV8Reader(tmp_path / 'news').import_result(json.loads(source.read_text(encoding='utf-8')))
    svc = SensitivityService(audit=AuditStore(':memory:'),
                             clock=lambda: datetime(2026, 11, 3, 9, tzinfo=ZoneInfo('Europe/Berlin')))
    app = create_app(service=svc, news_dir=tmp_path / 'news', state_db=tmp_path / 'state.sqlite', assistant=Assistant(None))
    assert app.state.cash_engine == 'spec'
    return TestClient(app)


@pytest.fixture(scope='module')
def adapter():
    return EngineAdapter()


@pytest.fixture(scope='module')
def spec_zero(adapter):
    return adapter.run(AS_OF, {})


@pytest.fixture(scope='module')
def mock_zero():
    if not MOCK_MARKET.is_file():
        pytest.skip('mock market database not available')
    return RiskEngine(MOCK_MARKET).run(AS_OF, {})


def _same_types(spec, mock, where):
    for key, value in mock.items():
        assert key in spec, f'{where}.{key} missing'
        if value is None or spec[key] is None:
            continue
        number = (int, float)
        if isinstance(value, bool) or isinstance(spec[key], bool):
            assert isinstance(spec[key], bool) == isinstance(value, bool), f'{where}.{key}'
        elif isinstance(value, number):
            assert isinstance(spec[key], number), f'{where}.{key}: {type(spec[key])}'
        else:
            assert type(spec[key]) is type(value), f'{where}.{key}: {type(spec[key])} vs {type(value)}'


# ---------------------------------------------------------------- endpoints


def test_dashboard_and_risk_run_return_200(client):
    get = client.get('/api/v1/dashboard')
    assert get.status_code == 200, get.text
    fin = get.json()['finance']
    assert fin['calculation']['profile_version'] == EngineAdapter().engine_profile.profile_version
    assert fin['calculation']['run_id'].startswith('spec_')
    post = client.post('/api/v1/dashboard', json={'horizon_months': 6, 'shocks': {'fx_eur': 5}})
    assert post.status_code == 200, post.text
    run = client.post('/api/v1/risk/run', json={'as_of': AS_OF.isoformat(), 'scenario': {'fx': {'move_pct': {'EUR': .1}}}})
    assert run.status_code == 200, run.text
    body = run.json()
    assert body['engine_params']['fx']['move_pct'] == {'EUR': 10.0}
    assert client.get('/api/v1/risk/runs/' + body['run_id']).json()['run_id'] == body['run_id']


def test_demo_request_is_ignored(client):
    r = client.post('/api/v1/risk/run', json={'as_of': AS_OF.isoformat(), 'scenario': {}, 'demo': True})
    assert r.status_code == 200
    assert r.json()['flags']['demo'] is False and r.json()['demo'] is False


def test_engine_endpoints(client):
    market = client.get('/api/v1/engine/market', params={'as_of': AS_OF.isoformat()}).json()
    assert market['total'] == 20 and market['available'] == 20
    eur = next(s for s in market['series'] if s['series_id'] == 'FX_USD_PER_EUR')
    for key in ('value', 'date', 'weekly_change', 'volatility_regime', 'stale', 'provenance', 'licence'):
        assert key in eur
    assert eur['licence'] == 'public reference data, cited.'
    run = client.post('/api/v1/risk/run', json={'as_of': AS_OF.isoformat(), 'scenario': {'bunker': {'vlsfo_shock_pct': .2}}}).json()
    flows = client.get(f"/api/v1/engine/runs/{run['run_id']}/cashflows").json()
    assert flows['baseline'] and flows['scenario_flows'] and flows['deltas'] and flows['path_items']
    assert abs(flows['totals']['delta'] - run['attribution']['total']) < 1e-3
    assumptions = client.get('/api/v1/engine/assumptions').json()
    assert assumptions['count'] == len(assumptions['parameters']) > 40
    gamma = next(p for p in assumptions['parameters'] if p['key'] == 'freight.gamma')
    assert gamma['adjustable'] and gamma['allowed_range'] and gamma['rationale']


def test_overrides_accepted_and_range_checked(client):
    ok = client.post('/api/v1/risk/run', json={'as_of': AS_OF.isoformat(), 'scenario': {},
                                                'overrides': {'bunker.mfr_share': 0.5}})
    assert ok.status_code == 200, ok.text
    assert ok.json()['engine_params']['overrides'] == {'bunker.mfr_share': 0.5}
    bad = client.post('/api/v1/risk/run', json={'as_of': AS_OF.isoformat(), 'scenario': {},
                                                 'overrides': {'bunker.mfr_share': 1.5}})
    assert bad.status_code == 422
    dash = client.post('/api/v1/dashboard', json={'overrides': {'bunker.mfr_share': 0.5}})
    assert dash.status_code == 200


def test_profile_and_market_writes_are_refused(client):
    profile = client.get('/api/v1/risk/profile').json()
    assert profile['values']['liquidity.cash_usd_m']['value'] == 3150
    assert client.put('/api/v1/risk/profile', json={'actor': 'a', 'values': {'x': {'value': 1}}}).status_code == 409
    assert client.post('/api/v1/market/import-csv', json={'csv': 'series_id,date,value\n'}).status_code == 409


def test_presets_and_pair(client):
    presets = client.get('/api/v1/risk/presets').json()['presets']
    ids = [p['id'] for p in presets]
    assert ids[0] == 'baseline' and 'red_sea_reescalation' in ids and 'eur_rally' in ids
    red_sea = next(p for p in presets if p['id'] == 'red_sea_reescalation')
    run = client.post('/api/v1/risk/run', json={'as_of': AS_OF.isoformat(), 'scenario': red_sea['scenario']})
    assert run.status_code == 200, run.text
    assert run.json()['engine_params']['freight']['rate_shock_pct']['ASIA_EUROPE'] == 40.0
    # what the What-if dropdown sends: the preset's scenario on POST /api/v1/dashboard, no shocks, no forecast_id
    page = client.post('/api/v1/dashboard', json={'horizon_months': 6, 'shocks': {}, 'news_limit': 50,
                                                   'scenario': red_sea['scenario']})
    assert page.status_code == 200, page.text
    fin = page.json()['finance']
    assert fin['scenario']['custom'] and fin['scenario']['full_scenario']['preset_id'] == 'red_sea_reescalation'
    assert next(r for r in fin['cash_impact']['rows'] if r['id'] == 'freight')['impact_eur_m'] != 0
    pair = client.post('/api/v1/risk/pair', json={'as_of': AS_OF.isoformat(), 'scenario': {},
                                                   'state_change': {'rates': {'zim_tranche_on': True}}})
    assert pair.status_code == 200, pair.text
    assert pair.json()['difference']['min_headroom_usd_m'] < 0


def test_presets_are_grouped_joint_module_sensitivity(adapter):
    presets = adapter.presets(AS_OF)
    groups = [p['group'] for p in presets]
    assert groups[0] == 'baseline'
    order = [g for i, g in enumerate(groups[1:]) if i == 0 or g != groups[i]]
    assert order == ['joint', 'module', 'sensitivity']  # each group is one contiguous block, in this order
    by_id = {p['id']: p['group'] for p in presets}
    assert by_id['red_sea_reescalation'] == 'joint' and by_id['credit_squeeze'] == 'module'
    assert by_id['rates_up_100bp'] == 'sensitivity' and by_id['fx_eur_up_10'] == 'sensitivity'
    assert all(p['name'] and not p['name'].startswith(('fx_', 'rates_')) for p in presets)


def test_engine_store_is_not_written(client, store_before):
    client.post('/api/v1/risk/run', json={'as_of': AS_OF.isoformat(), 'scenario': {}})
    assert _store_state() == store_before


# ---------------------------------------------------------------- result contract


def test_result_has_mock_keys_and_types(spec_zero, mock_zero):
    json.dumps(spec_zero, allow_nan=False)
    _same_types(spec_zero, mock_zero, 'result')
    for name in ('headroom', 'summary', 'attribution', 'flags', 'margin_test', 'memo_positions', 'anchor_adjustment'):
        _same_types(spec_zero[name], mock_zero[name], name)
    _same_types(spec_zero['lines'][0], mock_zero['lines'][0], 'lines[0]')
    _same_types(spec_zero['baseline_path'][0], mock_zero['baseline_path'][0], 'baseline_path[0]')
    _same_types(spec_zero['scenario_path'][0], mock_zero['scenario_path'][0], 'scenario_path[0]')
    sid = next(iter(mock_zero['market_snapshot']))
    _same_types(spec_zero['market_snapshot'][sid], mock_zero['market_snapshot'][sid], 'market_snapshot')
    for key in ('liquidity.cash_usd_m', 'liquidity.credit_lines_usd_m', 'liquidity.investments_usd_m',
                'liquidity.reserve_usd_m', 'liquidity.count_liquid_investments', 'liquidity.lease_monthly_usd_m',
                'rates.financial_debt_usd_m'):
        assert 'value' in spec_zero['profile']['values'][key]
    assert set(spec_zero['profile']['trades']) == set(mock_zero['profile']['trades'])


def test_zero_shock_gives_zero_deltas(spec_zero):
    assert all(line['delta_usd_m'] == 0 for line in spec_zero['lines'])
    assert all(v == 0 for v in spec_zero['attribution'].values())
    assert spec_zero['baseline_path'] == spec_zero['scenario_path']
    assert spec_zero['headroom']['baseline_min_usd_m'] == spec_zero['headroom']['scenario_min_usd_m']


def test_hedge_currency_suffix_matches_dashboard_split(spec_zero):
    hedges = [l for l in spec_zero['lines'] if l['line_code'] == 'HEDGE_SETTLEMENT']
    suffixes = {l['sub_id'].rsplit(':', 1)[-1] for l in hedges}
    assert hedges and suffixes <= {'EUR', 'CAD', 'GBP', 'CNY', 'INR', 'BRL'} and {'CAD', 'INR', 'BRL'} <= suffixes
    keys = {(l['owner'], l['line_code'], l['date'], l['currency'], l['sub_id']) for l in spec_zero['lines']}
    assert len(keys) == len(spec_zero['lines'])  # the suffix keeps every pairing key unique


def test_fx_cards_get_their_own_hedge_settlements(adapter):
    from treasury_gateway.dashboard_adapter import compose_cash_dashboard
    run = adapter.run(AS_OF, {'fx': {'move_pct': {'CAD': .1}}})
    fin = compose_cash_dashboard({}, run, {'series': [], 'history': {}}, 12, {}, AS_OF)
    rows = {r['id']: r['impact_usd_m'] for r in fin['cash_impact']['rows']}
    # CAD is 70% hedged: the CAD card nets the hedge settlement; nothing CAD-related lands on the EUR card.
    assert rows['fx_cad'] is not None and abs(rows['fx_eur']) < 1e-6


# ---------------------------------------------------------------- conversions


def test_fraction_to_percent_and_t0():
    dash = scenario_params({'freight': {'rate_shock_pct': {'ASIA_EUROPE': .15}, 'volume_shock_pct': {'AFRICA_INTRA': -.05}},
                            'bunker': {'vlsfo_shock_pct': .2, 'consumption_uplift_pct': .3},
                            'rates': {'rate_path': {'USD': [{'date': 'T0', 'step_bp': 25}]}, 'credit_spread_change_bp': 50},
                            'fx': {'move_pct': {'EUR': .1, 'CNY': -.05}, 'shape': 'ramp'},
                            'hedge_tests': [{'module': 'bunker', 'instrument': 'swap', 'target': 'fuel_all',
                                             'coverage_pct': .75, 'tenor_months': 6}]}, AS_OF)
    p = to_engine_params(dash, AS_OF)
    assert p.freight.rate_shock_pct['ASIA_EUROPE'] == 15.0 and p.freight.volume_shock_pct['AFRICA_INTRA'] == -5.0
    assert p.bunker.vlsfo_shock_pct == 20.0 and p.bunker.consumption_uplift_pct == 30.0
    assert p.bunker.uplift_affected_share == 1.0  # dashboard default when omitted
    assert p.rates.rate_path['USD'][0].date == AS_OF and p.rates.credit_spread_change_bp == 50
    assert p.fx.move_pct == {'EUR': 10.0, 'CNY': -5.0} and set(p.fx.shape.values()) == {'ramp'}
    assert p.hedge_tests[0].coverage_pct == 75.0 and p.hedge_tests[0].start_date == AS_OF


def test_forecast_scenario_and_presets_round_trip(adapter):
    forecast = {'name': 'News-inferred forecast', 'source': 'custom', 'event_ids': ['e1'], 'start_date': AS_OF.isoformat(),
                'duration_days': 90, 'default_shape': 'step', 'freight': {'rate_shock_pct': {'ASIA_EUROPE': .15}},
                'bunker': {'consumption_uplift_pct': .105, 'uplift_affected_share': 1.0}, 'rates': {}, 'fx': {}}
    p = to_engine_params(scenario_params(forecast, AS_OF), AS_OF)
    assert p.duration_days == 90 and p.bunker.consumption_uplift_pct == 10.5 and p.event_ids == ['e1']
    assert to_engine_params(scenario_params({'preset_id': 'baseline', 'source': 'preset'}, AS_OF), AS_OF).preset_id is None
    for preset in adapter.presets(AS_OF)[1:]:
        params = to_engine_params(scenario_params(preset['scenario'], AS_OF), AS_OF)
        assert to_dashboard_scenario(params) == preset['scenario'], preset['id']


def test_missing_input_prefixes():
    assert map_missing('freight.collection_lag_days', 'to_enter', '') == 'freight.collection_lag_days'
    assert map_missing('debt.current_lease_liabilities_usd', 'to_enter', '') == 'profile:rates:debt.current_lease_liabilities_usd'
    assert map_missing('liquidity.x', 'to_enter', '') == 'profile:liquidity.x'
    assert map_missing('bunker', 'module_error', 'bunker (base pass): boom') == 'bunker.module_error:bunker'


def test_gateway_adjustments_become_engine_adjustments(adapter):
    gateway = [{'id': 'adj_1', 'param_key': 'bunker.mfr_share', 'old_value': 0.7, 'new_value': 0.5, 'step': 'medium',
                'effective_date': '2026-11-01', 'review_date': '2027-01-31', 'event_ids': ['e1'], 'justification': 'j',
                'proposed_by': 'agent', 'status': 'approved', 'approver': 'treasurer',
                'decided_at': '2026-11-02T10:00:00+00:00'},
               {'id': 'adj_2', 'param_key': 'bunker.mfr_share', 'old_value': 0.7, 'new_value': 0.6, 'status': 'proposed',
                'effective_date': '2026-11-01', 'review_date': '2027-01-31', 'proposed_by': 'agent'}]
    converted = to_engine_adjustments(gateway, AS_OF)
    assert [a.adjustment_id for a in converted] == ['adj_1'] and converted[0].new_value == 0.5
    shocked = adapter.run(AS_OF, {'bunker': {'vlsfo_shock_pct': .2}})
    adjusted = adapter.run(AS_OF, {'bunker': {'vlsfo_shock_pct': .2}}, adjustments=gateway)
    assert adjusted['run_id'] != shocked['run_id']
    assert adjusted['attribution']['bunker'] != shocked['attribution']['bunker']  # lower MFR share recovers less


def test_assistant_uses_the_same_assumption_overrides_as_dashboard(client, monkeypatch):
    monkeypatch.setattr(Assistant, 'answer', lambda self, question, context, **kw: context)
    body = {'as_of': '2026-11-03', 'horizon_months': 6,
            'shocks': {'fx_eur': 10}, 'overrides': {'fx.hedge_ratio.eur_operating_costs': 1.0}}
    page = client.post('/api/v1/dashboard', json=body)
    answer = client.post('/api/v1/assistant/query', json={**body, 'question': 'Current figures?'})
    assert page.status_code == answer.status_code == 200
    f, c = page.json()['finance'], answer.json()
    assert c['liquidity_eur_m']['scenario_headroom'] == f['liquidity']['scenario_headroom_eur_m']
    assert c['cash_impact_eur_m']['net'] == f['cash_impact']['net_eur_m']
    assert c['monitoring']['minimum_date'] == f['liquidity']['scenario_min_date']
    assert c['monitoring']['headroom_used_vs_baseline'] == f"{f['calculation']['consumed_pct']:.1f}%"
    assert 'run_id' not in c['calculation']


def test_market_history_and_review_use_current_saved_data(client):
    history = client.get('/api/v1/market/history', params={'series_id': 'FX_USD_PER_EUR', 'as_of': '2026-11-03', 'range': '1M'})
    assert history.status_code == 200
    assert history.json()['points']
    assert all('2026-10-04' <= p['date'] <= '2026-11-03' for p in history.json()['points'])
    assert client.get('/api/v1/market/history', params={'series_id':'FX_USD_PER_EUR','as_of':'2026-11-03','range':'invalid'}).status_code == 422
    run = client.get('/api/v1/dashboard').json()['finance']['calculation']['run_id']
    url = f'/api/v1/risk/runs/{run}/reviews'
    assert client.post(url, json={'reviewer':' '}).status_code == 422
    saved = client.post(url, json={'reviewer':'QA', 'comment':'Saved run review test'})
    assert saved.status_code == 200 and saved.json()['run_id'] == run
    assert client.get(url).json()['reviews'][0]['comment'] == 'Saved run review test'
