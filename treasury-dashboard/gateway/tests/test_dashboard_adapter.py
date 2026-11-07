from copy import deepcopy
from datetime import date

import pytest

from treasury_gateway.dashboard_adapter import compose_cash_dashboard, slider_scenario


DAY=date(2026,11,2)


def sample():
    def line(owner,code,sub,d,base,scenario,currency='USD',bx=1,sx=1):
        return {'owner':owner,'line_code':code,'sub_id':sub,'date':d,'currency':currency,
                'amount_native_base_m':base,'amount_native_scenario_m':scenario,'fx_rate_base':bx,
                'fx_rate_scenario':sx,'amount_usd_base_m':base*bx,'amount_usd_scenario_m':scenario*sx,
                'delta_usd_m':scenario*sx-base*bx}
    def path(amounts):
        return [{'date':d,'cash_usd_m':cash,'headroom_usd_m':cash+200}
                for d,cash in zip(('2026-12-31','2027-02-28','2027-05-31','2027-11-30'),amounts)]
    values={'liquidity.cash_usd_m':1000,'liquidity.credit_lines_usd_m':300,'liquidity.investments_usd_m':200,
            'liquidity.reserve_usd_m':100,'liquidity.count_liquid_investments':False,
            'liquidity.lease_monthly_usd_m':10,'rates.financial_debt_usd_m':600}
    quotes={s:{'series_id':s,'date':'2026-11-02','value':v,'source':'local_csv','label':'ASSUMPTION'}
            for s,v in [('FX_USD_PER_EUR',2),('FX_USD_PER_GBP',1.3),('FX_USD_PER_CNY',.14),
                        ('ESTR',2.5),('VLSFO_RTM',500),('HSFO_RTM',400),('WCI_COMPOSITE',2400)]}
    return {'run_id':'cash_test','as_of':DAY.isoformat(),'profile_version':'test','market_snapshot':quotes,
            'profile':{'balance_date':'2026-06-30','values':{k:{'value':v} for k,v in values.items()},
                       'trades':{'ASIA_EUROPE':{'volume_h1_teu':100}}},
            'scenario':{'name':'Test'},'flags':{'incomplete_data':False},'missing_inputs':[],'limitations':[],
            'baseline_path':path([900,700,600,50]),'scenario_path':path([920,714,614,34]),
            'lines':[line('freight','FREIGHT_REVENUE','freight:spot','2026-12-31',100,120),
                     line('rates','INTEREST_EXPENSE','rates:float','2027-01-15',-10,-15),
                     line('fx','FX_OPERATING_COST','fx:operating:EUR','2027-02-01',-10,-10,'EUR',2,2.2),
                     line('fx','HEDGE_SETTLEMENT','hedge:fx:operating:EUR','2027-02-01',0,1),
                     line('fx','FX_OPERATING_COST','fx:operating:GBP','2027-11-30',-10,-40),
                     line('rates','DEBT_PRINCIPAL','rates:principal','2027-02-28',-100,-100)]}


def test_selected_horizon_cash_rows_sum_without_fixture_totals():
    run=sample()
    d=compose_cash_dashboard({'liquidity':{'current_eur_m':999999},'insights':{'breakevens':[999999]}},run,{},3,{},DAY)
    rows=d['cash_impact']['rows']
    assert sum(r['impact_eur_m'] for r in rows)==pytest.approx(7)
    assert d['cash_impact']['net_eur_m']==pytest.approx(7)
    assert next(r for r in rows if r['id']=='interest')['cash'] is True
    assert d['cash_engine']['horizon_end']=='2027-02-28'
    assert d['liquidity']['scenario_headroom_eur_m']==457
    assert d['liquidity']['current_eur_m']==750
    assert d['liquidity']['operating_eur_m']==650
    assert d['insights']['breakevens']==[]
    assert d['cash_engine']['flags']['manual_inputs'] is True
    assert d['data_sources']['market']['source']=='local'
    assert '999999' not in str(d)
    twelve=compose_cash_dashboard({},run,{},12,{},DAY)
    assert twelve['cash_impact']['net_eur_m']==pytest.approx(-8)
    assert twelve['liquidity']['scenario_min_date']=='2027-11-30'
    assert d['insights']['waterfall']['steps'][-1]['eur_m']==457+50


def test_missing_material_impact_is_unknown_not_zero():
    r=sample();r['flags']['incomplete_data']=True;r['missing_inputs']=['market:WCI_SHA_RTM']
    d=compose_cash_dashboard({},r,{},3,{},DAY)
    freight=next(x for x in d['cash_impact']['rows'] if x['id']=='freight')
    assert freight['impact_eur_m'] is None and freight['provenance']=='UNAVAILABLE'
    assert d['cash_impact']['net_eur_m'] is None
    assert d['cash_engine']['status']=='INCOMPLETE'
    assert d['insights']['waterfall']['available'] is False


