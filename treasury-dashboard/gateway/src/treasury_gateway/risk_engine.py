"""Deterministic, native-currency cash-flow prototype from the combined specification.

The engine reads immutable market snapshots and never calls an LLM or network.
All monetary line amounts are in native currency millions; summary amounts USD m.
"""
from __future__ import annotations

import calendar
import copy
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

CCYS = ('EUR', 'CAD', 'GBP', 'CNY', 'INR', 'BRL')
TRADES = ('ASIA_EUROPE', 'ASIA_AMERICA', 'EUROPE_AMERICA', 'AFRICA_INTRA')
FUEL = ('VLSFO_SIN', 'VLSFO_RTM', 'HSFO_SIN', 'HSFO_RTM')
WCI = ('WCI_SHA_RTM', 'WCI_SHA_LAX', 'WCI_RTM_NYC', 'WCI_COMPOSITE')
MATERIAL = (*('FX_USD_PER_' + c for c in CCYS), 'SOFR', 'ESTR', 'BB_OAS', *WCI, *FUEL)
PROFILE = Path(__file__).with_name('risk_profile.json')
ALIASES = {
    'freight.gamma': 'freight.contract_repricing_share',
    'freight.leakage_gap_down': 'freight.leakage_trigger_down',
    'freight.leakage_gap_up': 'freight.leakage_trigger_up',
    'bunker.hub_weight_sin': 'bunker.singapore_weight',
    'bunker.supplier_lag_days': 'bunker.supplier_payment_lag_days',
    'rates.interest_bearing_cash_share': 'rates.interest_bearing_share',
    'fx.hedge_ratio.cad': 'fx.cad_hedge_ratio',
    'fx.hedge_ratio.eur_debt': 'fx.eur_debt_hedge_ratio',
    'fx.hedge_ratio.inr_brl': 'fx.inr_brl_hedge_ratio',
}
BOOL_KEYS = {'liquidity.interest_in_ocf','liquidity.lease_includes_interest',
             'liquidity.count_liquid_investments','fx.inr_brl_obligations_on','fx.transactions_in_capex','freight.trade_mapping_verified'}


def validate_profile(profile):
    """Validate a complete administrator-supplied profile; None preserves an unresolved input."""
    template = json.loads(PROFILE.read_text(encoding='utf-8'))
    if set(profile.get('values', {})) != set(template['values']):
        raise ValueError('Profile must preserve the known input keys')
    for key, entry in profile['values'].items():
        if not isinstance(entry, dict) or 'value' not in entry or 'label' not in entry:
            raise ValueError(f'{key}: value and provenance label required')
        if entry['label'] not in {'DISCLOSED','DERIVED','ASSUMPTION','MARKET','PROXY','MISSING'}:
            raise ValueError(f'{key}: unknown provenance label')
        if entry['value'] is not None:
            RiskEngine._validate_override(key, entry['value'])
        elif template['values'][key]['value'] is not None:
            raise ValueError(f'{key}: required model parameter cannot be null')
        if entry['label'] == 'DISCLOSED' and not entry.get('source'):
            raise ValueError(f'{key}: disclosed value requires source')
    date.fromisoformat(profile['anchor_start']); date.fromisoformat(profile['anchor_end']); date.fromisoformat(profile['balance_date'])
    if profile['anchor_start'] > profile['anchor_end']: raise ValueError('Anchor start must precede end')
    if not profile.get('profile_version'): raise ValueError('profile_version is required')
    if profile['values']['freight.contract_share']['value'] + profile['values']['freight.index_linked_share']['value'] > 1:
        raise ValueError('Freight shares exceed 100%')
    return profile


def add_months(day: date, months: int) -> date:
    y, m = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(y, m + 1, min(day.day, calendar.monthrange(y, m + 1)[1]))


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def days(start: date, end: date):
    for n in range((end - start).days + 1):
        yield start + timedelta(days=n)


def old_debt_balance_days(start, finish, maturity_dates, monthly_maturity, total_debt):
    """Integrate the surviving original debt fraction over [start, finish)."""
    if finish <= start:
        return 0.
    if monthly_maturity is None or not total_debt:
        return float((finish-start).days)
    boundaries = [start, *(d for d in maturity_dates if start < d < finish), finish]
    return sum((right-left).days * max(0., 1-sum(d <= left for d in maturity_dates)*monthly_maturity/total_debt)
               for left, right in zip(boundaries, boundaries[1:]))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


DIRECT_REQUIRED = {'effect_id', 'overlap_key', 'date', 'amount_usd_m', 'label', 'rationale'}
DIRECT_FIELDS = DIRECT_REQUIRED | {'event_ids'}


