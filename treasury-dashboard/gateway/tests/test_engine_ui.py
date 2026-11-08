"""Dashboard additions on hlag_engine: market data refresh, liquidity paths, T0 cash, run status passthrough."""
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip('treasury_gateway.engine_adapter', reason='hlag_engine is not installed')
from fastapi.testclient import TestClient  # noqa: E402
from hlag_engine.data.connectors.base import FetchResult, Observation  # noqa: E402
from hlag_engine.data.series import SERIES  # noqa: E402
from hlag_engine.data.store import Store  # noqa: E402

from treasury_gateway.app import create_app  # noqa: E402
from treasury_gateway.assistant import Assistant  # noqa: E402
from treasury_gateway.engine_adapter import default_store_path  # noqa: E402
from treasury_gateway.news_v8 import NewsV8Reader  # noqa: E402
from treasury_sensitivity.audit import AuditStore  # noqa: E402
from treasury_sensitivity.service import SensitivityService  # noqa: E402

STORE = default_store_path()
AS_OF = date(2026, 11, 3)
NEWS = Path(__file__).resolve().parents[3] / 'news-engine-v8/examples/event-result.json'
pytestmark = pytest.mark.spec_engine


def _service():
    return SensitivityService(audit=AuditStore(':memory:'),
                              clock=lambda: datetime(2026, 11, 3, 9, tzinfo=ZoneInfo('Europe/Berlin')))


def _app(tmp_path, **kw):
    NewsV8Reader(tmp_path / 'news').import_result(json.loads(NEWS.read_text(encoding='utf-8')))
    return create_app(service=_service(), news_dir=tmp_path / 'news', state_db=tmp_path / 'state.sqlite',
                      assistant=Assistant(None), **kw)


class FakeECB:
    """Supplies EUR and ESTR only; one EUR value fails the positive-only check; ESTR stops early (stale)."""
    name = 'Fake ECB'
    series_ids = ('FX_USD_PER_EUR', 'ESTR')

    def fetch(self, series_ids, start, end):
        obs = []
        for i in range(10):
            day = AS_OF - timedelta(days=9 - i)
            if 'FX_USD_PER_EUR' in series_ids:
                value = -1.0 if i == 4 else 1.10 + i / 1000
                obs.append(Observation('FX_USD_PER_EUR', day, value, SERIES['FX_USD_PER_EUR'].unit, 'fake://ecb/eur'))
            if 'ESTR' in series_ids and i < 2:
                obs.append(Observation('ESTR', day - timedelta(days=20), 1.9, SERIES['ESTR'].unit, 'fake://ecb/estr'))
        return FetchResult(obs, 'Fake ECB', 'fake')


@pytest.fixture
def temp_store(tmp_path):
    path = tmp_path / 'engine_market.sqlite'
    Store(path).close()
    return path


def test_refresh_writes_only_through_engine_load_and_audits_the_person(tmp_path, temp_store):
    client = TestClient(_app(tmp_path, market_db=temp_store, cash_engine='spec', market_sources=lambda: [FakeECB()]))
    assert client.post('/api/v1/engine/market/refresh', json={'actor': '  '}).status_code == 422
    assert client.post('/api/v1/engine/market/refresh', json={}).status_code == 422
    res = client.post('/api/v1/engine/market/refresh', json={'actor': 'Ayush'})
    assert res.status_code == 200, res.text
    report = res.json()
    assert report['as_of'] == AS_OF.isoformat()
    assert report['actor'] == 'Ayush (dashboard: Refresh market data)'
    by_id = {s['series_id']: s for s in report['series']}
    assert by_id['FX_USD_PER_EUR']['prints'] == 9 and by_id['FX_USD_PER_EUR']['stale'] is False
    assert by_id['FX_USD_PER_EUR']['rejected'][0]['value'] == -1.0
    assert report['rejected_values'] == 1
    assert 'ESTR' in report['stale_series'] and 'SOFR' in report['failed_series']
    assert 'WCI_COMPOSITE' in report['not_fetched'] and 'VLSFO_RTM' in report['not_fetched']
    with sqlite3.connect(temp_store) as db:
        audit = db.execute("SELECT entry_id, actor, action FROM audit_log").fetchall()
        tables_written = db.execute("SELECT COUNT(*) FROM market_data").fetchone()[0]
    assert audit == [(report['audit_entry_id'], 'Ayush (dashboard: Refresh market data)', 'market_data_load')]
    assert tables_written > 0
    market = client.get('/api/v1/engine/market', params={'as_of': AS_OF.isoformat()}).json()
    eur = next(s for s in market['series'] if s['series_id'] == 'FX_USD_PER_EUR')
    assert eur['value'] == pytest.approx(1.109)