def test_weekly_change_never_uses_half_year_anchor_as_previous_week():
    r=sample()
    market={'history':{'VLSFO_RTM':[{'date':'2026-06-30','value':400},{'date':'2026-11-02','value':500}]}}
    d=compose_cash_dashboard({},r,market,3,{},DAY)
    assert next(x for x in d['market_metrics'] if x['id']=='bunker')['change'] is None
    market['history']['VLSFO_RTM'].insert(1,{'date':'2026-10-26','value':480})
    d=compose_cash_dashboard({},r,market,3,{},DAY)
    assert next(x for x in d['market_metrics'] if x['id']=='bunker')['change']==20


def test_slider_merge_preserves_detailed_forecast_and_units():
    forecast={'event_ids':['E1'],'freight':{'volume_shock_pct':{'ASIA_EUROPE':-.05}},
              'bunker':{'consumption_uplift_pct':.1},'fx':{'move_pct':{'BRL':-.1}}}
    original=deepcopy(forecast)
    market={'series':list(sample()['market_snapshot'].values())}
    p=slider_scenario({'bunker':100,'interest':25,'freight':-20,'fx_eur':10},DAY,market,forecast)
    assert p['bunker']['vlsfo_shock_pct']==.2 and p['bunker']['hsfo_shock_pct']==.25
    assert p['bunker']['consumption_uplift_pct']==.1
    assert p['fx']['move_pct']=={'BRL':-.1,'EUR':.1}
    assert p['freight']['volume_shock_pct']=={'ASIA_EUROPE':-.05}
    assert set(p['freight']['rate_shock_pct'].values())=={-.2}
    assert p['rates']['rate_path']['USD']==[{'date':'2026-11-02','step_bp':25}]
    assert p['rates']['rate_path']['EUR']==p['rates']['rate_path']['USD']
    assert forecast==original
    for invalid in ({'freight':51},{'fx_eur':float('nan')},{'unknown':1}):
        with pytest.raises(ValueError): slider_scenario(invalid,DAY,market)
    with pytest.raises(ValueError): slider_scenario({'bunker':100},DAY,{})


def test_unset_sliders_and_default_observed_run_agree():
    market={'series':list(sample()['market_snapshot'].values()),'history':{
        'VLSFO_RTM':[{'date':'2026-10-26','value':480}],
        'WCI_COMPOSITE':[{'date':'2026-10-26','value':2000}],
        'ESTR':[{'date':'2026-10-26','value':2.25}]}}
    p=slider_scenario({},DAY,market)
    assert p['bunker']['vlsfo_shock_pct']==pytest.approx(20/500)
    assert set(p['freight']['rate_shock_pct'].values())=={.2}
    assert p['rates']['rate_path']['EUR'][0]['step_bp']==25
    assert p['fx']['move_pct']['EUR']==0
    forecast={'bunker':{'vlsfo_shock_pct':.1}}
    assert slider_scenario({},DAY,market,forecast)==forecast


def test_current_monitoring_uses_selected_horizon_path_and_reports_breach():
    run=sample()
    run['scenario_path'][2]['headroom_usd_m']=-20
    run['scenario_path'][2]['cash_usd_m']=-220
    d=compose_cash_dashboard({},run,{},6,{},DAY)
    m=d['calculation']
    assert m['status']=='CRITICAL'
    assert m['first_breach_date']=='2027-05-31'
    assert m['days_until_minimum']==(date(2027,5,31)-DAY).days
    assert d['liquidity']['scenario_min_date']=='2027-05-31'
    assert d['liquidity']['scenario_headroom_eur_m']==-10
    assert m['monitoring_complete'] is True
    short=compose_cash_dashboard({},run,{},3,{},DAY)
    assert short['calculation']['first_breach_date'] is None
    assert short['liquidity']['scenario_min_date']=='2027-02-28'


def test_current_monitoring_does_not_claim_complete_when_inputs_missing():
    run=sample()
    run['flags']['incomplete_data']=True
    run['missing_inputs']=['profile:liquidity.cash_usd_m']
    d=compose_cash_dashboard({},run,{},6,{},DAY)
    assert d['calculation']['monitoring_complete'] is False


def test_weekly_change_uses_quote_date_not_dashboard_date():
    from treasury_gateway.dashboard_adapter import _weekly_move
    run = sample()
    run['market_snapshot']['VLSFO_RTM'].update(date='2026-10-23', value=510)
    market = {'market_snapshot': run['market_snapshot'], 'history': {'VLSFO_RTM': [
        {'date': '2026-10-16', 'value': 500}, {'date': '2026-10-23', 'value': 510}]}}
    assert _weekly_move(market, 'VLSFO_RTM', DAY) == 10
    view = compose_cash_dashboard({}, run, market, 3, {}, DAY)
    metric = next(m for m in view['market_metrics'] if m['id'] == 'bunker')
    assert metric['change'] == 10 and metric['change_pct'] == 2
    market['history']['VLSFO_RTM'] = [{'date': '2026-10-23', 'value': 510}]
    assert _weekly_move(market, 'VLSFO_RTM', DAY) is None