def scenario_params(raw, as_of):
    """Strict trust boundary. Percentage fields use fractions, bp fields use basis points."""
    p = copy.deepcopy(raw or {})
    allowed = {'name', 'source', 'preset_id', 'event_ids', 'start_date', 'duration_days',
               'default_shape', 'ramp_or_revert_weeks', 'freight', 'bunker', 'rates', 'fx', 'direct', 'overrides', 'hedge_tests'}
    if set(p) - allowed:
        raise ValueError('Unknown scenario fields: ' + ', '.join(sorted(set(p) - allowed)))
    p.setdefault('name', 'Baseline'); p.setdefault('source', 'custom')
    p.setdefault('start_date', as_of.isoformat()); p.setdefault('duration_days', None)
    p.setdefault('default_shape', 'step'); p.setdefault('ramp_or_revert_weeks', 8)
    p.setdefault('event_ids', []); p.setdefault('preset_id', None)
    if not isinstance(p['start_date'],str): raise ValueError('start_date must be an ISO date')
    date.fromisoformat(p['start_date'])
    if p['default_shape'] not in {'step', 'ramp', 'spike_revert'}:
        raise ValueError('default_shape must be step, ramp or spike_revert')
    if p['duration_days'] is not None and (not isinstance(p['duration_days'], int) or not 1 <= p['duration_days'] <= 3650):
        raise ValueError('duration_days must be null or an integer in 1..3650')
    if not _finite(p['ramp_or_revert_weeks']) or not 1 <= p['ramp_or_revert_weeks'] <= 104:
        raise ValueError('ramp_or_revert_weeks must be in 1..104')
    if p['source'] not in {'custom', 'preset', 'auto_built', 'scheduled'}:
        raise ValueError('Invalid scenario source')
    specs = {'freight': {'rate_shock_pct', 'volume_shock_pct', 'transit_delay_days', 'shape'},
             'bunker': {'vlsfo_shock_pct', 'hsfo_shock_pct', 'consumption_uplift_pct', 'uplift_affected_share', 'shape'},
             'rates': {'rate_path', 'credit_spread_change_bp', 'refinancing_mode', 'zim_tranche_on'},
             'fx': {'move_pct', 'shape'}}
    for group, keys in specs.items():
        block = p.setdefault(group, {})
        if not isinstance(block, dict) or set(block) - keys:
            raise ValueError(f'Unknown or invalid {group} scenario fields')
        if 'shape' in block and group != 'fx' and block['shape'] not in {'step', 'ramp', 'spike_revert'}:
            raise ValueError(f'Invalid {group} shape')
    for key, limit in [('rate_shock_pct', (-.95, 3)), ('volume_shock_pct', (-.95, 1)), ('transit_delay_days', (0, 180))]:
        if not isinstance(p['freight'].get(key,{}),dict): raise ValueError(f'freight.{key} must be a trade map')
        for trade, value in p['freight'].get(key, {}).items():
            if trade not in TRADES or not _finite(value) or not limit[0] <= value <= limit[1]:
                raise ValueError(f'Invalid freight {key}: {trade}')
    for key in ('vlsfo_shock_pct', 'hsfo_shock_pct', 'consumption_uplift_pct', 'uplift_affected_share'):
        if key in p['bunker']:
            v = p['bunker'][key]
            if not _finite(v) or not (-.95 <= v <= 3 if key != 'uplift_affected_share' else 0 <= v <= 1):
                raise ValueError(f'Invalid bunker {key}')
    if not isinstance(p['fx'].get('move_pct',{}),dict): raise ValueError('fx.move_pct must be a currency map')
    for c, value in p['fx'].get('move_pct', {}).items():
        if c not in CCYS or not _finite(value) or not -.95 <= value <= 1:
            raise ValueError(f'Invalid FX move: {c}')
    shape = p['fx'].get('shape', {})
    if isinstance(shape, str):
        if shape not in {'step', 'ramp', 'spike_revert'}: raise ValueError('Invalid FX shape')
    elif not isinstance(shape, dict) or any(c not in CCYS or s not in {'step', 'ramp', 'spike_revert'} for c, s in shape.items()):
        raise ValueError('Invalid FX shape')
    r = p['rates']
    if r.get('refinancing_mode', 'rollover') not in {'rollover', 'unavailable'}:
        raise ValueError('Invalid refinancing mode')
    if not _finite(r.get('credit_spread_change_bp', 0)) or not -500 <= r.get('credit_spread_change_bp', 0) <= 2000:
        raise ValueError('Invalid credit spread change')
    if not isinstance(r.get('zim_tranche_on', False), bool): raise ValueError('zim_tranche_on must be boolean')
    if not isinstance(r.get('rate_path',{}),dict): raise ValueError('rates.rate_path must be a currency map')
    for c, steps in r.get('rate_path', {}).items():
        if c not in {'USD', 'EUR'} or not isinstance(steps, list): raise ValueError('Invalid rate path currency')
        for step in steps:
            if not isinstance(step, dict) or set(step) != {'date', 'step_bp'}: raise ValueError('Rate steps require date and step_bp')
            if step['date']=='T0': step['date']=as_of.isoformat()
            date.fromisoformat(step['date'])
            if not _finite(step['step_bp']) or abs(step['step_bp']) > 1000: raise ValueError('Rate step outside +/-1000bp')
    # Direct cash flows: dated signed USD million amounts (hlag_engine only; see engine/direct.py). Full contract
    # checks (identifiers, double counting, policy limits) are the engine's; this boundary checks only the shape.
    direct = p.get('direct')
    if direct is not None:
        if not isinstance(direct, dict) or set(direct) - {'cash_flows'} or not isinstance(direct.get('cash_flows', []), list):
            raise ValueError('direct must be {"cash_flows": [...]}')
        for flow in direct.get('cash_flows', []):
            if not isinstance(flow, dict) or set(flow) - DIRECT_FIELDS or not DIRECT_REQUIRED <= set(flow):
                raise ValueError('Direct cash flows require effect_id, overlap_key, date, amount_usd_m, label and rationale')
            if not isinstance(flow['date'], str): raise ValueError('Direct cash date must be an ISO date')
            date.fromisoformat(flow['date'])
            if not _finite(flow['amount_usd_m']) or flow['amount_usd_m'] == 0:
                raise ValueError('Direct cash amount_usd_m must be a finite nonzero number')
        if not direct.get('cash_flows'):
            p.pop('direct')
    if not isinstance(p.setdefault('overrides', {}), dict): raise ValueError('overrides must be a dictionary')
    if not isinstance(p.setdefault('hedge_tests', []),list): raise ValueError('hedge_tests must be a list')
    for hedge in p['hedge_tests']:
        if not isinstance(hedge,dict) or set(hedge)-{'module','instrument','target','coverage_pct','tenor_months','start_date','strike_rule'}:
            raise ValueError('Invalid hedge fields')
        if hedge.get('module')!='bunker' or hedge.get('instrument') not in {'swap','fixed_price_supply'} or hedge.get('target') not in {'fuel_all','vlsfo','hsfo'}:
            raise ValueError('Supported hedge: bunker swap/fixed_price_supply on fuel_all/vlsfo/hsfo')
        if not _finite(hedge.get('coverage_pct')) or not 0<=hedge['coverage_pct']<=1: raise ValueError('Hedge coverage must be 0..1')
        if not isinstance(hedge.get('tenor_months'),int) or not 1<=hedge['tenor_months']<=12: raise ValueError('Hedge tenor must be 1..12 months')
        hedge.setdefault('start_date',as_of.isoformat()); date.fromisoformat(hedge['start_date'])
        if hedge.get('strike_rule','spot_at_T0')!='spot_at_T0': raise ValueError('Only spot_at_T0 strike is supported')
    return p


def factor(p, day, shape=None):
    elapsed = (day - date.fromisoformat(p['start_date'])).days
    if elapsed < 0 or (p['duration_days'] is not None and elapsed >= p['duration_days']): return 0.
    n = p['ramp_or_revert_weeks'] * 7
    shape = shape or p['default_shape']
    return min(1., elapsed / n) if shape == 'ramp' else max(0., 1 - elapsed / n) if shape == 'spike_revert' else 1.


