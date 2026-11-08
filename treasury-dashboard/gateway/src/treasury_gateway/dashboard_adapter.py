"""Present the combined cash ledger in the original Treasury dashboard contract.

No legacy sensitivity/fixture amounts survive this adapter. Percent sliders are
displayed in percent numbers; the saved cash-engine scenario remains the authority.
"""
from __future__ import annotations

from collections import defaultdict
import copy
from datetime import date, timedelta
import math

from .risk_engine import add_months, month_end

ROWS = ('fx_eur','fx_gbp','fx_cny','fx_cad','interest','bunker','freight')
NAMES = {'fx_eur':'EUR and other FX','fx_gbp':'GBP FX','fx_cny':'CNY FX','fx_cad':'CAD FX',
         'interest':'Interest and refinancing','bunker':'Bunker fuel and MFR','freight':'Freight collections'}
METRICS = {'fx_eur':('FX_USD_PER_EUR','EUR / USD','USD/EUR'),
           'fx_gbp':('FX_USD_PER_GBP','GBP / USD','USD/GBP'),
           'fx_cny':('FX_USD_PER_CNY','CNY / USD','USD/CNY'),
           'fx_cad':('FX_USD_PER_CAD','CAD / USD','USD/CAD'),
           'interest':('ESTR','Euro short-term rate','%'),
           'bunker':('VLSFO_RTM','VLSFO Rotterdam','USD/t'),
           'freight':('WCI_COMPOSITE','WCI composite','USD/FEU')}
DRIVER = {'interest':'rates','bunker':'bunker','freight':'freight',
          'fx_eur':'fx','fx_gbp':'fx','fx_cny':'fx','fx_cad':'fx'}


def _number(v):
    return isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v)


def _currency_for_hedge(line):
    sub = line.get('sub_id','')
    for c in ('EUR','CAD','GBP','CNY','INR','BRL'):
        if sub.endswith(':'+c): return c
    return 'EUR' if 'dividend' in sub else 'OTHER'


def _weekly_move(market,sid,as_of):
    quotes={r['series_id']:r for r in market.get('series',[]) if isinstance(r,dict) and 'series_id' in r}
    quotes.update(market.get('market_snapshot',{}))
    quote=quotes.get(sid,{})
    latest=quote.get('value')
    comparison_day=date.fromisoformat(quote['date']) if quote.get('date') else as_of
    points=market.get('history',{}).get(sid,[])
    if isinstance(points,dict): points=points.get('points',[])
    tolerance=10 if sid.startswith(('WCI','VLSFO','HSFO')) else 5
    points=sorted((r for r in points if r.get('date') and (comparison_day-timedelta(days=7+tolerance)).isoformat()<=r['date']<=(comparison_day-timedelta(days=7)).isoformat() and _number(r.get('value'))),key=lambda r:r['date'])
    if not _number(latest) or not points: return None
    previous=points[-1]['value']
    change=latest-previous
    if sid=='ESTR': return change*100
    if sid=='VLSFO_RTM': return change
    return change/previous*100 if previous else None


# Working-capital levers (roadmap phase 3), not cash-impact rows. collection_delay = collections slower by N days on
# every trade, sent as the spec's freight.transit_delay_days scenario shock (6.5, D40): a real scenario delta that
# moves cash to later periods. The spec field allows slowdowns only; its policy range is 0..60 days (thresholds.yaml).
WC_SHOCKS={'collection_delay':(0,60)}


def observed_moves(market:dict,as_of:date)->dict:
    """Each slider's market move over the last week (FX %, ESTR bp, VLSFO USD/t, WCI %); a missing comparison is 0."""
    out={}
    for key in ROWS:
        sid='FX_USD_PER_'+key[3:].upper() if key.startswith('fx_') else 'ESTR' if key=='interest' else 'VLSFO_RTM' if key=='bunker' else 'WCI_COMPOSITE'
        out[key]=_weekly_move(market,sid,as_of) or 0.
    return out