def test_refresh_refused_with_mock_engine(tmp_path, monkeypatch):
    if not STORE.is_file():
        pytest.skip('engine store not available')
    called = []
    client = TestClient(_app(tmp_path, cash_engine='mock', market_db=Path(__file__).resolve().parents[3] / 'market_data.sqlite',
                             market_sources=lambda: called.append(1) or [FakeECB()]))
    assert client.post('/api/v1/engine/market/refresh', json={'actor': 'Ayush'}).status_code == 409
    assert called == []


@pytest.fixture
def client(tmp_path):
    if not STORE.is_file():
        pytest.skip(f'hlag_engine market store not found at {STORE}')
    return TestClient(_app(tmp_path))


def test_dashboard_carries_engine_status_desk_exceptions_and_t0_cash(client):
    preset = next(p for p in client.get('/api/v1/risk/presets').json()['presets'] if p['id'] == 'red_sea_reescalation')
    fin = client.post('/api/v1/dashboard', json={'scenario': preset['scenario']}).json()['finance']
    calc = fin['calculation']
    assert calc['engine_status'] and isinstance(calc['status_reasons'], list) and isinstance(calc['desk_exceptions'], list)
    t0 = fin['liquidity']['cash_t0']
    assert t0['usd_m'] > 0 and t0['eur_m'] == pytest.approx(t0['usd_m'] / fin['liquidity']['balance_date_eurusd'])
    assert t0['headroom_t0_eur_m'] == pytest.approx(
        t0['eur_m'] + t0['credit_lines_eur_m'] + t0['special_fund_eur_m'] - t0['reserve_eur_m'])
    assert 't1_at_t0' in t0['roll_forward']


def test_cashflows_include_both_liquidity_paths_and_the_run_reserve(client):
    run = client.post('/api/v1/risk/run', json={'as_of': AS_OF.isoformat(), 'scenario': {},
                                                 'overrides': {'liquidity.minimum_operational_reserve_usd': 900_000_000}}).json()
    liq = client.get(f"/api/v1/engine/runs/{run['run_id']}/cashflows").json()['liquidity']
    assert liq['reserve_usd_m'] == pytest.approx(900)
    assert len(liq['baseline_path']) == len(liq['scenario_path']) > 0
    p = liq['baseline_path'][0]
    assert p['liquidity_usd_m'] - liq['reserve_usd_m'] == pytest.approx(p['headroom_usd_m'])


def test_data_connections_relabelled_under_spec(client):
    entries = {e['id']: e for e in client.get('/api/v1/integrations').json()['integrations']}
    assert 'market_feed.py' not in entries['cash_market']['configure']
    assert 'Refresh market data' in entries['cash_market']['configure']
    assert 'PUT' not in entries['company_profile']['configure'] and entries['company_profile']['status'].startswith('read-only')
    assert not any(k.startswith('data_') for k in entries)