class RiskEngine:
    def __init__(self, market_db, state_db=None, *, profile=None, demo=False):
        self.market_db = Path(market_db)
        self.state_db = state_db
        self.demo = demo
        self.profile = copy.deepcopy(profile) if isinstance(profile, dict) else json.loads(Path(profile or PROFILE).read_text(encoding='utf-8'))
        validate_profile(self.profile)

    def presets(self):
        def preset(id, name, **kwargs): return {'id': id, 'name': name, 'scenario': {'name': name, 'source': 'preset', 'preset_id': id, **kwargs}}
        all_rates = {t: -.2 for t in TRADES}; all_volume = {t: -.05 for t in TRADES}
        result = [preset('baseline', 'Baseline'),
            preset('red_sea_reescalation', 'Red Sea re-escalation', duration_days=182,
                   freight={'rate_shock_pct': {'ASIA_EUROPE': .4}, 'transit_delay_days': {'ASIA_EUROPE': 12}},
                   bunker={'vlsfo_shock_pct': .15, 'hsfo_shock_pct': .12, 'consumption_uplift_pct': .3, 'uplift_affected_share': .35}),
            preset('demand_slump', 'Demand slump', default_shape='ramp', ramp_or_revert_weeks=8,
                   freight={'rate_shock_pct': all_rates, 'volume_shock_pct': all_volume},
                   bunker={'vlsfo_shock_pct': -.15, 'hsfo_shock_pct': -.15, 'consumption_uplift_pct': -.03, 'uplift_affected_share': 1}),
            preset('oil_spike', 'Oil spike', default_shape='spike_revert', ramp_or_revert_weeks=26,
                   bunker={'vlsfo_shock_pct': .3, 'hsfo_shock_pct': .2}),
            preset('eur_rally', 'EUR rally', fx={'move_pct': {'EUR': .1, 'GBP': .05, 'CAD': .03}}),
            preset('em_stress', 'Emerging-market stress', fx={'move_pct': {'INR': -.15, 'BRL': -.15}}),
            preset('credit_squeeze', 'Credit squeeze', rates={'credit_spread_change_bp': 200, 'refinancing_mode': 'unavailable'})]
        # Meeting-dated presets require verified future official calendars; no dates are fabricated.
        for c in CCYS:
            for direction, v in [('up', .1), ('down', -.1)]:
                result.append(preset(f'fx_{c.lower()}_{direction}_10', f'{c} {v:+.0%}', fx={'move_pct': {c: v}}))
        for direction, bp in [('up',100),('down',-100)]:
            result.append(preset(f'rates_{direction}_100bp',f'Rates {bp:+} bp',rates={'rate_path':{c:[{'date':'T0','step_bp':bp}] for c in ('USD','EUR')}}))
        return result

    def run_pair(self, as_of, scenario, state_change, adjustments=None):
        first=self.run(as_of,scenario,adjustments)
        second_params=copy.deepcopy(scenario or {})
        for key,value in state_change.items():
            if key not in {'overrides','hedge_tests','rates'}: raise ValueError('Pair only accepts state changes')
            if key=='rates' and set(value)-{'zim_tranche_on'}: raise ValueError('Only ZIM is a rates state field')
            second_params[key]={**second_params.get(key,{}),**value} if isinstance(value,dict) else value
        second=self.run(as_of,second_params,adjustments)
        return {'label':'state comparison','run_a':first,'run_b':second,
                'difference':{'min_headroom_usd_m':second['headroom']['scenario_min_usd_m']-first['headroom']['scenario_min_usd_m'],
                              'attribution_usd_m':{k:second['attribution'][k]-first['attribution'][k] for k in second['attribution']}}}

    def _market(self, as_of):
        snapshot, anchor, history = {}, {}, {}
        if self.market_db.exists():
            uri = self.market_db.resolve().as_uri() + '?mode=ro'
            with sqlite3.connect(uri, uri=True) as db:
                db.row_factory = sqlite3.Row
                for s in (*MATERIAL, 'UST_2Y', 'EA_2Y'):
                    rows = [dict(r) for r in db.execute('SELECT * FROM market_data WHERE series_id=? AND date<=? ORDER BY date', (s, as_of.isoformat()))]
                    history[s] = rows
                    if rows: snapshot[s] = rows[-1]
                    period = [r['value'] for r in rows if self.profile['anchor_start'] <= r['date'] <= self.profile['anchor_end']]
                    if period: anchor[s] = sum(period) / len(period)
        reference=self.profile.get('disclosure_reference',{})
        disclosed_average=reference.get('average_usd_per_eur_h1_2026')
        if (not self.demo and self.profile['anchor_start']=='2026-01-01' and self.profile['anchor_end']=='2026-06-30'
                and _finite(disclosed_average) and disclosed_average>0):
            anchor['FX_USD_PER_EUR']=disclosed_average
        if self.demo:
            values = {'SOFR': 3.5, 'ESTR': 2., 'BB_OAS': 2., 'UST_2Y': 3.8, 'EA_2Y': 2.4,
                      'VLSFO_SIN': 600., 'VLSFO_RTM': 550., 'HSFO_SIN': 450., 'HSFO_RTM': 400.,
                      'WCI_SHA_RTM': 2500., 'WCI_SHA_LAX': 3000., 'WCI_RTM_NYC': 2000., 'WCI_COMPOSITE': 2400.,
                      **{'FX_USD_PER_' + c: v for c, v in zip(CCYS, (1.15, .72, 1.3, .14, .012, .18))}}
            # Demo is wholly illustrative, never a silent replacement of missing real observations.
            snapshot = {s: {'series_id': s, 'date': as_of.isoformat(), 'value': v, 'unit': '%' if s in ('SOFR','ESTR','BB_OAS','UST_2Y','EA_2Y') else 'demo',
                            'label': 'ASSUMPTION', 'source': 'illustrative_demo', 'source_ref': 'not market data', 'confirmed': True} for s, v in values.items()}
            anchor = dict(values)
            history = {}
        return snapshot, anchor, history

    def run(self, as_of: date, scenario=None, adjustments=None):
        p = scenario_params(scenario, as_of)
        if p.get('direct'):
            raise ValueError('Direct cash flows require hlag_engine (CASH_ENGINE=spec); the mock engine does not model them')
        profile = copy.deepcopy(self.profile)
        if as_of<date.fromisoformat(profile['balance_date']) and not self.demo: raise ValueError('as_of precedes the profile disclosure date')
        missing = [k for k, v in profile['values'].items() if v['value'] is None]
        if self.demo:
            demo_values = {'liquidity.lease_monthly_usd_m': 60, 'liquidity.capex_monthly_usd_m': 180,
                           'rates.closing_usd_per_eur_2025': 1.15, 'rates.financial_debt_usd_m': 5000,
                           'rates.current_leases_usd_m': 720, 'fx.dividend_eur_m': 200,
                           'liquidity.historical_financing_adjustment_usd_m':0, 'freight.trade_mapping_verified':True,
                           **{'freight.quarter_volume_teu.Q'+str(q):3342000 for q in range(1,5)}}
            for key, value in demo_values.items(): profile['values'][key] = {'value': value, 'label': 'ASSUMPTION', 'source': 'Illustrative demo only'}
            missing = []
        for key, value in p['overrides'].items():
            if key not in profile['values']: raise ValueError(f'Unknown profile override {key}')
            old = profile['values'][key]
            if old['label'] == 'DISCLOSED': raise ValueError(f'Disclosed value cannot be overridden: {key}')
            self._validate_override(key, value)
        applied = []
        for original in adjustments or []:
            a = copy.deepcopy(original)
            if a.get('status') not in {'approved', None}: continue
            if a.get('review_date') and a['review_date'] <= as_of.isoformat(): continue
            key = ALIASES.get(a.get('param_key'), a.get('param_key'))
            a['param_key'] = key
            if key == 'rates.zim_tranche_on':
                if not isinstance(a['new_value'], bool): raise ValueError('ZIM state must be boolean')
                if a.get('effective_date', as_of.isoformat()) <= as_of.isoformat(): p['rates']['zim_tranche_on'] = a['new_value']
                continue
            if key not in profile['values']: raise ValueError(f'Unknown adjustment key {key}')
            if profile['values'][key]['label'] == 'DISCLOSED': raise ValueError('Cannot adjust disclosed inputs')
            self._validate_override(key, a['new_value']); applied.append(copy.deepcopy(a))
        def val(key, day=as_of):
            v = profile['values'][key]['value']
            for a in sorted(applied, key=lambda a: (a.get('effective_date', ''), a.get('decided_at', ''))):
                if a['param_key'] == key and a.get('effective_date', as_of.isoformat()) <= day.isoformat() and (not a.get('review_date') or day.isoformat() < a['review_date']): v = a['new_value']
            return p['overrides'].get(key, v)
        missing = [key for key in missing if val(key) is None]
        market, anchor, history = self._market(as_of)
        for s in MATERIAL:
            if s not in market: missing.append('market:' + s)
            elif (as_of - date.fromisoformat(market[s]['date'])).days > (10 if s in (*WCI, *FUEL) else 5): missing.append('stale:' + s)
            if s not in anchor: missing.append('anchor:' + s)
            elif not self.demo:
                period = [r for r in history.get(s,[]) if profile['anchor_start'] <= r['date'] <= profile['anchor_end']]
                tolerance = 10 if s in (*WCI, *FUEL) else 5
                if not period or (date.fromisoformat(period[0]['date'])-date.fromisoformat(profile['anchor_start'])).days > tolerance or (date.fromisoformat(profile['anchor_end'])-date.fromisoformat(period[-1]['date'])).days > tolerance:
                    missing.append('anchor_coverage:' + s)
        locked = {}
        for c in ('INR', 'BRL'):
            rows = [r for r in history.get('FX_USD_PER_' + c, []) if r['date'] <= '2025-12-31']
            if rows: locked[c] = rows[-1]['value']
            elif self.demo: locked[c] = market['FX_USD_PER_' + c]['value']
            else: missing.append('locked_fx_2025:' + c)
        end = month_end(add_months(as_of, 12))
        timeline = [as_of, *days(as_of + timedelta(days=1), min(end, as_of + timedelta(days=91)))]
        cursor = month_end(as_of + timedelta(days=92))
        while cursor <= end:
            timeline.append(cursor); cursor = month_end(add_months(cursor, 1))
        snap = lambda d: d if d <= as_of + timedelta(days=91) else month_end(d)
        base_p = copy.deepcopy(p)
        base_p['freight'] = {}; base_p['bunker'] = {}; base_p['fx'] = {}
        base_p['rates'] = {'zim_tranche_on': p['rates'].get('zim_tranche_on', False)}
        base, base_volumes = self._pass(as_of, end, base_p, market, anchor, locked, history, val, snap)
        stressed, volumes = self._pass(as_of, end, p, market, anchor, locked, history, val, snap)
        # Anchor swaps are ordered freight -> bunker -> rates -> FX and use DELTA_ONLY lines only.
        anchor_market = {s: {**record, 'value': anchor.get(s, record['value'])} for s, record in market.items()}
        previous, _ = self._pass(as_of, end, base_p, anchor_market, anchor, locked, history, val, snap)
        anchor_by_day = defaultdict(float); anchor_parts = {}
        for driver, series in [('freight', WCI), ('bunker', FUEL), ('rates', ('SOFR','ESTR','BB_OAS','UST_2Y','EA_2Y')), ('fx', tuple('FX_USD_PER_' + c for c in CCYS))]:
            for s in series:
                if s in market: anchor_market[s] = market[s]
            current, _ = self._pass(as_of, end, base_p, anchor_market, anchor, locked, history, val, snap)
            old = self._by_date(previous, delta_only=True); new = self._by_date(current, delta_only=True)
            diffs = {d: new.get(d, 0) - old.get(d, 0) for d in set(old) | set(new)}
            for d, amount in diffs.items(): anchor_by_day[d] += amount
            anchor_parts[driver] = round(sum(diffs.values()), 6); previous = current
        pairs = []; attribution = dict.fromkeys(('freight','bunker','rates','fx'), 0.)
        delta_by_day = defaultdict(float); gross_by_driver = defaultdict(lambda: defaultdict(float))
        for key in sorted(set(base) | set(stressed)):
            b, s = base.get(key), stressed.get(key)
            line = s or b; bv = b['amount_native_m'] if b else 0.; sv = s['amount_native_m'] if s else 0.
            bx = b['fx_rate'] if b else self._fx(line['currency'], date.fromisoformat(line['date']), base_p, market)
            sx = s['fx_rate'] if s else self._fx(line['currency'], date.fromisoformat(line['date']), p, market)
            bv, sv = round(bv,8), round(sv,8)
            base_usd, scenario_usd = round(bv * bx,8), round(sv * sx,8)
            delta = round(scenario_usd-base_usd,8)
            native_driver = round((sv - bv) * bx,8)
            if line['owner'] == 'fx': attribution['fx'] += native_driver
            else: attribution[line['owner']] += native_driver
            attribution['fx'] += delta-native_driver
            delta_by_day[line['date']] += delta
            if line['owner']=='rates':
                side='rates_cash' if line['line_code']=='INTEREST_INCOME' else 'rates_debt'
                gross_by_driver[side][line['date']]+=native_driver
            elif line['line_code'] in {'FREIGHT_REVENUE','FUEL_PURCHASE'}:
                gross_by_driver[line['owner']][line['date']] += native_driver
            if line['line_code']!='HEDGE_SETTLEMENT':
                gross_by_driver['fx'][line['date']]+=sv*(sx-bx)+(native_driver if line['owner']=='fx' else 0)
            pairs.append({**line, 'amount_native_base_m': bv, 'amount_native_scenario_m': sv,
                          'fx_rate_base': bx, 'fx_rate_scenario': sx,
                          'amount_usd_base_m': base_usd, 'amount_usd_scenario_m': scenario_usd,
                          'delta_usd_m': delta, 'label': 'ASSUMPTION', 'synthetic': True})
        # Missing source figures are omitted, and explicitly prevent complete/low-risk claims.
        ocf = val('liquidity.ocf_daily_usd_m') or 0.
        lease, capex = val('liquidity.lease_monthly_usd_m'), val('liquidity.capex_monthly_usd_m')
        roll_days = max(0, (as_of - date.fromisoformat(profile['balance_date'])).days)
        # shortcut: disclosed-to-T0 roll-forward uses OCF/capex/leases only; replace with a complete historical cash ledger when supplied.
        starting = (val('liquidity.cash_usd_m') or 0) + roll_days * (ocf - ((lease or 0) + (capex or 0)) * 12 / 365)+(val('liquidity.historical_financing_adjustment_usd_m') or 0)
        base_amount = self._by_date(base, base_only=True)
        cash_b = cash_s = starting; path_b = []; path_s = []; last = as_of
        credit = val('liquidity.credit_lines_usd_m') or 0
        investment = (val('liquidity.investments_usd_m') or 0) if val('liquidity.count_liquid_investments') else 0
        reserve = val('liquidity.reserve_usd_m') or 0
        for day in timeline:
            n = (day - last).days; fixed = 0.
            # Include each actual month-end between successive timeline points exactly once.
            for d in days(last + timedelta(days=1), day):
                if d == month_end(d): fixed -= (lease or 0) + (capex or 0)
            flow = ocf * n + fixed + base_amount.get(day.isoformat(), 0) + anchor_by_day.get(day.isoformat(), 0)
            cash_b += flow; cash_s += flow + delta_by_day.get(day.isoformat(), 0)
            path_b.append({'date': day.isoformat(), 'cash_usd_m': round(cash_b, 6), 'headroom_usd_m': round(cash_b + credit + investment - reserve, 6)})
            path_s.append({'date': day.isoformat(), 'cash_usd_m': round(cash_s, 6), 'headroom_usd_m': round(cash_s + credit + investment - reserve, 6)})
            last = day
        mb = min(path_b, key=lambda r: r['headroom_usd_m']); ms = min(path_s, key=lambda r: r['headroom_usd_m'])
        hb, hs = mb['headroom_usd_m'], ms['headroom_usd_m']
        consumed = (hb - hs) / hb if hb > 0 else None
        status = 'CRITICAL' if hb <= 0 or hs < 0 or (consumed is not None and consumed >= .5) else 'HIGH' if consumed >= .25 else 'WATCH' if consumed >= .1 else 'LOW'
        proxy = any(r.get('label') == 'PROXY' for s, r in market.items() if s in MATERIAL) or 'WCI_COMPOSITE' in market
        if status == 'LOW' and proxy: status = 'WATCH'
        if not val('freight.trade_mapping_verified'): missing.append('profile:trade_mapping_unverified')
        if missing and status in {'LOW', 'WATCH'}: status = 'INCOMPLETE'
        exceptions = []
        if hb > 0:
            for driver, amounts in gross_by_driver.items():
                cumulative = minimum = 0.; low_day = None
                for d in sorted(amounts):
                    cumulative += amounts[d]
                    if cumulative < minimum: minimum, low_day = cumulative, d
                if -minimum >= .25 * hb:
                    exceptions.append({'driver': 'rates' if driver.startswith('rates_') else driver,
                                       'side': driver, 'gross_adverse_usd_m': -minimum, 'pct_of_headroom': -minimum / hb * 100, 'date': low_day})
            rate_exceptions=[e for e in exceptions if e['driver']=='rates']
            if rate_exceptions:
                winner=max(rate_exceptions,key=lambda e:e['gross_adverse_usd_m'])
                exceptions=[e for e in exceptions if e['driver']!='rates']+[winner]
        attribution = {k: round(v, 8) for k, v in attribution.items()}
        attribution['total'] = round(sum(delta_by_day.values()), 8)
        headroom = {'baseline_min_usd_m': hb, 'baseline_min_date': mb['date'], 'scenario_min_usd_m': hs,
                    'scenario_min_date': ms['date'], 'consumed_pct': round(consumed * 100, 4) if consumed is not None else None,
                    'first_breach_date': next((r['date'] for b, s in zip(path_b, path_s) for r in [b] if min(b['headroom_usd_m'], s['headroom_usd_m']) < 0), None),
                    'shortfall_tier': 'reserve_breached' if hs < 0 else 'within_operating_cash' if ms['cash_usd_m'] >= reserve else 'credit_lines_needed'}
        flags = {'synthetic_register': True, 'proxy_based': proxy, 'uses_unconfirmed_data': any(not r.get('confirmed', True) for r in market.values()),
                 'includes_unapproved_adjustments': False, 'incomplete_data': bool(missing), 'demo': self.demo}
        historical_inputs = {s: [(r['date'],r['value']) for r in rows] for s,rows in sorted(history.items())}
        historical_inputs_hash = hashlib.sha256(json.dumps({'history':historical_inputs,'locked':locked},sort_keys=True).encode()).hexdigest()
        payload = {'as_of': as_of.isoformat(), 'profile': profile, 'scenario': p, 'adjustments': applied,
                   'market_snapshot': market, 'anchor_snapshot': anchor, 'demo': self.demo,
                   'historical_inputs_hash':historical_inputs_hash,'locked_fx_snapshot':locked}
        run_id = 'risk_' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
        margin_balance=0.; margin_min=None
        if any(h['instrument']=='swap' for h in p['hedge_tests']):
            by_date=defaultdict(float)
            for l in pairs:
                if l['line_code']=='MARGIN_CALL': by_date[l['date']]-=l['amount_usd_scenario_m']
            for row in path_s:
                margin_balance+=by_date.get(row['date'],0)
                available=.25*row['cash_usd_m']-margin_balance
                margin_min=available if margin_min is None else min(margin_min,available)
            if margin_min<0: exceptions.append({'driver':'bunker','type':'margin_eligible_shortfall','minimum_usd_m':margin_min})
        return {**payload, 'run_id': run_id, 'profile_version': profile['profile_version'],
                'profile_hash':hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest(), 'status': status, 'flags': flags,
                'missing_inputs': sorted(set(missing)), 'baseline_path': path_b, 'scenario_path': path_s,
                'attribution': attribution, 'attribution_usd_m': attribution, 'headroom': headroom,
                'summary': {**headroom, 'net_delta_usd_m': attribution['total']},
                'opportunity': consumed is not None and consumed <= -.1,
                'lines': pairs, 'volume_lines': {'baseline': base_volumes, 'scenario': volumes},
                'anchor_adjustment': {'by_driver_usd_m': anchor_parts, 'by_date': dict(anchor_by_day), 'label': 'ASSUMPTION'},
                'desk_exceptions': exceptions, 'units': 'USD millions; native line amounts in currency millions',
                'margin_test':{'applicable':margin_min is not None,'passed':margin_min is None or margin_min>=0,'minimum_usd_m':margin_min},
                'limitations': [*profile['limitations'], 'Historical roll-forward omits historical financing flows; opening liquidity is provisional.',
                                'Rates swaps and verified meeting-calendar presets are not implemented.'],
                'memo_positions': {'CNY_long_usd_m': 85, 'GBP_short_usd_m': 24, 'on_liquidity_path': False}}

    @staticmethod
    def _validate_override(key, value):
        if key in BOOL_KEYS:
            if not isinstance(value, bool): raise ValueError(f'{key} requires a boolean')
            return
        if isinstance(value, bool):
            raise ValueError(f'{key} requires a number')
        if not _finite(value): raise ValueError(f'{key} requires a finite value')
        if any(word in key for word in ('share','ratio','weight','trigger','pass_through')) and not 0 <= value <= 1: raise ValueError(f'{key} must be 0..1')
        if key == 'fx.cad_hedge_ratio' and value > .8: raise ValueError('CAD hedge cap is 80%')
        if 'days' in key and not 0 <= value <= 180: raise ValueError(f'{key} outside 0..180')
        if key == 'fx.obligation_shift_months' and (not isinstance(value,int) or not 0 <= value <= 24): raise ValueError('Obligation shift must be an integer in 0..24')
        if 'monthly_usd_m' in key and value < 0: raise ValueError('Monthly capex and lease amounts are positive outflow magnitudes')
        if value < 0 and key not in {'rates.deposit_spread_bp','rates.new_issue_premium_bp','freight.volume_growth','liquidity.historical_financing_adjustment_usd_m'}:
            raise ValueError(f'{key} must be nonnegative')

    @staticmethod
    def _by_date(lines, *, delta_only=False, base_only=False):
        totals = defaultdict(float)
        for line in lines.values():
            if delta_only and line['treatment'] != 'DELTA_ONLY': continue
            if base_only and line['treatment'] != 'BASE_AMOUNT': continue
            totals[line['date']] += line['amount_native_m'] * line['fx_rate']
        return totals

    @staticmethod
    def _fx(currency, day, p, market):
        if currency == 'USD': return 1.
        rate = market.get('FX_USD_PER_' + currency)
        if not rate: return None
        shape = p['fx'].get('shape', {})
        shape = shape.get(currency) if isinstance(shape, dict) else shape
        return rate['value'] * (1 + p['fx'].get('move_pct', {}).get(currency, 0) * factor(p, day, shape))

    def _pass(self, t0, end, p, market, anchor, locked, history, val, snap):
        lines = {}; volumes = defaultdict(float)
        def emit(owner, code, sub, day, currency, amount, treatment='DELTA_ONLY'):
            if not t0 < day <= end: return
            day = snap(day); rate = self._fx(currency, day, p, market)
            if rate is None: return
            key = (code, sub, currency, day.isoformat())
            if key not in lines: lines[key] = {'owner': owner, 'line_code': code, 'sub_id': sub, 'currency': currency,
                                             'date': day.isoformat(), 'amount_native_m': 0., 'fx_rate': rate, 'treatment': treatment}
            lines[key]['amount_native_m'] += amount
        def index(series, day, shock=0, shape=None):
            return market[series]['value'] * (1 + shock * factor(p, day, shape))
        freight = p['freight']; bunker = p['bunker']; rates = p['rates']
        def collect(owner, code, sub, trade, d, amount):
            delay = freight.get('transit_delay_days', {}).get(trade, 0) * factor(p, d, freight.get('shape'))
            lag = int(val('freight.collection_lag_days', d) or 0)
            share = val('freight.delay_affected_share', d)
            config = self.profile['trades'][trade]
            currency, n = config['invoice_currency'], val('freight.non_usd_share.' + trade,d)
            for suffix, date_lag, split in [('arr', lag + round(delay), share), ('dep', lag, 1-share)]:
                if not split: continue
                day = d + timedelta(days=date_lag)
                emit(owner, code, sub + ':' + suffix, day, 'USD', amount * split * (1-n))
                fx0 = market.get('FX_USD_PER_' + currency)
                if fx0: emit(owner, code, sub + ':' + suffix, day, currency, amount * split * n / fx0['value'])
        for trade, config in self.profile['trades'].items():
            sid = config['series']
            if sid not in market or sid not in anchor: continue
            contract = val('freight.contract_share'); linked = val('freight.index_linked_share')
            if contract + linked > 1: raise ValueError('Freight contract and index-linked shares exceed 100%')
            cohorts = []
            for month in ([5] if trade == 'ASIA_AMERICA' else range(1,13)):
                reset = date(t0.year, month, 1)
                if reset <= t0: reset = date(t0.year + 1, month, 1)
                cohorts.append({'reset': reset, 'rate': config['rate_usd_per_teu'], 'leaked': 0., 'lost': 0., 'down': False, 'up': False})
            for d in days(t0 + timedelta(days=1), end):
                shock = freight.get('rate_shock_pct', {}).get(trade, 0)
                def rho(day): return index(sid, day, shock, freight.get('shape')) / anchor[sid] - 1
                base_rate = config['rate_usd_per_teu']
                spot = base_rate * (1 + rho(d - timedelta(days=int(val('freight.pricing_lag_days', d)))))
                index_rate = base_rate * (1 + val('freight.clause_pass_through', d) * rho(d - timedelta(days=int(val('freight.clause_lag_days', d)))))
                quarter=(d.month-1)//3+1
                qvolume=val('freight.quarter_volume_teu.Q'+str(quarter))
                qstart=date(d.year,(quarter-1)*3+1,1); qend=month_end(add_months(qstart,2))
                trade_share=config['volume_h1_teu']/sum(t['volume_h1_teu'] for t in self.profile['trades'].values())
                daily=qvolume*trade_share/((qend-qstart).days+1) if qvolume is not None else config['volume_h1_teu']/181
                v = daily * (1 + val('freight.volume_growth')) * (1 + freight.get('volume_shock_pct', {}).get(trade, 0) * factor(p, d, freight.get('shape')))
                collect('freight','FREIGHT_REVENUE',f'freight:spot:{trade}', trade, d, v * (1-contract-linked) * spot / 1e6)
                collect('freight','FREIGHT_REVENUE',f'freight:index:{trade}', trade, d, v * linked * index_rate / 1e6)
                actual_volume = v * (1-contract)
                for n, cohort in enumerate(cohorts):
                    if d == cohort['reset']: cohort['rate'] = base_rate * (1 + val('freight.contract_repricing_share', d) * rho(d))
                    gap = spot / cohort['rate'] - 1
                    intact = 1 - cohort['leaked'] - cohort['lost']
                    if gap < -val('freight.leakage_trigger_down', d) and not cohort['down']:
                        cohort['leaked'] += intact * val('freight.leakage_share_down', d)
                        cohort['lost'] += intact * val('freight.leakage_loss_share', d); cohort['down'] = True
                    if gap > val('freight.leakage_trigger_up', d) and not cohort['up']:
                        cohort['leaked'] += intact * val('freight.leakage_share_up', d); cohort['up'] = True
                    intact = 1 - cohort['leaked'] - cohort['lost']
                    cv = v * contract / len(cohorts)
                    actual_volume += cv * (1-cohort['lost'])
                    collect('freight','FREIGHT_REVENUE',f'freight:contract:{trade}:{n}',trade,d,cv * (intact * cohort['rate'] + cohort['leaked'] * spot) / 1e6)
                volumes[trade + ':' + d.strftime('%Y-%m')] += actual_volume
        if all(s in market and s in anchor for s in FUEL):
            def blended(day, anchored=False):
                scrubber = val('bunker.scrubber_share', day); sw = val('bunker.singapore_weight', day)
                total = 0.
                for hub, weight in [('SIN', sw), ('RTM', 1-sw)]:
                    for grade, share in [('VLSFO', 1-scrubber), ('HSFO', scrubber)]:
                        sid = grade + '_' + hub
                        price = anchor[sid] if anchored else index(sid, day, bunker.get(grade.lower() + '_shock_pct',0), bunker.get('shape'))
                        total += weight * share * price
                return total
            monthly_prices = {}
            def month_prices(day):
                month = day.replace(day=1)
                if month not in monthly_prices:
                    dates = list(days(month,month_end(month)))
                    prices = {s: sum(index(s,d,bunker.get(s.split('_')[0].lower()+'_shock_pct',0),bunker.get('shape')) for d in dates)/len(dates) for s in FUEL}
                    f = sum(factor(p,d,bunker.get('shape')) for d in dates)/len(dates)
                    monthly_prices[month] = prices, f
                return monthly_prices[month]
            for d in days(t0 + timedelta(days=1), end):
                sw = val('bunker.singapore_weight', d); scrubber = val('bunker.scrubber_share', d)
                prices, fm = month_prices(d)
                q = val('bunker.monthly_consumption_t') / calendar.monthrange(d.year,d.month)[1]
                q *= 1 + bunker.get('consumption_uplift_pct',0) * bunker.get('uplift_affected_share',1) * fm
                for hub, w in [('SIN', sw),('RTM',1-sw)]:
                    for grade, share in [('VLSFO',1-scrubber),('HSFO',scrubber)]:
                        price = prices[grade+'_'+hub]
                        emit('bunker','FUEL_PURCHASE',f'bunker:purchase:{hub}:{grade}',d+timedelta(days=int(val('bunker.supplier_payment_lag_days',d))),'USD',-q*w*share*price/1e6)
                reference_day = add_months(d, -int(val('bunker.mfr_lag_months',d)))
                ref_prices, _ = month_prices(reference_day)
                ref = sw*((1-scrubber)*ref_prices['VLSFO_SIN']+scrubber*ref_prices['HSFO_SIN'])+(1-sw)*((1-scrubber)*ref_prices['VLSFO_RTM']+scrubber*ref_prices['HSFO_RTM'])
                anchor_price = blended(d,True)
                for trade in TRADES:
                    key = trade+':'+d.strftime('%Y-%m')
                    if key not in volumes: continue
                    # Monthly freight TEU is the shared orchestrator link, not an independently regenerated estimate.
                    month_start = max(t0+timedelta(days=1),d.replace(day=1)); ndays = (min(end,month_end(d))-month_start).days+1
                    teu = volumes[key] / ndays
                    recovery = val('bunker.mfr_share',d)*(ref-anchor_price)*val('bunker.fuel_per_teu')*teu/1e6
                    collect('bunker','MFR_RECOVERY','bunker:mfr:'+trade,trade,d,recovery)
            for hnum,hedge in enumerate(p['hedge_tests']):
                start=max(t0+timedelta(days=1),date.fromisoformat(hedge['start_date']))
                finish=min(end,add_months(date.fromisoformat(hedge['start_date']),hedge['tenor_months']))
                if finish<start: continue
                target=hedge['target']; share=1 if target=='fuel_all' else (1-val('bunker.scrubber_share') if target=='vlsfo' else val('bunker.scrubber_share'))
                quantity=val('bunker.monthly_consumption_t')*hedge['coverage_pct']*share
                sw=val('bunker.singapore_weight'); scrubber=val('bunker.scrubber_share')
                def target_price(prices):
                    if target=='fuel_all': return sw*((1-scrubber)*prices['VLSFO_SIN']+scrubber*prices['HSFO_SIN'])+(1-sw)*((1-scrubber)*prices['VLSFO_RTM']+scrubber*prices['HSFO_RTM'])
                    grade=target.upper(); return sw*prices[grade+'_SIN']+(1-sw)*prices[grade+'_RTM']
                strike=target_price({s:market[s]['value'] for s in FUEL})
                hedge_days=list(days(start,finish)); remaining=sum(quantity/calendar.monthrange(d.year,d.month)[1] for d in hedge_days)
                posted=0.; monthly_settlements=defaultdict(float)
                for d in hedge_days:
                    q=quantity/calendar.monthrange(d.year,d.month)[1]
                    prices,_=month_prices(d); price=target_price(prices)
                    settlement=(price-strike)*q/1e6
                    if hedge['instrument']=='fixed_price_supply':
                        emit('bunker','HEDGE_SETTLEMENT',f'bunker:fixed:{hnum}',d+timedelta(days=int(val('bunker.supplier_payment_lag_days',d))),'USD',settlement,'BASE_AMOUNT')
                    else:
                        monthly_settlements[min(month_end(d),finish)]+=settlement
                        remaining=max(0,remaining-q)
                        current_prices={s:index(s,d,bunker.get(s.split('_')[0].lower()+'_shock_pct',0),bunker.get('shape')) for s in FUEL}
                        required=max(0,(strike-target_price(current_prices))*remaining/1e6)
                        emit('bunker','MARGIN_CALL',f'bunker:margin:{hnum}',d,'USD',posted-required,'BASE_AMOUNT')
                        posted=required
                for d,amount in monthly_settlements.items(): emit('bunker','HEDGE_SETTLEMENT',f'bunker:swap:{hnum}',d,'USD',amount,'BASE_AMOUNT')
        interest_treatment = 'DELTA_ONLY' if val('liquidity.interest_in_ocf') else 'BASE_AMOUNT'
        fx0 = market.get('FX_USD_PER_EUR',{}).get('value')
        closing = val('rates.closing_usd_per_eur_2025')
        float_total = val('rates.floating_debt_eur_m') * closing if closing else None
        total_debt = val('rates.financial_debt_usd_m')
        lease_current = val('rates.current_leases_usd_m')
        maturity = max(0,val('rates.current_debt_and_leases_usd_m')-lease_current)/12 if lease_current is not None else None
        # Pro-rata debt retirements on one shared monthly maturity schedule.
        maturity_dates = []; m = month_end(t0+timedelta(days=1))
        while m <= end: maturity_dates.append(m); m=month_end(add_months(m,1))
        def benchmark(c, day):
            row=market.get('SOFR' if c=='USD' else 'ESTR')
            if row is None: return None
            steps=sum(step['step_bp'] for step in rates.get('rate_path',{}).get(c,[]) if step['date']<=day.isoformat())
            return max(0,row['value']+steps/100)
        if float_total and fx0:
            for c, weight in [('USD',val('rates.usd_floating_share')),('EUR',1-val('rates.usd_floating_share'))]:
                if benchmark(c,t0) is None: continue
                conversion=1 if c=='USD' else fx0
                for n in range(3):
                    payment=add_months(t0,n+1); reset=add_months(payment,-3)
                    while payment<=end:
                        notional=float_total*weight/3/conversion
                        spread=val('rates.usd_spread_bp' if c=='USD' else 'rates.eur_spread_bp')/100
                        rate=(market['SOFR' if c=='USD' else 'ESTR']['value'] if payment==add_months(t0,n+1) else benchmark(c,reset))+spread
                        rate=max(0,rate-spread)+spread
                        balance_days=old_debt_balance_days(reset,payment,maturity_dates,maturity,total_debt)
                        emit('rates','INTEREST_EXPENSE',f'rates:float:{c}:{n}',payment,c,-notional*rate/100*balance_days/360,interest_treatment)
                        reset=payment;payment=add_months(payment,3)
        if fx0 and closing:
            for m in maturity_dates:
                emit('rates','INTEREST_EXPENSE','rates:lease',m,'USD',-val('rates.lease_interest_eur_m')*closing/12,
                     'DELTA_ONLY' if val('liquidity.lease_includes_interest') else interest_treatment)
                if total_debt and float_total is not None:
                    fixed=max(0,total_debt-float_total); eur_bond=min(fixed,300*fx0)
                    reset=month_end(add_months(m,-1))
                    remaining=old_debt_balance_days(reset,m,maturity_dates,maturity,total_debt)/(m-reset).days
                    usd_fixed=(fixed-eur_bond)*remaining
                    emit('rates','INTEREST_EXPENSE','rates:fixed:USD',m,'USD',-usd_fixed*val('rates.fixed_rate_pct')/100/12,interest_treatment)
                    if m.month==10:
                        coupon=date(m.year,10,15); reset=date(m.year-1,10,15)
                        remaining=old_debt_balance_days(reset,coupon,maturity_dates,maturity,total_debt)/(coupon-reset).days
                        emit('rates','INTEREST_EXPENSE','rates:bond:EUR',coupon,'EUR',-(eur_bond/fx0)*.025*remaining,interest_treatment)
        for c, weight in [('USD',val('rates.deposit_usd_share')),('EUR',1-val('rates.deposit_usd_share'))]:
            if benchmark(c,t0) is None or (c=='EUR' and not fx0): continue
            balance=val('liquidity.cash_usd_m')*val('rates.interest_bearing_share')*weight/(1 if c=='USD' else fx0)
            for n in range(3):
                payment=add_months(t0,n+1); reset=add_months(payment,-3)
                while payment<=end:
                    b=market['SOFR' if c=='USD' else 'ESTR']['value'] if payment==add_months(t0,n+1) else benchmark(c,reset)
                    rate=max(0,max(0,b)+val('rates.deposit_spread_bp')/100)
                    emit('rates','INTEREST_INCOME',f'rates:deposit:{c}:{n}',payment,c,balance/3*rate/100*(payment-reset).days/360,interest_treatment)
                    reset=payment;payment=add_months(payment,3)
        if maturity is not None and fx0:
            for m in maturity_dates:
                eur_amount=maturity*.3/fx0
                emit('rates','DEBT_PRINCIPAL','rates:principal:USD',m,'USD',-maturity*.7,'BASE_AMOUNT')
                emit('fx','DEBT_PRINCIPAL','fx:principal:EUR',m,'EUR',-eur_amount,'BASE_AMOUNT')
                self._hedge(emit,'fx:principal:EUR',m,'EUR',eur_amount,val('fx.eur_debt_hedge_ratio'),fx0,p,market)
                if rates.get('refinancing_mode','rollover')=='rollover':
                    emit('rates','DEBT_DRAWDOWN','rates:drawdown:'+m.isoformat(),m,'USD',maturity,'BASE_AMOUNT')
                    if benchmark('USD',m) is not None and 'BB_OAS' in market:
                        reset=m;payment=add_months(m,3)
                        while payment<=end:
                            new_rate=benchmark('USD',reset)+market['BB_OAS']['value']+(val('rates.new_issue_premium_bp')+rates.get('credit_spread_change_bp',0))/100
                            emit('rates','REFINANCING_COST','rates:refi:'+m.isoformat(),payment,'USD',-maturity*new_rate/100*(payment-reset).days/360,interest_treatment)
                            reset=payment;payment=add_months(payment,3)
        if rates.get('zim_tranche_on',False) and benchmark('USD',t0) is not None:
            reset=max(t0,date(2027,1,1));payment=add_months(reset,3)
            while payment<=end:
                rate=benchmark('USD',reset)+2.25+(rates.get('credit_spread_change_bp',0)/100 if date(2027,1,1)>t0 else 0)
                emit('rates','INTEREST_EXPENSE','rates:zim',payment,'USD',-4200*rate/100*(payment-reset).days/360,interest_treatment)
                reset=payment;payment=add_months(payment,3)
        for d in days(t0+timedelta(days=1),end):
            if d.weekday()==4:
                for c, amount, ratio in [('CAD',val('fx.cad_operating_weekly_m'),val('fx.cad_hedge_ratio',d)),('EUR',val('fx.eur_operating_quarterly_m')/13,val('fx.eur_operating_hedge_ratio',d))]:
                    emit('fx','FX_OPERATING_COST','fx:operating:'+c,d,c,-amount)
                    start=d-timedelta(days=91)
                    if start<=t0:
                        h=[r for r in history.get('FX_USD_PER_'+c,[]) if r['date']<=start.isoformat()]
                        strike=h[-1]['value'] if h else market.get('FX_USD_PER_'+c,{}).get('value')
                    else: strike=self._fx(c,start,p,market)
                    if strike is not None: self._hedge(emit,'fx:operating:'+c,d,c,amount,ratio,strike,p,market)
        dividend=val('fx.dividend_eur_m')
        if dividend is not None and fx0:
            pay=date(t0.year if t0<date(t0.year,5,10) else t0.year+1,5,10)
            emit('fx','DIVIDEND','fx:dividend',pay,'EUR',-dividend,'BASE_AMOUNT')
            self._hedge(emit,'fx:dividend',pay,'EUR',dividend,val('fx.eur_dividend_hedge_ratio'),fx0,p,market)
        if val('fx.inr_brl_obligations_on'):
            for c, total in [('INR',1400),('BRL',256)]:
                for m in maturity_dates:
                    pay=month_end(add_months(m,int(val('fx.obligation_shift_months',m))))
                    emit('fx','FX_TRANSACTION','fx:transaction:'+c,pay,c,-total/12,'DELTA_ONLY' if val('fx.transactions_in_capex') else 'BASE_AMOUNT')
                    if c in locked: self._hedge(emit,'fx:transaction:'+c,pay,c,total/12,val('fx.inr_brl_hedge_ratio',m),locked[c],p,market)
        return lines, dict(volumes)

    def _hedge(self, emit, sub, day, currency, notional, ratio, strike, p, market):
        spot=self._fx(currency,day,p,market)
        if spot is not None and ratio:
            emit('fx','HEDGE_SETTLEMENT','hedge:'+sub,day,'USD',notional*ratio*(spot-strike),'BASE_AMOUNT')