def slider_scenario(shocks:dict,as_of:date,market:dict,base_scenario:dict|None=None)->dict:
    """Merge explicitly moved original-page sliders into an optional detailed news forecast. A moved slider replaces
    the forecast's value for that market. Without a forecast, every unmoved slider takes last week's market move once
    more (today's prices are already in the baseline)."""
    if not isinstance(shocks,dict) or set(shocks)-set(ROWS)-set(WC_SHOCKS): raise ValueError('Unknown what-if slider')
    wc={k:shocks[k] for k in WC_SHOCKS if k in shocks}
    shocks={k:v for k,v in shocks.items() if k not in WC_SHOCKS}
    p=copy.deepcopy(base_scenario or {})
    for key,v in wc.items():
        lo,hi=WC_SHOCKS[key]
        if not _number(v) or not lo<=v<=hi: raise ValueError(f'{key} must be a number of days in {lo}..{hi}')
        if v:
            p.setdefault('freight',{})['transit_delay_days']={t:int(round(v)) for t in ('ASIA_EUROPE','ASIA_AMERICA','EUROPE_AMERICA','AFRICA_INTRA')}
            p['source']='custom'; p.setdefault('name','Collections slower')
    quotes={r['series_id']:r for r in market.get('series',[]) if isinstance(r,dict) and 'series_id' in r}
    quotes.update(market.get('market_snapshot',{}))
    effective=observed_moves(market,as_of) if base_scenario is None else {}
    effective.update(shocks)
    for key,v in effective.items():
        lo,hi=(-20,20) if key.startswith('fx_') else (-200,200) if key=='interest' else (-300,300) if key=='bunker' else (-50,50)
        if not _number(v) or (key in shocks and not lo<=v<=hi): raise ValueError(f'{key} must be a finite number in {lo}..{hi}')
        if key.startswith('fx_'):
            p.setdefault('fx',{}).setdefault('move_pct',{})[key[3:].upper()]=v/100
        elif key=='interest':
            paths=p.setdefault('rates',{}).setdefault('rate_path',{})
            for c in ('USD','EUR'): paths[c]=[{'date':as_of.isoformat(),'step_bp':v}]
        elif key=='bunker':
            if v==0:
                p.setdefault('bunker',{}).update(vlsfo_shock_pct=0,hsfo_shock_pct=0)
                continue
            for grade in ('VLSFO','HSFO'):
                price=quotes.get(grade+'_RTM',{}).get('value')
                if not _number(price) or price<=0: raise ValueError(f'{grade}_RTM price is required for an absolute fuel-price shock')
                if price+v<=0: raise ValueError('Fuel-price shock would create a non-positive price')
                p.setdefault('bunker',{})[grade.lower()+'_shock_pct']=v/price
        else:
            p.setdefault('freight',{})['rate_shock_pct']={t:v/100 for t in ('ASIA_EUROPE','ASIA_AMERICA','EUROPE_AMERICA','AFRICA_INTRA')}
    if effective:
        p['source']='custom';p.setdefault('name','Cash what-if' if shocks else "Last week's moves repeated")
    return p


ROW_CURRENCIES={'fx_eur':('EUR','INR','BRL'),'fx_gbp':('GBP',),'fx_cny':('CNY',),'fx_cad':('CAD',)}


def memo_for(run,rid,convert):
    """Translation memo for an FX row (hlag_engine runs only): the engine's balance-sheet revaluation of the row's
    memo positions, in EUR for display. memo_only when the row's currencies have memo positions but no cash lines in
    the run, so the row's cash impact is structurally zero (e.g. GBP, D19)."""
    memo=run.get('translation_memo')
    if not memo: return None
    currencies=ROW_CURRENCIES[rid]
    held=[c for c in currencies if c in memo.get('positions_usd_m',{})]
    if not held: return None
    cash=set()
    for line in run.get('lines',[]):
        c=line['currency']
        cash.add(_currency_for_hedge(line) if line['owner']=='fx' and c=='USD' else c)
    reval=sum(memo.get('revaluation_usd_m',{}).get(c,0.) for c in held)
    return {'currencies':held,'memo_only':not any(c in cash for c in currencies),
            'position_usd_m':{c:memo['positions_usd_m'][c] for c in held},
            'revaluation_usd_m':reval,'revaluation_eur_m':convert(reval),'horizon_end':memo.get('horizon_end'),
            'cash':False,'on_liquidity_path':False,'label':memo.get('label','DERIVED'),
            'note':'Balance-sheet translation memo: position x scenario FX move; not a cash flow and not on the liquidity path (D19).'}


