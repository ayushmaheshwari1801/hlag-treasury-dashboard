"""Row Methodology view and sensitivity on hlag_engine: the engine's components add up to the card, the sensitivity
points are real runs that reproduce the card, the view's header reads the engine store (option A; the page tiles are
not asserted), and runs with a direct cash flow still open the view."""
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip('treasury_gateway.engine_adapter', reason='hlag_engine is not installed')
from fastapi.testclient import TestClient  # noqa: E402

from treasury_gateway.app import create_app  # noqa: E402
from treasury_gateway.assistant import Assistant  # noqa: E402
from treasury_gateway.dashboard_adapter import ROWS  # noqa: E402
from treasury_gateway.news_v8 import NewsV8Reader  # noqa: E402
from treasury_sensitivity.audit import AuditStore  # noqa: E402
from treasury_sensitivity.service import SensitivityService  # noqa: E402

NEWS = Path(__file__).resolve().parents[3] / 'news-engine-v8/examples/event-result.json'
pytestmark = pytest.mark.spec_engine


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('methodology')
    NewsV8Reader(tmp / 'news').import_result(json.loads(NEWS.read_text(encoding='utf-8')))
    service = SensitivityService(audit=AuditStore(':memory:'),
                                 clock=lambda: datetime(2026, 11, 3, 9, tzinfo=ZoneInfo('Europe/Berlin')))
    return TestClient(create_app(service=service, news_dir=tmp / 'news', state_db=tmp / 'state.sqlite',
                                 assistant=Assistant(None), cash_engine='spec'))


def page(client, months=6, **body):
    return client.post('/api/v1/dashboard', json={'horizon_months': months, 'news_limit': 6, **body}).json()['finance']


def method(client, fin, row, months=6):
    r = client.post('/api/v1/engine/methodology',
                    json={'row_id': row, 'run_id': fin['calculation']['run_id'], 'horizon_months': months})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize('shocks', [{}, {'freight': 12, 'bunker': -60, 'interest': 50, 'fx_eur': 5, 'fx_cad': -4}])
def test_components_inside_the_horizon_add_up_to_each_card_row(client, shocks):
    for months in (3, 12):
        fin = page(client, months, shocks=shocks)
        for row in fin['cash_impact']['rows']:
            m = method(client, fin, row['id'], months)
            inside = sum(c['inside_eur_m'] for c in m['breakdown']['components'])
            assert inside == pytest.approx(row['impact_eur_m'], abs=1e-6), row['id']
            assert m['breakdown']['totals_eur_m']['on_or_before_t0'] == pytest.approx(0, abs=1e-9)


def test_every_declared_component_is_listed(client):
    fin = page(client)
    bunker = method(client, fin, 'bunker')
    assert [c['id'] for c in bunker['breakdown']['components']] == \
        ['price_effect', 'consumption_effect', 'mfr', 'hedge_settlement', 'margin']
    hedge = next(c for c in bunker['breakdown']['components'] if c['id'] == 'hedge_settlement')
    assert hedge['inside_eur_m'] == 0  # no hedge tested in this scenario
    freight = method(client, fin, 'freight')
    spot = next(c for c in freight['breakdown']['components'] if c['id'] == 'spot')
    assert {d['detail'] for d in spot['details']} <= {'Asia–Europe', 'Asia–America', 'Europe–America',
                                                      'Africa & Intraregional'}


def test_text_formulas_and_sources_come_from_the_engine(client):
    from hlag_engine.engine.methodology import SPECS
    fin = page(client)
    m = method(client, fin, 'freight')
    assert m['summary'] == list(SPECS['freight'].summary)
    assert m['approximation']['label'] == 'rough approximation for intuition'
    beta = next(s for f in m['formulas'] for s in f['symbols'] if s['symbol'] == 'β')
    assert beta['value'] == 0.5 and beta['key'] == 'freight.beta'
    assert any(d['source'] == 'Hapag-Lloyd Investor Report H1 2026, p. 10' for d in m['disclosed'])
    assert {x['source'] for x in m['market_inputs']} == {'Drewry WCI via Bloomberg Terminal'}
    assert [p['entry_id'] for p in m['playbook']][0] == 'freight.sustained_fall'
    assert m['limitations']


def test_override_status_shows_old_and_new(client):
    fin = page(client, overrides={'freight.beta': 0.6})
    beta = next(a for a in method(client, fin, 'freight')['assumptions'] if a['key'] == 'freight.beta')
    assert (beta['profile_value'], beta['value'], beta['layer'], beta['changed']) == (0.5, 0.6, 'override', True)


def test_header_series_and_history_ranges(client):
    fin = page(client)
    freight = method(client, fin, 'freight')['header']
    assert freight['default'] == 'FREIGHT_COMPOSITE'
    assert [s['series_id'] for s in freight['series']] == ['FREIGHT_COMPOSITE', 'WCI_SHA_RTM', 'WCI_SHA_LAX',
                                                          'WCI_RTM_NYC', 'WCI_COMPOSITE']
    assert abs(sum(freight['series'][0]['weights'].values()) - 1) < 1e-9
    ranges = freight['history']['FREIGHT_COMPOSITE']['ranges']
    assert '3M' in ranges and '2Y' not in ranges  # about one year of weekly WCI history
    assert method(client, fin, 'interest')['header']['history'] == {}  # no chart for interest and FX
    assert [s['series_id'] for s in method(client, fin, 'bunker')['header']['series']] == \
        ['VLSFO_SIN', 'VLSFO_RTM', 'HSFO_SIN', 'HSFO_RTM']


