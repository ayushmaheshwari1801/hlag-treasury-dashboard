"""Row Methodology view (hlag_engine only, read-only).

All text, formulas, components, assumptions, sources, playbook entries and limitations come from
hlag_engine.engine.methodology; this module only selects the row's part, buckets the engine's dated components by
the selected horizon, converts to EUR at the run's T0 rate and adds the header series and their history.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from types import SimpleNamespace

from .dashboard_adapter import NAMES, ROWS, horizon_cutoff

ROW_DRIVER = {'freight': 'freight', 'bunker': 'bunker', 'interest': 'rates',
              'fx_eur': 'fx', 'fx_gbp': 'fx', 'fx_cny': 'fx', 'fx_cad': 'fx'}
FX_ROW_CURRENCY = {'GBP': 'fx_gbp', 'CNY': 'fx_cny', 'CAD': 'fx_cad'}
ROW_LABEL = {**NAMES}
FULL_VERSION = {'freight', 'bunker'}  # history chart and sensitivity table
TRADE_SERIES = {'ASIA_EUROPE': 'WCI_SHA_RTM', 'ASIA_AMERICA': 'WCI_SHA_LAX', 'EUROPE_AMERICA': 'WCI_RTM_NYC',
                'AFRICA_INTRA': 'WCI_COMPOSITE'}
TRADE_NAME = {'ASIA_EUROPE': 'Asia–Europe', 'ASIA_AMERICA': 'Asia–America', 'EUROPE_AMERICA': 'Europe–America',
              'AFRICA_INTRA': 'Africa & Intraregional'}
HEADER_SERIES = {
    'freight': ['WCI_SHA_RTM', 'WCI_SHA_LAX', 'WCI_RTM_NYC', 'WCI_COMPOSITE'],
    'bunker': ['VLSFO_SIN', 'VLSFO_RTM', 'HSFO_SIN', 'HSFO_RTM'],
    'interest': ['ESTR', 'SOFR', 'BB_OAS'],
    'fx_eur': ['FX_USD_PER_EUR', 'FX_USD_PER_INR', 'FX_USD_PER_BRL'],
    'fx_gbp': ['FX_USD_PER_GBP'], 'fx_cny': ['FX_USD_PER_CNY'], 'fx_cad': ['FX_USD_PER_CAD'],
}
COMPOSITE = 'FREIGHT_COMPOSITE'
RANGES = (('1M', 30), ('3M', 91), ('6M', 182), ('1Y', 365), ('2Y', 730))
SENSITIVITY_STEPS = (-20, -10, 0, 10, 20)
MODULE_NAME = {'freight': 'Freight', 'bunker': 'Bunker', 'rates': 'Interest'}
OWN_NAME = {'opex': 'operating costs', 'dividend': 'dividend', 'maturity': 'debt principal', 'obligation': 'obligations'}


def fx_row(currency: str) -> str:
    return FX_ROW_CURRENCY.get(currency, 'fx_eur')


def row_items(items: list[dict], row_id: str) -> list[dict]:
    driver = ROW_DRIVER[row_id]
    return [i for i in items if i['driver'] == driver and (driver != 'fx' or fx_row(i['currency']) == row_id)]


def _eur(usd, rate):
    return None if usd is None or not rate else usd / rate


def row_breakdown(run: dict, row_id: str, months: int, components: list[dict]) -> dict:
    """The row's engine components split into: inside the selected horizon (the card figure), after it but inside
    the engine horizon, and after the engine horizon (not on the liquidity path)."""
    as_of = date.fromisoformat(run['as_of'])
    cutoff = horizon_cutoff(as_of, months)
    b = run.get('breakdown') or {}
    rate = (run.get('market_snapshot', {}).get('FX_USD_PER_EUR') or {}).get('value')
    multi_ccy = row_id == 'fx_eur'
    sums = defaultdict(lambda: defaultdict(float))  # component -> bucket -> usd
    details = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for i in row_items(b.get('items', []), row_id):
        bucket = 'on_or_before_t0' if i['date'] <= run['as_of'] else 'inside' if i['date'] <= cutoff else 'after'
        detail = _detail_label(row_id, i, multi_ccy)
        sums[i['component']][bucket] += i['usd_m']
        details[i['component']][detail][bucket] += i['usd_m']
    for i in row_items(b.get('beyond_horizon', []), row_id):
        sums[i['component']]['beyond'] += i['usd_m']
        details[i['component']][_detail_label(row_id, i, multi_ccy)]['beyond'] += i['usd_m']
    buckets = ('inside', 'after', 'beyond', 'on_or_before_t0')
    out = []
    for c in components:
        cid = c['id']  # every declared component is listed, zero when the scenario does not use it
        out.append({**c, **{f'{k}_usd_m': sums[cid].get(k, 0.) for k in buckets},
                    **{f'{k}_eur_m': _eur(sums[cid].get(k, 0.), rate) for k in buckets},
                    'details': [{'detail': d, **{f'{k}_eur_m': _eur(v.get(k, 0.), rate) for k in buckets}}
                                for d, v in sorted(details[cid].items(), key=lambda kv: -abs(kv[1].get('inside', 0.)))]})
    totals = {k: sum(c[f'{k}_usd_m'] for c in out) for k in buckets}
    return {'components': out, 'totals_usd_m': totals, 'totals_eur_m': {k: _eur(v, rate) for k, v in totals.items()},
            'horizon_end': cutoff, 'engine_horizon_end': b.get('horizon_end'), 'eurusd': rate,
            'beyond_horizon_note': b.get('beyond_horizon_note'),
            'note': 'Inside the selected horizon is the figure on the card. Amounts after the selected horizon are in '
                    'the engine run but not in this figure; amounts after the engine horizon are not on the '
                    'liquidity path.'}


def _detail_label(row_id: str, item: dict, multi_ccy: bool) -> str:
    detail = item.get('detail') or '—'
    if item['driver'] == 'fx':
        if item['component'] == 'conversion':
            detail = f"{MODULE_NAME.get(detail, detail)} lines"
        elif item['component'] == 'own_exposure':
            detail = OWN_NAME.get(detail, detail)
        else:
            detail = detail.replace('_', ' ')
        return f"{detail} · {item['currency']}" if multi_ccy else detail
    if detail in TRADE_NAME:
        return TRADE_NAME[detail]
    return detail


def lines_included(run: dict, row_id: str) -> str:
    """Plain statement of which modules' lines the row includes (from the run's own breakdown)."""
    items = row_items((run.get('breakdown') or {}).get('items', []), row_id)
    if ROW_DRIVER[row_id] != 'fx':
        return f"All {MODULE_NAME[ROW_DRIVER[row_id]].lower()} cash lines dated after T0 and inside the selected horizon."
    conv = sorted({(MODULE_NAME.get(i['detail'], i['detail']), i['currency']) for i in items
                   if i['component'] == 'conversion' and i['currency'] != 'USD'})
    own = sorted({(OWN_NAME.get(i['detail'], i['detail']), i['currency']) for i in items if i['component'] == 'own_exposure'})
    hedges = sorted({i['currency'] for i in items if i['component'] == 'policy_hedge'})
    parts = []
    if conv:
        parts.append('currency effect on ' + ', '.join(f'{m.lower()} lines in {c}' for m, c in conv))
    if own:
        parts.append("FX's own " + ', '.join(f'{c} {n}' for n, c in own))
    if hedges:
        parts.append('policy hedges on ' + ', '.join(hedges))
    if not parts:
        return 'No cash lines: this currency has no cash flows in the run (memo position only).'
    return 'Includes ' + '; '.join(parts) + '.'


# ---------------------------------------------------------------- header series and history


def _ranges(points: list[dict], weekly: bool) -> list[str]:
    if not points:
        return []
    first, last = date.fromisoformat(points[0]['date']), date.fromisoformat(points[-1]['date'])
    slack = 14 if weekly else 4  # a window counts as covered when history starts within ~two prints of its start
    return [label for label, days in RANGES if (last - first).days + slack >= days]


def series_header(market: dict, sid: str, name: str) -> dict:
    s = next((r for r in market['series'] if r.get('series_id') == sid), {'series_id': sid, 'value': None})
    week = market.get('weekly', {}).get(sid)
    change = pct = None
    if week and s.get('value') is not None:
        change = week['last_value'] - week['value']
        pct = change / week['value'] * 100 if week['value'] and s.get('unit') != '%' else None
        if s.get('unit') == '%':
            change *= 100  # bp
    return {'series_id': sid, 'name': name, 'value': s.get('value'), 'unit': s.get('unit'), 'date': s.get('date'),
            'change': change, 'change_unit': 'bp' if s.get('unit') == '%' else s.get('unit'), 'change_pct': pct,
            'comparison_date': week['date'] if week else None, 'provenance': s.get('label'),
            'confirmed': s.get('confirmed'), 'stale': s.get('stale'), 'source': s.get('source'),
            'source_ref': s.get('source_ref')}


def header(market: dict, row_id: str, profile, series_name: dict) -> dict:
    """Header series for the row; freight adds the H1-volume-weighted composite (engine freight_composite)."""
    from hlag_engine.engine.methodology import composite_weights, freight_composite
    out = [series_header(market, sid, series_name.get(sid, sid)) for sid in HEADER_SERIES[row_id]]
    history = {}
    if row_id in FULL_VERSION:
        for sid in HEADER_SERIES[row_id]:
            history[sid] = market['history'].get(sid, [])
    default = out[0]['series_id']
    if row_id == 'freight':
        for h, trade in zip(out, TRADE_SERIES):
            h['trade'] = trade
            h['trade_name'] = TRADE_NAME[trade]
        out[-1]['name'] += ' · Africa & Intraregional proxy'
        hist = {sid: [(date.fromisoformat(p['date']), p['value']) for p in pts] for sid, pts in history.items()}
        comp = [{'date': d.isoformat(), 'value': v} for d, v in freight_composite(hist, profile)]
        history[COMPOSITE] = comp
        prov = [h['provenance'] for h in out]
        last, prev = (comp[-1], comp[-2]) if len(comp) > 1 else (comp[-1] if comp else None, None)
        out.insert(0, {'series_id': COMPOSITE, 'name': 'Volume-weighted WCI (H1 2026 trade volumes)',
                       'value': last['value'] if last else None, 'unit': 'USD/FEU', 'date': last['date'] if last else None,
                       'change': last['value'] - prev['value'] if prev else None, 'change_unit': 'USD/FEU',
                       'change_pct': (last['value'] / prev['value'] - 1) * 100 if prev else None,
                       'comparison_date': prev['date'] if prev else None,
                       'provenance': 'MARKET' if all(p == 'MARKET' for p in prov) else 'DERIVED',
                       'confirmed': all(h['confirmed'] for h in out), 'stale': any(h['stale'] for h in out),
                       'source': 'Drewry WCI via Bloomberg Terminal; weights: Hapag-Lloyd Investor Report H1 2026, p. 10',
                       'weights': composite_weights(profile), 'derived': True})
        default = COMPOSITE
    return {'series': out, 'default': default,
            'history': {sid: {'points': pts, 'ranges': _ranges(pts, True), 'first': pts[0]['date'] if pts else None}
                        for sid, pts in history.items()}}


# ---------------------------------------------------------------- full response


def _snapshot(run: dict) -> dict:
    return {sid: SimpleNamespace(value=e['value'], value_date=date.fromisoformat(e['date']), unit=e.get('unit'),
                                 label=e.get('label'), confirmed=bool(e.get('confirmed', 1)), stale=bool(e.get('stale')))
            for sid, e in run.get('market_snapshot', {}).items()}


def methodology(shared, run: dict, row_id: str, months: int, market: dict) -> dict:
    from hlag_engine.company_profile.resolve import RunContext
    from hlag_engine.contracts.scenario import ScenarioParams
    from hlag_engine.engine.methodology import SERIES_NAME, describe
    from .engine_adapter import to_engine_adjustments
    if row_id not in ROWS:
        raise ValueError(f'unknown row {row_id}')
    if 'breakdown' not in run:
        raise LookupError('This saved run has no engine breakdown; run the scenario again')
    as_of = date.fromisoformat(run['as_of'])
    params = ScenarioParams.model_validate(run['engine_params'])
    adjustments = to_engine_adjustments(run.get('adjustments'), as_of)
    ctx = RunContext.for_scenario(shared.profile, as_of, params, tuple(adjustments), shared.registers)
    with shared.lock:
        desc = describe(ROW_DRIVER[row_id], shared.profile, ctx, _snapshot(run), params)
    out = {'row_id': row_id, 'label': ROW_LABEL[row_id], 'driver': ROW_DRIVER[row_id], 'run_id': run['run_id'],
           'as_of': run['as_of'], 'horizon_months': months, 'full': row_id in FULL_VERSION,
           **desc, 'breakdown': row_breakdown(run, row_id, months, desc['components']),
           'header': header(market, row_id, shared.profile, SERIES_NAME),
           'lines_included': lines_included(run, row_id),
           'read_only': True}
    if out['driver'] == 'fx':
        out['memo'] = _memo(run, row_id)
    return out


def _memo(run, row_id):
    memo = run.get('translation_memo') or {}
    ccys = {'fx_eur': ('EUR', 'INR', 'BRL'), 'fx_gbp': ('GBP',), 'fx_cny': ('CNY',), 'fx_cad': ('CAD',)}[row_id]
    held = {c: v for c, v in (memo.get('positions_usd_m') or {}).items() if c in ccys}
    if not held:
        return None
    return {'positions_usd_m': held, 'revaluation_usd_m': {c: (memo.get('revaluation_usd_m') or {}).get(c, 0.) for c in held},
            'horizon_end': memo.get('horizon_end'), 'cash': False}


# ---------------------------------------------------------------- sensitivity


def sensitivity_shock(row_id: str, step_pct: float, market: dict) -> float:
    """The page slider value for a fixed driver move: freight = route rate move in % on every trade; bunker = the
    $/t move equal to step_pct of VLSFO Rotterdam at T0, applied to every fuel grade (as the bunker slider does)."""
    if row_id == 'freight':
        return float(step_pct)
    price = next((r.get('value') for r in market['series'] if r.get('series_id') == 'VLSFO_RTM'), None)
    if not price:
        raise ValueError('VLSFO_RTM price is required for the bunker sensitivity')
    return round(price * step_pct / 100, 6)


def sensitivity_cache_key(*parts) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def row_value(run: dict, row_id: str, months: int) -> dict:
    from .dashboard_adapter import row_amounts
    as_of = date.fromisoformat(run['as_of'])
    cutoff = horizon_cutoff(as_of, months)
    lines = [l for l in run.get('lines', []) if run['as_of'] < l['date'] <= cutoff]
    usd = row_amounts(lines)[row_id]
    rate = (run.get('market_snapshot', {}).get('FX_USD_PER_EUR') or {}).get('value')
    base = [r for r in run.get('baseline_path', []) if run['as_of'] < r['date'] <= cutoff]
    scen = [r for r in run.get('scenario_path', []) if run['as_of'] < r['date'] <= cutoff]
    hs = min((r['headroom_usd_m'] for r in scen), default=None)
    hb = min((r['headroom_usd_m'] for r in base), default=None)
    return {'impact_usd_m': usd, 'impact_eur_m': _eur(usd, rate),
            'scenario_min_headroom_eur_m': _eur(hs, rate), 'headroom_change_eur_m': _eur(hs - hb, rate) if hs is not None and hb is not None else None}
