"""Cash ledger identities and specification boundaries, without network or LLM calls."""
from datetime import date
import copy
import json
import sqlite3

import pytest

from treasury_gateway.risk_engine import RiskEngine, PROFILE, scenario_params, validate_profile

DAY = date(2026,11,2)


@pytest.fixture(scope='module')
def engine(tmp_path_factory):
    return RiskEngine(tmp_path_factory.mktemp('cash') / 'absent.sqlite',demo=True)


@pytest.fixture(scope='module')
def baseline(engine):
    return engine.run(DAY)


def test_zero_shock_and_anchor_identity(baseline):
    assert baseline['baseline_path'] == baseline['scenario_path']
    assert all(line['delta_usd_m'] == 0 for line in baseline['lines'])
    assert baseline['attribution']['total'] == 0
    assert all(abs(v)<1e-9 for v in baseline['anchor_adjustment']['by_driver_usd_m'].values())
    assert baseline['flags']['demo'] and baseline['flags']['synthetic_register']


def test_joint_full_outer_pairing_and_attribution(engine):
    p=next(p['scenario'] for p in engine.presets() if p['id']=='red_sea_reescalation')
    p['fx']={'move_pct':{'EUR':.1,'CNY':-.05}}
    result=engine.run(DAY,p)
    bars=result['attribution']
    assert abs(sum(bars[k] for k in ('freight','bunker','rates','fx'))-bars['total'])<1e-8
    assert abs(sum(l['delta_usd_m'] for l in result['lines'])-bars['total'])<1e-8
    assert abs(result['scenario_path'][-1]['cash_usd_m']-result['baseline_path'][-1]['cash_usd_m']-bars['total'])<2e-6
    assert any(l['amount_native_base_m']==0 or l['amount_native_scenario_m']==0 for l in result['lines'])
    assert all(l['currency']=='USD' or l['fx_rate_base']>0 for l in result['lines'])


def test_state_override_applies_to_both_passes(engine,baseline):
    r=engine.run(DAY,{'overrides':{'bunker.scrubber_share':.3},'rates':{'zim_tranche_on':True}})
    assert r['baseline_path']==r['scenario_path']
    assert r['attribution']['total']==0
    assert r['baseline_path']!=baseline['baseline_path']


def test_old_coupon_not_shocked_until_reset(engine):
    r=engine.run(DAY,{'rates':{'rate_path':{'USD':[{'date':DAY.isoformat(),'step_bp':100}]}}})
    lines=[l for l in r['lines'] if l['sub_id'].startswith('rates:float:USD')]
    initial=[l for l in lines if l['date']<='2027-02-02']
    assert initial and all(l['delta_usd_m']==0 for l in initial)
    assert any(l['delta_usd_m']<0 for l in lines if l['date']>'2027-02-02')


def test_missing_real_data_is_explicit(tmp_path):
    r=RiskEngine(tmp_path/'not_created.sqlite').run(DAY)
    assert r['status']=='INCOMPLETE'
    assert r['flags']['incomplete_data'] and not r['flags']['demo']
    assert 'market:WCI_SHA_RTM' in r['missing_inputs']
    assert r['market_snapshot']=={}
    assert not (tmp_path/'not_created.sqlite').exists()


def test_scenario_validation_rejects_unknown_and_bad_units():
    for p in [{'fx':{'move_pct':{'EUR':10}}},{'arbitrary':1},{'rates':{'rate_path':{'JPY':[]}}},
              {'overrides':[]},{'bunker':{'vlsfo_shock_pct':float('nan')}}]:
        with pytest.raises(ValueError): scenario_params(p,DAY)


def test_profile_and_cad_cap_validation(engine):
    profile=json.loads(PROFILE.read_text())
    profile['values']['liquidity.cash_usd_m']['source']=''
    with pytest.raises(ValueError): validate_profile(profile)
    with pytest.raises(ValueError): engine.run(DAY,{'overrides':{'fx.cad_hedge_ratio':.9}})
    with pytest.raises(ValueError): engine.run(DAY,{'overrides':{'liquidity.cash_usd_m':1}})


def test_future_adjustment_is_dated_and_sizing_state_preserved(engine):
    adjustment={'param_key':'bunker.scrubber_share','new_value':.3,'effective_date':'2027-03-01',
                'review_date':'2027-08-01','status':'approved','adjustment_id':'test'}
    r=engine.run(DAY,adjustments=[adjustment])
    assert r['baseline_path']==r['scenario_path']
    assert r['attribution']['total']==0
    assert len(r['adjustments'])==1


def test_refinancing_unavailable_is_shock(engine):
    r=engine.run(DAY,{'rates':{'refinancing_mode':'unavailable'}})
    drawdowns=[l for l in r['lines'] if l['line_code']=='DEBT_DRAWDOWN']
    assert drawdowns and all(l['amount_native_base_m']>0 and l['amount_native_scenario_m']==0 for l in drawdowns)
    assert r['attribution']['rates']<0


def test_bunker_hedge_is_state_and_margin_releases(engine):
    hedge={'module':'bunker','instrument':'swap','target':'fuel_all','coverage_pct':.5,'tenor_months':6}
    zero=engine.run(DAY,{'hedge_tests':[hedge]})
    assert zero['baseline_path']==zero['scenario_path']
    r=engine.run(DAY,{'bunker':{'vlsfo_shock_pct':-.2,'hsfo_shock_pct':-.2},'hedge_tests':[hedge]})
    margins=[l for l in r['lines'] if l['line_code']=='MARGIN_CALL']
    assert margins and min(l['amount_usd_scenario_m'] for l in margins)<0
    assert abs(sum(l['amount_usd_scenario_m'] for l in margins))<1e-7
    assert r['margin_test']['applicable']


def test_mock_engine_refuses_direct_cash_flows(engine):
    flow = {'effect_id': 'fine', 'overlap_key': 'fine', 'date': '2026-12-15', 'amount_usd_m': -100,
            'label': 'Fine', 'rationale': 'Stated fine'}
    with pytest.raises(ValueError, match='require hlag_engine'):
        engine.run(DAY, {'direct': {'cash_flows': [flow]}})
    with pytest.raises(ValueError, match='require effect_id'):
        engine.run(DAY, {'direct': {'cash_flows': [{'date': '2026-12-15', 'amount_usd_m': -1}]}})