def cash_t0(inputs,convert):
    """Engine cash at T0, rolled forward from the last disclosure (hlag_engine runs only), with the bridge to
    headroom at T0 = cash + credit lines [+ special fund] - reserve."""
    if not inputs: return None
    special=inputs['special_fund_usd_m'] if inputs.get('count_special_fund') else 0.
    headroom=inputs['cash_t0_usd_m']+inputs['credit_lines_usd_m']+special-inputs['reserve_usd_m']
    return {'usd_m':inputs['cash_t0_usd_m'],'eur_m':convert(inputs['cash_t0_usd_m']),
            'credit_lines_eur_m':convert(inputs['credit_lines_usd_m']),'special_fund_eur_m':convert(special),
            'reserve_eur_m':convert(inputs['reserve_usd_m']),'headroom_t0_eur_m':convert(headroom),
            'roll_forward':inputs.get('roll_forward',{}),'label':'DERIVED'}


def direct_cash(run,as_of,cutoff,usd,convert):
    """Direct cash flows of the run (hlag_engine 'direct' owner): the dated amounts the scenario stated, within the
    selected horizon. They are on the liquidity path and in the totals, but are not a market-driver row: no market
    move explains them, so they are listed with their stated label, rationale and source Events."""
    flows=[{**f,'amount_eur_m':convert(f['amount_usd_m'])}
           for f in ((run.get('engine_params') or {}).get('direct') or {}).get('cash_flows',[])
           if as_of.isoformat()<f['date']<=cutoff]
    return {'impact_usd_m':usd,'impact_eur_m':convert(usd) if flows else 0.,'flows':flows,'provenance':'ASSUMPTION',
            'note':'Dated cash amounts stated directly in the scenario (for a news scenario: inferred by the forecast model, '
                   'not reported figures). They reach the liquidity path in full; market-driver rows exclude them.'}


def row_of_line(line)->str:
    """The card row whose FX part a line's currency belongs to (fx_eur takes EUR, INR, BRL and the rest)."""
    currency=line['currency']
    if line['owner']=='fx' and currency=='USD': currency=_currency_for_hedge(line)
    return {'GBP':'fx_gbp','CNY':'fx_cny','CAD':'fx_cad'}.get(currency,'fx_eur')


def _native_change(line)->float:
    return (line['amount_native_scenario_m']-line['amount_native_base_m'])*line['fx_rate_base']


def row_amounts(lines)->dict:
    """USD m per card row from paired lines (already cut to the horizon). Driver rows take the native change at
    base FX; the FX rows take the rest of each line's change by currency, and the full change of FX-owned lines.
    Direct lines (owner direct, USD) are not a card row: they are reported on their own (cash_impact.direct)."""
    out=dict.fromkeys(ROWS,0.)
    for line in lines:
        if line['owner']=='direct': continue
        native=_native_change(line)
        delta=line['delta_usd_m']
        if line['owner']=='fx':
            out[row_of_line(line)]+=delta
        else:
            out[ROW_OF_DRIVER[line['owner']]]+=native
            out[row_of_line(line)]+=delta-native
    return out


def horizon_cutoff(as_of:date,months:int)->str:
    return month_end(add_months(as_of,months)).isoformat()


ROW_OF_DRIVER={'freight':'freight','bunker':'bunker','rates':'interest'}