def test_memo_only_currency_and_translation_memo(client):
    """GBP has only a translation memo position (D19): no cash lines, so the row is memo-only and its memo is the
    engine's position x move. CNY has cash flows and a memo, so it keeps its cash value and also carries the memo."""
    fin = client.post('/api/v1/dashboard', json={'shocks': {'fx_gbp': 10, 'fx_cny': -10}}).json()['finance']
    rows = {r['id']: r for r in fin['cash_impact']['rows']}
    sliders = {s['id']: s for s in fin['scenario']['sliders']}
    gbp, cny = rows['fx_gbp'], rows['fx_cny']
    assert gbp['memo_only'] is True and sliders['fx_gbp']['memo_only'] is True
    assert gbp['impact_eur_m'] == 0
    assert gbp['memo']['currencies'] == ['GBP'] and gbp['memo']['cash'] is False and gbp['memo']['on_liquidity_path'] is False
    assert gbp['memo']['revaluation_usd_m'] == pytest.approx(-24 * 0.10)          # USD 24m short x +10%
    assert gbp['memo']['revaluation_eur_m'] == pytest.approx(-2.4 / fin['liquidity']['balance_date_eurusd'])
    assert cny['memo_only'] is False and cny['impact_eur_m'] != 0
    assert cny['memo']['revaluation_usd_m'] == pytest.approx(85 * -0.10)           # USD 85m long x -10%
    for rid in ('fx_eur', 'fx_cad', 'interest', 'bunker', 'freight'):
        assert rows[rid]['memo_only'] is False and rows[rid]['memo'] is None
    # the memo never reaches the cash figures
    base = client.post('/api/v1/dashboard', json={'shocks': {'fx_gbp': 0, 'fx_cny': -10}}).json()['finance']
    assert base['cash_impact']['net_eur_m'] == pytest.approx(fin['cash_impact']['net_eur_m'])


# ---------------------------------------------------------------- hypothetical fuel hedge tool

HEDGE = {'target': 'fuel_all', 'instrument': 'swap', 'coverage_pct': 50, 'tenor_months': 12}
FUEL_UP = {'bunker': {'vlsfo_shock_pct': .3, 'hsfo_shock_pct': .3}, 'name': 'Fuel +30%', 'start_date': AS_OF.isoformat()}