def test_fx_rows_state_which_lines_they_include(client):
    fin = page(client)
    assert 'freight lines in EUR' in method(client, fin, 'fx_eur')['lines_included']
    assert method(client, fin, 'fx_gbp')['lines_included'].startswith('No cash lines')
    assert method(client, fin, 'fx_gbp')['memo']['positions_usd_m'] == {'GBP': -24.0}


def test_sensitivity_points_are_real_runs_that_reproduce_the_card(client):
    r = client.post('/api/v1/engine/sensitivity', json={'row_id': 'freight', 'horizon_months': 6, 'shocks': {'freight': 10}})
    assert r.status_code == 200 and r.json()['cached'] is False
    points = r.json()['points']
    assert [p['step_pct'] for p in points] == [-20, -10, 0, 10, 20]
    card = next(x for x in page(client, shocks={'freight': 10})['cash_impact']['rows'] if x['id'] == 'freight')
    assert points[3]['impact_eur_m'] == pytest.approx(card['impact_eur_m'], abs=1e-9)
    values = [p['impact_eur_m'] for p in points]
    assert values == sorted(values) and points[2]['impact_eur_m'] == pytest.approx(0, abs=1e-6)
    assert abs(values[4] - values[3]) != pytest.approx(abs(values[1] - values[0]), rel=1e-6)  # not linear scaling
    again = client.post('/api/v1/engine/sensitivity', json={'row_id': 'freight', 'horizon_months': 6, 'shocks': {'freight': 10}})
    assert again.json()['cached'] is True and again.json()['points'] == points


def test_bunker_sensitivity_uses_the_slider_unit(client):
    r = client.post('/api/v1/engine/sensitivity', json={'row_id': 'bunker', 'horizon_months': 6}).json()
    vlsfo = next(m['value'] for m in page(client)['market_metrics'] if m['id'] == 'bunker')
    assert r['points'][3]['slider_value'] == pytest.approx(vlsfo * 0.1)
    assert r['slider_unit'] == 'USD/t'
    assert client.post('/api/v1/engine/sensitivity', json={'row_id': 'interest'}).status_code == 422


def test_header_values_dates_and_sources_come_from_the_engine_store(client):
    """Option A: the view's header is the engine store's T0 snapshot. Each value carries its real print date
    (shown as "Published") and the engine's source name."""
    from hlag_engine.engine.methodology import SERIES_SOURCE
    from hlag_engine.engine.snapshot import build_snapshot
    from treasury_gateway.engine_adapter import EngineAdapter
    from treasury_gateway.methodology import HEADER_SERIES
    fin = page(client)
    store = EngineAdapter().shared.store
    snapshot = build_snapshot(store, date.fromisoformat(fin['as_of']))
    for row in ROWS:
        for h in method(client, fin, row)['header']['series']:
            if h.get('derived'):
                continue
            snap = snapshot[h['series_id']]
            assert (h['value'], h['date']) == (snap.value, snap.value_date.isoformat()), h['series_id']
            assert h['source'] == SERIES_SOURCE[h['series_id']], h['series_id']
            assert store.get_series(h['series_id'], snap.value_date, snap.value_date, include_filled=False), \
                f"{h['series_id']}: the header date is a real print, not a forward-filled day"
    assert {sid for ids in HEADER_SERIES.values() for sid in ids} <= set(SERIES_SOURCE)


def test_direct_cash_flow_run_opens_the_view_for_every_row(client):
    """A run with a direct cash flow (hlag_engine owner direct): the view answers for every row, the components
    inside the horizon still add up to each card row (direct cash is its own entry, not a driver row), and the
    response is read-only."""
    flow = {'effect_id': 'fine-eu', 'overlap_key': 'eu-fine', 'date': '2026-12-15', 'amount_usd_m': -150,
            'label': 'EU fine', 'rationale': 'Stated fine', 'event_ids': ['E1']}
    fin = page(client, 6, shocks={'freight': 8}, scenario={'name': 'Fine', 'direct': {'cash_flows': [flow]}})
    assert fin['cash_impact']['direct']['impact_usd_m'] == pytest.approx(-150)
    for row in fin['cash_impact']['rows']:
        m = method(client, fin, row['id'])
        assert m['read_only'] is True
        assert {'summary', 'formulas', 'assumptions', 'breakdown', 'header', 'lines_included'} <= set(m)
        inside = sum(c['inside_eur_m'] for c in m['breakdown']['components'])
        assert inside == pytest.approx(row['impact_eur_m'], abs=1e-6), row['id']


def test_unknown_run_is_404(client):
    r = client.post('/api/v1/engine/methodology', json={'row_id': 'freight', 'run_id': 'nope', 'horizon_months': 6})
    assert r.status_code == 404
    assert set(ROWS) == {'freight', 'bunker', 'interest', 'fx_eur', 'fx_gbp', 'fx_cny', 'fx_cad'}
