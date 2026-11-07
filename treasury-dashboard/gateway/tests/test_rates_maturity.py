"""Original debt retires on maturity dates without changing quarterly reset dates."""
import copy
import json
from datetime import date

import pytest

from treasury_gateway.risk_engine import PROFILE, RiskEngine, old_debt_balance_days, scenario_params


def rate_pass(total=100, floating=100, monthly_maturity=25, start=date(2027,1,1), end=date(2027,7,1), scenario=None):
    profile = copy.deepcopy(json.loads(PROFILE.read_text(encoding='utf-8')))
    profile['trades'] = {}
    updates = {'rates.financial_debt_usd_m': total, 'rates.floating_debt_eur_m': floating,
               'rates.closing_usd_per_eur_2025': 1, 'rates.current_leases_usd_m': 0,
               'rates.current_debt_and_leases_usd_m': 12*monthly_maturity,
               'rates.usd_floating_share': 1, 'rates.usd_spread_bp': 0, 'rates.eur_spread_bp': 0,
               'rates.fixed_rate_pct': 12, 'rates.lease_interest_eur_m': 0,
               'rates.interest_bearing_share': 0, 'rates.new_issue_premium_bp': 0,
               'fx.dividend_eur_m': 0, 'fx.cad_operating_weekly_m': 0, 'fx.eur_operating_quarterly_m': 0,
               'fx.inr_brl_obligations_on': False}
    for key, value in updates.items():
        profile['values'][key]['value'] = value
    engine = RiskEngine('absent.sqlite', profile=profile)
    market = {key: {'value': value} for key, value in {'SOFR':12, 'ESTR':2, 'BB_OAS':0, 'FX_USD_PER_EUR':1}.items()}
    val = lambda key, day=start: profile['values'][key]['value']
    lines, _ = engine._pass(start, end, scenario_params(scenario, start), market, {}, {}, {}, val, lambda day: day)
    return list(lines.values())


def find(lines, sub, day):
    return next(line['amount_native_m'] for line in lines if line['sub_id']==sub and line['date']==day)


def test_balance_days_stops_on_maturity_and_includes_same_day_new_balance():
    maturities = [date(2027,1,31), date(2027,2,28), date(2027,3,31)]
    # Principal 100: 30 days *100 +28 *75 +31 *50 +1 *25 = 6675 principal-days.
    weighted_days = old_debt_balance_days(date(2027,1,1), date(2027,4,1), maturities, 25, 100)
    assert weighted_days * 100 == pytest.approx(6675)
    assert old_debt_balance_days(date(2027,1,31), date(2027,2,1), maturities,25,100)==.75


def test_floating_coupon_reduces_balance_inside_quarter_without_resetting_rate():
    base = rate_pass()
    stressed = rate_pass(scenario={'rates':{'rate_path':{'USD':[{'date':'2027-01-01','step_bp':100}]}}})
    expected = -(100/3) * .12 * 66.75/360
    assert find(base, 'rates:float:USD:2','2027-04-01') == pytest.approx(expected)
    # The first coupon uses the existing fixed coupon until 1 April despite earlier maturities.
    assert find(stressed, 'rates:float:USD:2','2027-04-01') == pytest.approx(expected)
    # The next coupon picks up the April reset rate but accrues only until full retirement on 30 April.
    assert find(stressed, 'rates:float:USD:2','2027-07-01') == pytest.approx(-(100/3)*.13*(29*.25)/360)
    # Refinance interest belongs to a separate tranche, beginning exactly on the January maturity date.
    assert find(base,'rates:refi:2027-01-31','2027-04-30') == pytest.approx(-25*.12*89/360)


def test_fixed_only_pool_amortizes_and_uses_equal_monthly_coupon_convention():
    lines=rate_pass(total=500,floating=0,end=date(2027,3,1))
    # EUR300 bond leaves USD200 fixed; January retires 25/500 of every old debt component.
    assert find(lines,'rates:fixed:USD','2027-01-31') == pytest.approx(-200*.12/12)
    assert find(lines,'rates:fixed:USD','2027-02-28') == pytest.approx(-190*.12/12)


def test_eur_bond_notional_cannot_exceed_fixed_pool():
    lines=rate_pass(total=100,floating=0,monthly_maturity=0,
                    start=date(2027,9,1),end=date(2027,11,1))
    assert find(lines,'rates:bond:EUR','2027-10-15') == pytest.approx(-100*.025)


def test_h1_eur_anchor_uses_disclosed_average_only_for_matching_period(tmp_path):
    profile=copy.deepcopy(json.loads(PROFILE.read_text(encoding='utf-8')))
    profile['disclosure_reference']['average_usd_per_eur_h1_2026']=1.1667
    engine=RiskEngine(tmp_path/'absent.sqlite',profile=profile)
    _,anchor,_=engine._market(date(2026,11,2))
    assert anchor['FX_USD_PER_EUR']==1.1667
    profile['anchor_start']='2026-07-01';profile['anchor_end']='2026-09-30'
    _,anchor,_=RiskEngine(tmp_path/'absent.sqlite',profile=profile)._market(date(2026,11,2))
    assert 'FX_USD_PER_EUR' not in anchor
    # A wholly illustrative demo keeps a consistent anchor equal to its own T0 prices.
    market,anchor,_=RiskEngine(tmp_path/'absent.sqlite',profile=profile,demo=True)._market(date(2026,11,2))
    assert anchor['FX_USD_PER_EUR']==market['FX_USD_PER_EUR']['value']