def test_hedge_matches_engine_test_hedge(tmp_path):
    """The gateway runs test_hedge's steps without writing; the numbers must equal hlag_engine's own test_hedge,
    which is run here on a throwaway copy of the store (it writes run records and audit entries)."""
    if not STORE.is_file():
        pytest.skip('engine store not available')
    from hlag_engine.contracts.hedge_test import HedgeTest
    from hlag_engine.engine.compare import test_hedge
    from hlag_engine.engine.run import Engine
    from treasury_gateway.engine_adapter import EngineAdapter, to_engine_params
    from treasury_gateway.risk_engine import scenario_params
    copy = tmp_path / 'copy.sqlite'
    with sqlite3.connect(STORE.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(copy) as dst:
        src.backup(dst)
    ours = EngineAdapter().hedge_test(AS_OF, FUEL_UP, HEDGE)
    params = to_engine_params(scenario_params(FUEL_UP, AS_OF), AS_OF)
    store = Store(copy)
    try:
        theirs = test_hedge(Engine(store), HedgeTest(module='bunker', strike_rule='spot_at_T0', start_date=AS_OF, **HEDGE),
                            params, AS_OF).result.hedge_metrics.model_dump(mode='json')
    finally:
        store.close()
    assert ours['metrics'] == theirs
    assert ours['label'] == 'Hypothetical hedge test · nothing is executed'


def test_hedge_endpoint_uses_page_scenario_and_does_not_write(client):
    before = STORE.stat().st_mtime_ns
    opts = client.get('/api/v1/engine/hedge-test/options').json()
    assert {t['id'] for t in opts['targets']} == {'fuel_all', 'vlsfo', 'hsfo'}
    assert opts['coverage_pct_range'] == [0, 100] and opts['tenor_months_range'] == [1, 36]
    res = client.post('/api/v1/engine/hedge-test', json={'shocks': {'bunker': 200}, 'hedge': HEDGE})
    assert res.status_code == 200, res.text
    r = res.json()
    assert r['metrics']['saving_adverse_usd_m'] > 0 and r['settlement_by_month'] and r['margin_test']['applicable']
    assert r['min_headroom_usd_m']['change'] == pytest.approx(r['min_headroom_usd_m']['with'] - r['min_headroom_usd_m']['without'], abs=1e-3)
    fixed = client.post('/api/v1/engine/hedge-test', json={'shocks': {'bunker': 200},
                                                           'hedge': {**HEDGE, 'instrument': 'fixed_price_supply'}}).json()
    assert fixed['margin_by_month'] == [] and fixed['margin_test']['applicable'] is False
    assert client.post('/api/v1/engine/hedge-test', json={'hedge': {**HEDGE, 'tenor_months': 48}}).status_code == 422
    assert client.post('/api/v1/engine/hedge-test', json={'hedge': {**HEDGE, 'target': 'freight'}}).status_code == 422
    assert STORE.stat().st_mtime_ns == before


def test_hedge_tool_refused_with_mock_engine(tmp_path):
    client = TestClient(_app(tmp_path, cash_engine='mock', market_db=Path(__file__).resolve().parents[3] / 'market_data.sqlite'))
    assert client.post('/api/v1/engine/hedge-test', json={'hedge': HEDGE}).status_code == 409


def test_hedge_saving_and_cost_follow_fuel_direction_and_flag_t0_margin(client):
    """Fuel up: saving = selected scenario, cost = mirror. Fuel down: the roles swap. A step fall at T0 posts swap
    margin on T0, which is off the liquidity path; the response says so."""
    up = client.post('/api/v1/engine/hedge-test', json={'shocks': {'bunker': 200}, 'hedge': HEDGE}).json()
    assert up['fuel_move'] == 'up' and up['adverse_is'] == 'scenario'
    assert up['saving_adverse_usd_m'] == pytest.approx(up['metrics']['saving_adverse_usd_m'], abs=1e-3)
    assert up['cost_favourable_usd_m'] == pytest.approx(up['metrics']['cost_favourable_usd_m'], abs=1e-3)
    down = client.post('/api/v1/engine/hedge-test', json={'shocks': {'bunker': -200}, 'hedge': {**HEDGE, 'tenor_months': 24}}).json()
    assert down['fuel_move'] == 'down' and down['adverse_is'] == 'mirror'
    assert down['saving_adverse_usd_m'] == pytest.approx(down['change_in_min_headroom_usd_m']['mirror'], abs=1e-3)
    assert down['cost_favourable_usd_m'] == pytest.approx(-down['change_in_min_headroom_usd_m']['scenario'], abs=1e-3)
    assert down['margin_on_t0_usd_m'] < 0 and down['margin_on_t0_note']
    assert up['margin_on_t0_usd_m'] == 0 and up['margin_on_t0_note'] is None


def test_demand_slump_with_large_swap_raises_margin_desk_exception(client):
    preset = next(p for p in client.get('/api/v1/risk/presets').json()['presets'] if p['id'] == 'demand_slump')
    r = client.post('/api/v1/engine/hedge-test', json={'scenario': preset['scenario'],
                                                        'hedge': {**HEDGE, 'coverage_pct': 100, 'tenor_months': 36}}).json()
    assert r['margin_test']['passed'] is False and r['margin_test']['shortfall_date']
    assert [e['kind'] for e in r['desk_exceptions_raised_by_hedge']] == ['margin']
def test_desk_exceptions_come_with_the_engine_horizon_end(client):
    preset = next(p for p in client.get('/api/v1/risk/presets').json()['presets'] if p['id'] == 'demand_slump')
    calc = client.post('/api/v1/dashboard', json={'scenario': preset['scenario']}).json()['finance']['calculation']
    assert [e['kind'] for e in calc['desk_exceptions']] == ['driver']
    assert calc['engine_horizon_end'] > calc['horizon_end']          # full engine horizon vs the selected 6 months
    assert calc['desk_exceptions'][0]['date'] <= calc['engine_horizon_end']


def test_hedge_result_carries_the_engine_horizon_end(client):
    preset = next(p for p in client.get('/api/v1/risk/presets').json()['presets'] if p['id'] == 'demand_slump')
    r = client.post('/api/v1/engine/hedge-test', json={'scenario': preset['scenario'],
                                                        'hedge': {**HEDGE, 'coverage_pct': 100, 'tenor_months': 36}}).json()
    margin = r['desk_exceptions_raised_by_hedge'][0]
    assert margin['date'] == r['margin_test']['shortfall_date'] <= r['engine_horizon_end']
    assert margin['gross_adverse_usd_m'] == pytest.approx(-r['margin_test']['lowest_margin_eligible_balance_usd_m'], abs=1e-3)


def test_collection_delay_slider_moves_cash_to_later_periods(client):
    """Roadmap phase 3: collections N days slower is the spec's transit-delay scenario shock on every trade, so it is
    a freight delta that lowers cash in the first collection month and is rejected outside the 0..60 day range."""
    fin = client.post('/api/v1/dashboard', json={'horizon_months': 12, 'shocks': {'collection_delay': 13}}).json()['finance']
    assert fin['calculation']['attribution_usd_m']['freight'] < 0
    run = client.get(f"/api/v1/risk/runs/{fin['calculation']['run_id']}").json()
    assert set(run['engine_params']['freight']['transit_delay_days'].values()) == {13}
    rows = client.get(f"/api/v1/engine/runs/{fin['calculation']['run_id']}/cashflows").json()['by_driver_month']
    assert any(r['driver'] == 'freight' and r['delta_usd_m'] < -1 for r in rows)
    assert client.post('/api/v1/dashboard', json={'shocks': {'collection_delay': 61}}).status_code in (400, 422)
    # daily rows (for daily/weekly/monthly columns) add up exactly to the monthly rows
    flows = client.get(f"/api/v1/engine/runs/{fin['calculation']['run_id']}/cashflows").json()
    by_month = {}
    for d in flows['by_driver_day']:
        key = (d['driver'], d['date'][:7])
        by_month[key] = by_month.get(key, 0.) + d['delta_usd_m']
    assert by_month and all(abs(by_month[(r['driver'], r['month'])] - r['delta_usd_m']) < 1e-6 for r in rows)


def test_direct_cash_flow_reaches_path_totals_and_its_own_bar(client):
    """A scenario's direct cash flow (hlag_engine owner direct) lowers the scenario low point by its full amount,
    is its own attribution bar and cash_impact.direct entry, and is counted in the totals, not in a market row."""
    flow = {'effect_id': 'fine-eu', 'overlap_key': 'eu-fine', 'date': '2026-12-15', 'amount_usd_m': -150,
            'label': 'EU fine', 'rationale': 'Stated fine', 'event_ids': ['E1']}
    plain = client.post('/api/v1/dashboard', json={'horizon_months': 6, 'scenario': {'name': 'Plain'}}).json()['finance']
    fin = client.post('/api/v1/dashboard', json={'horizon_months': 6, 'scenario': {
        'name': 'Fine', 'direct': {'cash_flows': [flow]}}}).json()['finance']
    rate = fin['liquidity']['balance_date_eurusd']
    assert fin['calculation']['attribution_usd_m']['direct'] == pytest.approx(-150)
    d = fin['cash_impact']['direct']
    assert d['impact_eur_m'] == pytest.approx(-150 / rate) and [f['effect_id'] for f in d['flows']] == ['fine-eu']
    assert fin['cash_impact']['net_eur_m'] == pytest.approx(plain['cash_impact']['net_eur_m'] - 150 / rate)
    assert [r['impact_eur_m'] for r in fin['cash_impact']['rows']] == pytest.approx([r['impact_eur_m'] for r in plain['cash_impact']['rows']])
    assert fin['calculation']['scenario_min_usd_m'] == pytest.approx(plain['calculation']['scenario_min_usd_m'] - 150)
    rows = client.get(f"/api/v1/engine/runs/{fin['calculation']['run_id']}/cashflows").json()['by_driver_month']
    assert [r['delta_usd_m'] for r in rows if r['driver'] == 'direct'] == pytest.approx([-150])
    bad = {**flow, 'date': AS_OF.isoformat()}
    assert client.post('/api/v1/dashboard', json={'scenario': {'direct': {'cash_flows': [bad]}}}).status_code == 422