def compose_cash_dashboard(template:dict,run:dict,market:dict,months:int,shocks:dict,as_of:date)->dict:
    """Adapt one immutable run, with optional real history in market['history'][series_id]."""
    if months not in {3,6,12}: raise ValueError('horizon_months must be 3, 6 or 12')
    if run.get('as_of') != as_of.isoformat(): raise ValueError('Run and dashboard as_of must match')
    cutoff=horizon_cutoff(as_of,months)
    n_days=(date.fromisoformat(cutoff)-as_of).days
    profile=run.get('profile',{}); values=profile.get('values',{})
    def value(key): return values.get(key,{}).get('value')
    snapshots={r['series_id']:r for r in market.get('series',[]) if isinstance(r,dict) and 'series_id' in r}
    snapshots.update(run.get('market_snapshot',{}))
    eur=snapshots.get('FX_USD_PER_EUR',{}).get('value')
    eur=eur if _number(eur) and eur>0 else None
    convert=lambda amount: amount/eur if amount is not None and eur else None
    lines=[l for l in run.get('lines',[]) if as_of.isoformat()<l['date']<=cutoff]
    base=[r for r in run.get('baseline_path',[]) if as_of.isoformat()<r['date']<=cutoff]
    scenario=[r for r in run.get('scenario_path',[]) if as_of.isoformat()<r['date']<=cutoff]
    baseline_min=min(base,key=lambda r:r['headroom_usd_m']) if base else None
    scenario_min=min(scenario,key=lambda r:r['headroom_usd_m']) if scenario else None
    hb=baseline_min['headroom_usd_m'] if baseline_min else None
    hs=scenario_min['headroom_usd_m'] if scenario_min else None
    consumed=(hb-hs)/hb*100 if hb is not None and hb>0 and hs is not None else None
    amounts=row_amounts(lines)
    # direct: USD lines, so no FX part; they are their own entry, not a card row
    direct_usd=sum(_native_change(l) for l in lines if l['owner']=='direct')
    attribution={'freight':amounts['freight'],'bunker':amounts['bunker'],'rates':amounts['interest'],'direct':direct_usd}
    fx_total=sum(amounts[r] for r in ROWS if r.startswith('fx_'))
    total=sum(l['delta_usd_m'] for l in lines)
    missing=run.get('missing_inputs',[])
    def affected(rid):
        driver=DRIVER[rid]
        if not eur: return True
        currencies={'fx_eur':{'EUR','INR','BRL'},'fx_gbp':{'GBP'},'fx_cny':{'CNY'},'fx_cad':{'CAD'}}
        for key in missing:
            if driver=='fx':
                if key.startswith('fx.'): return True
                if any(('FX_USD_PER_'+c in key or 'locked_fx_2025:'+c in key) for c in currencies[rid]): return True
            else:
                if key.startswith(driver+'.') or key.startswith('profile:'+driver): return True
                series=('WCI_','FX_USD_PER_EUR','FX_USD_PER_CNY') if driver=='freight' else ('VLSFO_','HSFO_','FX_USD_PER_EUR','FX_USD_PER_CNY') if driver=='bunker' else ('SOFR','ESTR','BB_OAS','FX_USD_PER_EUR')
                if any(s in key for s in series): return True
        return False
    flags={**run.get('flags',{})}
    flags['manual_inputs']=any(r.get('label')=='ASSUMPTION' for r in snapshots.values())
    incomplete=bool(flags.get('incomplete_data'))
    status='CRITICAL' if (hb is not None and hb<=0) or (hs is not None and hs<0) or (consumed is not None and consumed>=50) else 'HIGH' if consumed is not None and consumed>=25 else 'WATCH' if consumed is not None and consumed>=10 else 'LOW'
    if flags.get('proxy_based') and status=='LOW': status='WATCH'
    if incomplete and status in {'LOW','WATCH'}: status='INCOMPLETE'
    memo_info={rid:memo_for(run,rid,convert) for rid in ROWS if rid.startswith('fx_')}
    rows=[]
    for rid in ROWS:
        usd=None if affected(rid) else amounts[rid]
        impact=convert(usd)
        notes=['Computed from paired native cash lines; all-in freight and synthetic exposures remain model assumptions.']
        if rid=='fx_eur': notes.append('This row includes EUR, INR, BRL and residual FX; other displayed currencies are separate, with no double counting.')
        if affected(rid): notes.append('Required material inputs are missing or stale; no zero impact is asserted.')
        rows.append({'id':rid,'module':'fx' if rid.startswith('fx_') else rid,'label':NAMES[rid],
                     'scenario':run.get('scenario',{}).get('name','Cash scenario'), 'impact_eur_m':impact,
                     'impact_usd_m':usd,'cash':True,'impact_type':'dated_cash_flow_delta',
                     'provenance':'UNAVAILABLE' if usd is None else 'ASSUMPTION',
                     'headroom_at_risk_pct':abs(min(usd,0))/hb*100 if usd is not None and hb is not None and hb>0 else None,
                     'status':'INCOMPLETE' if usd is None else status,'worst_case_status':status,
                     'rule_id':'cash_engine:'+DRIVER[rid],'reliability':'incomplete' if usd is None else 'synthetic_register',
                     'horizon_days':n_days,'warnings':notes,
                     'memo_only':bool((memo_info.get(rid) or {}).get('memo_only')),'memo':memo_info.get(rid)})
    complete=all(r['impact_eur_m'] is not None for r in rows)
    direct=direct_cash(run,as_of,cutoff,attribution['direct'],convert)
    direct_eur=direct['impact_eur_m'] or 0.
    net=sum(r['impact_eur_m'] for r in rows)+direct_eur if complete else None
    adverse=sum(min(r['impact_eur_m'],0) for r in rows)+min(direct_eur,0.) if complete else None
    history=market.get('history',{})
    metrics=[]; observed={}
    for rid,(sid,name,unit) in METRICS.items():
        record=snapshots.get(sid,{})
        points=history.get(sid,[])
        if isinstance(points,dict): points=points.get('points',[])
        points=sorted(({'date':r['date'],'value':r['value']} for r in points
                       if r.get('date') and r['date']<=as_of.isoformat() and _number(r.get('value'))),key=lambda r:r['date'])
        latest=record.get('value'); latest_date=record.get('date')
        comparison_day=date.fromisoformat(latest_date) if latest_date else as_of
        prior=next((r for r in reversed(points) if r['date']<=(comparison_day-timedelta(days=7)).isoformat()),None)
        tolerance=10 if sid.startswith(('WCI','VLSFO','HSFO')) else 5
        if prior and prior['date']<(comparison_day-timedelta(days=7+tolerance)).isoformat(): prior=None
        change=(latest-prior['value']) if _number(latest) and prior else None
        pct=change/prior['value']*100 if change is not None and prior['value'] else None
        if rid=='interest' and change is not None: change*=100
        observed[rid]=change if rid in {'interest','bunker'} else pct
        age=(as_of-date.fromisoformat(latest_date)).days if latest_date else None
        freshness='MISSING' if latest is None else 'STALE' if age is not None and age>(10 if sid.startswith(('WCI','VLSFO','HSFO')) else 5) else 'FRESH'
        impact=next(r['impact_eur_m'] for r in rows if r['id']==rid)
        metrics.append({'id':rid,'module':'fx' if rid.startswith('fx_') else rid,'series_id':sid,'name':name,'unit':unit,
                        'value':latest,'date':latest_date,'change':change,'change_unit':'bps' if rid=='interest' else unit,
                        'change_pct':pct,'comparison':'one_week','freshness':freshness,'source':record.get('source','unavailable'),
                        'source_kind':'manual_assumption' if record.get('label')=='ASSUMPTION' else 'shared_feed',
                        'provenance':record.get('label','UNAVAILABLE'),
                        'sparkline':[r for r in points if r['date']>=(as_of-timedelta(days=92)).isoformat()],
                        'tone':None if impact is None else 'adverse' if impact<0 else 'favourable' if impact>0 else 'neutral',
                        'unusual':{'available':False,'text':'No volatility estimate is asserted by this adapter.'}})
    specs={s['id']:s for s in template.get('scenario',{}).get('sliders',[]) if s.get('id') in ROWS}
    sliders=[]; params=run.get('scenario',{})
    for rid in ROWS:
        defaults={'min':-20.,'max':20.,'step':.5,'unit':'%'} if rid.startswith('fx_') else {'min':-200.,'max':200.,'step':5.,'unit':'bps'} if rid=='interest' else {'min':-300.,'max':300.,'step':10.,'unit':'USD/t'} if rid=='bunker' else {'min':-50.,'max':50.,'step':1.,'unit':'%'}
        spec={**defaults,**{k:v for k,v in specs.get(rid,{}).items() if k in ('min','max','step')}}
        if rid.startswith('fx_'): selected=params.get('fx',{}).get('move_pct',{}).get(rid[3:].upper(),0)*100
        elif rid=='interest': selected=sum(s['step_bp'] for s in params.get('rates',{}).get('rate_path',{}).get('EUR',[]) if s['date']<=cutoff)
        elif rid=='bunker': selected=params.get('bunker',{}).get('vlsfo_shock_pct',0)*(snapshots.get('VLSFO_RTM',{}).get('value') or 0)
        else:
            trades=profile.get('trades',{})
            weights=sum(t['volume_h1_teu'] for t in trades.values())
            selected=sum(t['volume_h1_teu']*params.get('freight',{}).get('rate_shock_pct',{}).get(k,0) for k,t in trades.items())/weights*100 if weights else 0
        selected = shocks.get(rid, selected)
        sliders.append({'id':rid,'label':NAMES[rid] if rid!='interest' else 'USD and EUR benchmarks',**spec,
                        'observed':observed.get(rid) if rid in observed else _weekly_move({**market,'market_snapshot':snapshots},'FX_USD_PER_CAD',as_of),
                        'value':selected,'min':min(spec['min'],selected),'max':max(spec['max'],selected),
                        'memo_only':bool((memo_info.get(rid) or {}).get('memo_only')),'memo':memo_info.get(rid)})
    cash=value('liquidity.cash_usd_m'); credit=value('liquidity.credit_lines_usd_m'); investments=value('liquidity.investments_usd_m')
    reserve=value('liquidity.reserve_usd_m'); counted=value('liquidity.count_liquid_investments')
    reported=sum(v for v in (cash,credit,investments) if v is not None)
    operating=(cash or 0)+(credit or 0)+((investments or 0) if counted else 0)
    scenario_liquidity=(scenario_min['cash_usd_m']+(credit or 0)+((investments or 0) if counted else 0)) if scenario_min else None
    warnings=[*run.get('missing_inputs',[]),*run.get('limitations',[])]
    debt_amounts=defaultdict(lambda:[0.,0.])
    for l in lines:
        if l['line_code']=='DEBT_PRINCIPAL': debt_amounts[l['date'][:4]][0]+=abs(l['amount_usd_base_m'])
    cursor=month_end(as_of+timedelta(days=1)); lease=value('liquidity.lease_monthly_usd_m')
    if lease is not None:
        while cursor.isoformat()<=cutoff:
            debt_amounts[str(cursor.year)][1]+=lease; cursor=month_end(add_months(cursor,1))
    debt_values={y:[convert(x) for x in amounts] for y,amounts in sorted(debt_amounts.items())} if eur else {}
    debt={'available':bool(debt_values),'unit':'EUR m','categories':['Financial principal','Lease/concession principal run-rate'],
          'years':list(debt_values),'values':debt_values,'totals':{y:sum(v) for y,v in debt_values.items()},
          'provenance':'ASSUMPTION','source':'Cash-engine maturity lines and profile lease principal run-rate',
          'reported_total_usd_m':value('rates.financial_debt_usd_m'),
          'reconciliation_note':'Synthetic scheduled principal within the selected horizon only; not a disclosed full debt maturity ladder.'}
    actions=[]
    if incomplete:
        actions.append({'rule_id':'cash_data_missing','module':'freight','area':'Data completeness','text':'Resolve missing inputs before relying on the projected cash position.',
                        'condition':'Saved cash run has incomplete inputs','assumptions':warnings,'severity':'HIGH','kind':'review prompt'})
    for r in sorted(rows,key=lambda r:r['impact_usd_m'] if r['impact_usd_m'] is not None else 0):
        if r['impact_usd_m'] is not None and r['impact_usd_m']<0:
            actions.append({'rule_id':'review:'+r['id'],'module':r['module'],'area':r['label'],
                            'text':f"Review {r['label'].lower()} cash timing and exposure under this saved scenario.",
                            'condition':f"Adverse paired cash delta in run {run['run_id']}",'assumptions':r['warnings'],
                            'severity':status if status in {'LOW','HIGH','CRITICAL'} else 'LOW','kind':'review prompt'})
    start_eur=convert(baseline_min['cash_usd_m']+(credit or 0)+((investments or 0) if counted else 0)) if baseline_min else None
    # To reconcile exactly, this waterfall compares both paths at the same selected minimum date.
    min_date=scenario_min['date'] if scenario_min else None
    baseline_at_min=next((r for r in base if r['date']==min_date),None)
    start_eur=convert(baseline_at_min['cash_usd_m']+(credit or 0)+((investments or 0) if counted else 0)) if baseline_at_min else None
    before_min=sum(l['delta_usd_m'] for l in lines if min_date and l['date']<=min_date)
    end_eur=convert(scenario_liquidity)
    waterfall={'available':start_eur is not None and end_eur is not None and complete,'unit':'EUR m',
               'steps':[{'label':'Baseline at minimum date','kind':'start','eur_m':start_eur},
                        {'label':'Net paired cash delta','kind':'down' if before_min<0 else 'up','eur_m':convert(before_min)},
                        {'label':'Scenario minimum','kind':'end','eur_m':end_eur}],
               'minimum_eur_m':convert(reserve),'upside_not_counted':[],
               'note':'Net cash deltas, including favourable cash flows, compare both paths at the scenario minimum date; special funds follow the selected liquidity-tier switch.'}
    bullets=[{'kind':'headroom','id':'liquidity','tone':'adverse' if consumed is not None and consumed>0 else 'neutral',
              'text':f"Projected minimum headroom is EUR {convert(hs):,.1f}m through {cutoff}." if convert(hs) is not None else f"Cash projection requires the missing inputs for the period through {cutoff}."}]
    known=[r for r in rows if r['impact_usd_m'] is not None]
    if known:
        largest=max(known,key=lambda r:abs(r['impact_usd_m']))
        bullets.append({'kind':'impact','id':largest['id'],'tone':'adverse' if largest['impact_usd_m']<0 else 'neutral',
                        'text':f"{largest['label']} has the largest cash impact: EUR {largest['impact_eur_m']:+.2f}m within this horizon."})
    calculation={'run_id':run['run_id'],'status':status,'flags':flags,'missing_inputs':missing,'profile_version':run.get('profile_version'),
                 'attribution_usd_m':{**attribution,'fx':fx_total,'total':total},
                 'horizon_end':cutoff,'baseline_min_usd_m':hb,'scenario_min_usd_m':hs,'consumed_pct':consumed,
                 'forecast_id':run.get('forecast_id'),'forecast_modified':run.get('forecast_modified',False),
                 'forecast_overrides':run.get('forecast_overrides',{}),
                 'monitoring_complete':bool(base and scenario and complete and not incomplete),
                 'first_breach_date':next((point['date'] for point in sorted(scenario,key=lambda p:p['date']) if point['headroom_usd_m']<0),None),
                 'days_until_minimum':(date.fromisoformat(min_date)-as_of).days if min_date else None,
                 # the engine's own run status, reasons and desk exceptions (hlag_engine runs; absent on the mock)
                 'engine_status':run.get('status'),'status_reasons':run.get('status_reasons',[]),
                 'desk_exceptions':run.get('desk_exceptions',[]),
                 # desk exceptions are measured over the engine's full horizon, not the selected one
                 'engine_horizon_end':(run.get('baseline_path') or [{}])[-1].get('date')}
    return {'as_of':as_of.isoformat(),'horizon_months':months,'horizon_days':n_days,
            'horizons':[{'months':m,'days':(month_end(add_months(as_of,m))-as_of).days} for m in (3,6,12)],
            'eurusd':{'value':eur,'date':snapshots.get('FX_USD_PER_EUR',{}).get('date'),'provenance':snapshots.get('FX_USD_PER_EUR',{}).get('label','UNAVAILABLE')},
            'market_metrics':metrics,'scenario':{'shocks':{s['id']:s['value'] for s in sliders},'custom':bool(shocks),'sliders':sliders,
                'presets':[{'id':'observed','label':"Last week's moves repeated",'description':"Today's prices are already in the baseline; applies last week's moves once more. Missing comparisons use zero.",'shocks':{}},
                           {'id':'eur_up','label':'EUR +10%','description':'Illustrative single-driver FX scenario','shocks':{'fx_eur':10}},
                           {'id':'eur_rates_up','label':'€STR +100 bp','description':'Illustrative EUR benchmark step','shocks':{'interest':100}}],
                'note':'Sliders change only this cash-engine run. The full saved scenario preserves route, timing and multi-driver forecasts.'},
            'cash_impact':{'unit':'EUR m','rows':rows,'direct':direct,'overlap_adjustment_eur_m':0,'adverse_total_eur_m':adverse,
                           'net_eur_m':net,'adverse_pct_of_headroom':abs(adverse)/convert(hb)*100 if adverse is not None and hb is not None and hb>0 else None,
                           'note':'All rows are cash-flow deltas, including interest. MFR is owned once by bunker. FX allocation includes policy settlements. Partial rows are never summed as zero.'},
            'liquidity':{'unit':'EUR m','reported_usd_m':reported,'balance_date':profile.get('balance_date'),
                         'balance_date_eurusd':eur,'current_eur_m':convert(reported),'reported_total_eur_m':convert(reported),'operating_eur_m':convert(operating),
                         'minimum_eur_m':convert(reserve),'headroom_eur_m':convert(hb),
                         'scenario_eur_m':end_eur,'scenario_headroom_eur_m':convert(hs),
                         'baseline_min_date':baseline_min['date'] if baseline_min else None,'scenario_min_date':min_date,
                         'cash_t0':cash_t0(run.get('liquidity_inputs'),convert),
                         'items_usd_m':{'cash_and_equivalents':cash or 0,'credit_lines':credit or 0,'financial_assets':investments or 0},
                         'provenance':'ASSUMPTION','source':'Versioned company profile and paired cash-engine run',
                         'minimum_note':'Minimum operating reserve is a model assumption. Special funds are excluded from operating headroom unless enabled.',
                         'note':'Scenario liquidity is the minimum of the selected cash path. All EUR displays use the same T0 USD/EUR rate.',
                         'warnings':warnings},
            'debt_maturity':debt,'recommended_actions':actions[:3],'all_actions':actions,
            'insights':{'what_changed':bullets,'waterfall':waterfall,'breakevens':[],'debt':{'available':False},
                        'note':'No linear break-even estimate is asserted for the cash engine; reset lags, leakage, hedges and minimum-date shifts are nonlinear.'},
            'data_sources':{'market':{'source':'local'},
                            'exposures':{'source':'synthetic_register'},'liquidity':{'source':'versioned_profile'}},
            'cash_engine':calculation,'calculation':calculation}
