"""Treasury recommendation engine (recommendation-agent/3): engine hedge tools, effect grading, the v3 model loop
(per-item checks, repair, removal), the rule fallback and the API.

Engine tests run hlag_engine read-only (write=False) against its default store; the model is a scripted mock
transport, so no provider is called."""
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from treasury_gateway.recommendation_agent import grade_effect, select_events

AS_OF = date(2026, 11, 3)
FUEL_UP = {'bunker': {'vlsfo_shock_pct': .3, 'hsfo_shock_pct': .3}, 'name': 'Fuel +30%', 'start_date': AS_OF.isoformat()}
EXAMPLE = Path(__file__).resolve().parents[3] / 'news-engine-v8/examples/event-result.json'


# ---------------------------------------------------------------- pure functions (no engine)
def _payload():
    src = lambda aid, ts: {'article_id': aid, 'url': f'https://x/{aid}', 'title': aid, 'publisher': 'P', 'published_at': ts}
    ev = lambda eid, arts, total: {'event_id': eid, 'title': eid, 'category': 'industry', 'article_ids': arts,
                                   'summary': 's', 'score': {'total': total}, 'why_it_matters': [], 'what_to_do': []}
    return {'sources': [src('A1', '2026-10-01T08:00:00Z'), src('A2', '2026-10-20T08:00:00Z'), src('A3', '2026-10-28T08:00:00Z'),
                        src('A4', None)],
            'events': [ev('E1', ['A1'], 40), ev('E2', ['A2', 'A3'], 60), ev('E3', ['A4'], 90)]}


def test_time_window_uses_latest_article_and_skips_undated():
    events, notes = select_events(_payload(), date(2026, 10, 15), date(2026, 10, 31))
    assert [e['event_id'] for e in events] == ['E2'] and events[0]['latest_published_at'].startswith('2026-10-28')
    assert any('E3' in n for n in notes)
    events, _ = select_events(_payload(), None, date(2026, 10, 25))      # E2's latest article is after the window
    assert [e['event_id'] for e in events] == ['E1']
    events, notes = select_events(_payload(), date(2026, 10, 1), None, ['E1', 'E9'])
    assert [e['event_id'] for e in events] == ['E1'] and any('E9' in n for n in notes)
    events, _ = select_events(_payload(), date(2026, 9, 1), date(2026, 12, 1))
    assert [e['event_id'] for e in events] == ['E2', 'E1']             # ranked by v8 score


def test_grade_rules():
    ok = {'passed': True}
    assert grade_effect(10, 100, None, None, {'passed': False})[0] == 'counterproductive'
    assert grade_effect(-5, 100, None, None, ok)[0] == 'counterproductive'
    assert grade_effect(10, 100, '2027-01-31', None, ok)[0] == 'resolves_breach'
    assert grade_effect(10, 0, None, None, ok)[0] == 'not_needed'
    assert grade_effect(30, 100, None, None, ok)[0] == 'material'
    assert grade_effect(10, 100, None, None, ok)[0] == 'marginal'
    assert grade_effect(0, 100, None, None, ok)[0] == 'no_effect'
    # a margin shortfall counts against the hedge only when the hedge itself posts margin (team snapshot fix)
    assert grade_effect(0, 100, '2027-02-28', '2027-02-28', {'passed': False, 'hedge_posts_margin': False})[0] == 'no_effect'
    assert grade_effect(0, 100, None, None, {'passed': False, 'hedge_posts_margin': True})[0] == 'counterproductive'


# ---------------------------------------------------------------- engine tools

spec = pytest.mark.spec_engine


@pytest.fixture(scope='module')
def adapter():
    from treasury_gateway.engine_adapter import EngineAdapter, default_store_path
    if not default_store_path().is_file():
        pytest.skip('hlag_engine market store not available')
    return EngineAdapter()


@pytest.fixture(scope='module')
def fuel_lab(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab
    lab = HedgeLab(adapter, AS_OF, FUEL_UP, max_runs=20)
    lab.diag = lab.diagnose()
    lab.c25 = lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 12)
    lab.c50 = lab.test_hedge('bunker', 'swap', 'fuel_all', 50, 12)
    return lab


@spec
def test_diagnosis_finds_binding_date_and_module(fuel_lab):
    d = fuel_lab.diag
    assert d['adverse_modules'] == ['bunker'] and d['headroom_consumed_usd_m'] > 0
    assert d['scenario_min_date'] and d['binding_date_precision'] in {'day', 'month (accurate to the month)'}
    assert d['largest_adverse_lines'][0]['module'] == 'bunker' and d['largest_adverse_lines'][0]['delta_usd_m'] < 0
    assert d['run_id'].startswith('spec_')


@spec
def test_hedge_effect_comes_from_engine_runs(fuel_lab):
    c = fuel_lab.c25
    e = c['effect']
    assert e['min_headroom_change_usd_m'] == pytest.approx(e['min_headroom_with_usd_m'] - e['min_headroom_without_usd_m'], abs=2e-3)
    assert e['min_headroom_change_usd_m'] > 0 and c['grade'] in {'material', 'marginal', 'resolves_breach'}
    assert e['min_headroom_without_usd_m'] == pytest.approx(fuel_lab.diag['scenario_min_headroom_usd_m'])
    assert len(set(c['run_ids'].values())) == 4                       # four distinct, traceable runs
    assert c['timing']['binding_date_without'] == fuel_lab.diag['scenario_min_date']
    cover = c['timing']['cover_needed_from']
    assert cover == fuel_lab.diag['first_adverse_cash_date_by_module']['bunker'] <= c['timing']['binding_date_without']
    assert c['timing']['starts_after_first_adverse_cash'] is False and 'act_by' not in c['timing']
    assert c['margin_test']['applicable'] is True                     # a swap posts margin
    # a swap is symmetric: what it saves over the horizon if fuel rises, it gives up if fuel falls
    assert e['cost_if_reverses_over_horizon_usd_m'] == pytest.approx(e['net_cash_change_over_horizon_usd_m'], rel=1e-3)
    assert e['cost_if_reverses_over_horizon_usd_m'] > 0
    # a bigger hedge offsets more of the bunker bar; the larger one is flagged when it exceeds the net loss
    assert fuel_lab.c50['effect']['driver_bar_change_usd_m'] > e['driver_bar_change_usd_m'] > 0
    for cand in (c, fuel_lab.c50):
        assert cand['effect']['over_hedge'] == (cand['effect']['offset_of_driver_loss_pct'] > 100)


@spec
def test_same_candidate_is_not_rerun_and_bad_calls_cost_nothing(fuel_lab):
    from treasury_gateway.recommendation_agent import ToolError
    used = fuel_lab.runs_used
    assert fuel_lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 12)['candidate_id'] == fuel_lab.c25['candidate_id']
    for call in (lambda: fuel_lab.test_hedge('bunker', 'swap', 'freight', 25, 12),
                 lambda: fuel_lab.test_hedge('bunker', 'swap', 'fuel_all', 150, 12),
                 lambda: fuel_lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 48),
                 lambda: fuel_lab.test_hedge('rates', 'fixed_price_supply', 'floating_debt_usd', 25, 12),
                 lambda: fuel_lab.test_hedge('fx', 'forward', 'x', 25, 12),
                 lambda: fuel_lab.test_hedge_ratio('fx.hedge_ratio.cad', 0.95),      # above the 80% cap
                 lambda: fuel_lab.test_hedge_ratio('bunker.mfr_share', 0.5),
                 lambda: fuel_lab.liquidity_path(candidate_id='cand_unknown')):
        with pytest.raises(ToolError):
            call()
    assert fuel_lab.runs_used == used


@spec
def test_liquidity_path_is_daily_near_term(fuel_lab):
    p = fuel_lab.liquidity_path(candidate_id=fuel_lab.c25['candidate_id'])
    days = [date.fromisoformat(x['date']) for x in p['points']]
    assert days and (days[1] - days[0]).days == 1


@spec
def test_run_budget_is_enforced(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab, ToolError
    lab = HedgeLab(adapter, AS_OF, FUEL_UP, max_runs=4)
    lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 12)              # 2 base + 2
    with pytest.raises(ToolError, match='budget'):
        lab.test_hedge('bunker', 'swap', 'fuel_all', 50, 12)
    assert lab.runs_used == 4


# ---------------------------------------------------------------- model tool loop (scripted provider)
CONFIG = {'api_key': 'k', 'model': 'test-model', 'base_url': 'https://llm.test/v1'}


@pytest.mark.parametrize('message', [None, {'content': 42}, {'content': None, 'tool_calls': [None]},
    {'content': None, 'tool_calls': [{'id': 'c1', 'function': {'name': 'test_hedge', 'arguments': {}}}]}])
def test_malformed_tool_message_fails_with_validation_error(message):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': [{'message': message}]}))
    with pytest.raises(ValueError):
        RecommendationAgent(CONFIG, transport=transport)._chat([])


def _call(i, name, args):
    return {'id': f'c{i}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


def _scripted(steps):
    """Each step receives the request messages and returns the assistant message."""
    seen = []

    def handler(request):
        body = json.loads(request.content)
        assert body['tools'] and body['model'] == 'test-model'
        step = steps[len(seen)]
        seen.append(body['messages'])
        return httpx.Response(200, json={'choices': [{'message': step(body['messages'])}],
                                         'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'cost': 0.001}})
    return httpx.MockTransport(handler), seen


def _last_tool(messages):
    return json.loads([m for m in messages if m['role'] == 'tool'][-1]['content'])


def test_api_refused_with_mock_engine(tmp_path):
    from fastapi.testclient import TestClient
    from treasury_gateway.app import create_app
    from treasury_gateway.assistant import Assistant
    app = create_app(news_dir=tmp_path / 'news', state_db=tmp_path / 'state.sqlite', assistant=Assistant(None),
                     cash_engine='mock', market_db=Path(__file__).resolve().parents[3] / 'market_data.sqlite')
    assert TestClient(app).post('/api/v1/recommendations/agent', json={}).status_code == 409



@spec
def test_fully_hedged_operating_costs_are_not_ranked_as_residual_losses(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab
    lab = HedgeLab(adapter, AS_OF, {'name': 'EUR coverage', 'fx': {'move_pct': {'EUR': .1}},
        'overrides': {'fx.hedge_ratio.eur_operating_costs': 1.0}})
    result = lab.diagnose()
    assert not any(r['line_code'] == 'FX_OPERATING_COST' for r in result['largest_adverse_lines'])
    assert any(r['line_code'] == 'DIVIDEND' for r in result['largest_adverse_lines'])
    assert 'Residual cash' in result['cash_basis']

# ================================================================ recommendation-agent/3
from test_fact_pack import event as _event, pool_of as _pool_of, saved as _saved  # noqa: E402

HORIZON = 180


@pytest.fixture(scope='module')
def situation(adapter):
    """A real fact pack: fuel news with a saved model forecast (+20%), Red Sea news on fixed rules."""
    from hlag_engine.company_profile.registers import load_registers
    from treasury_gateway.engine_adapter import page_market
    from treasury_gateway.fact_pack import (build_fact_pack, combine_event_forecasts, market_alerts, market_moves_7d,
                                            playbook_entries, select_active_events, situation_runs)
    fuel = _event('E1', 'Bunker prices surge after refinery outage', 60, '2026-10-30T00:00:00Z')
    sea = _event('E2', 'Red Sea attacks force carriers to reroute via the Cape', 50, '2026-10-29T00:00:00Z')
    pool = _pool_of(fuel, sea)
    events = select_active_events(pool)
    ticked = [e['event_id'] for e in events['included']]
    combined = combine_event_forecasts(pool['events'], pool, [_saved('E1', 'bunker.vlsfo_shock_pct', 20)], AS_OF, HORIZON)
    market = page_market(adapter.shared, AS_OF)
    runs = situation_runs(adapter, AS_OF, combined['scenario'], [], market=market)
    pack = build_fact_pack(as_of=AS_OF, months=6, runs=runs, market=market, events=events, ticked=ticked,
                           combined=combined, pool=pool, profile=adapter.shared.profile, registers=load_registers(),
                           alerts=market_alerts(adapter, AS_OF), market_moves=market_moves_7d(adapter, AS_OF, []),
                           playbook=playbook_entries(adapter))
    pack['engine_runs'] = len(runs)
    _PACK['pack'] = pack
    return {'pack': pack, 'combined': combined, 'situation_result': runs['situation_result']}


def _lab(adapter, situation, max_runs=10):
    from treasury_gateway.recommendation_agent import HedgeLab
    return HedgeLab(adapter, AS_OF, situation['combined']['scenario'], [], max_runs=max_runs)


def _context(messages):
    return json.loads(messages[1]['content'].split('\n', 1)[1])


_PACK = {}


def _valid_set(messages):
    """What a well-behaved model would submit: a set built only from the session's facts (the summary it was sent
    lists the same fact ids; get_fact_detail gives the rest)."""
    from treasury_gateway.recommendation_agent import rule_set
    ctx = _context(messages)
    assert set(ctx['FACTS']) == set(_PACK['pack']['facts'])
    return rule_set({'facts': _PACK['pack']['facts'], 'ticked': ctx['ticked_event_ids']}, [])


def _with_invented_number(messages):
    s = _valid_set(messages)
    s['decisions'][0]['why'].append({'text': 'This frees EUR 640m.', 'label': 'Engine', 'cites': ['F.sit.status']})
    return s


def _submit(i, builder):
    return lambda m: {'content': None, 'tool_calls': [_call(i, 'submit_recommendation_set', builder(m))]}


def _check(out, situation):
    from treasury_gateway.recommendation_validator import passed, validate_set
    tools = {c['candidate_id']: c for c in out['candidates']}
    return validate_set(out, situation['pack'], tools, min_decisions=0)


@spec
def test_rule_fallback_is_a_full_grounded_set(adapter, situation):
    from treasury_gateway.fact_pack import OWNER_ROLES
    from treasury_gateway.recommendation_agent import RecommendationAgent, rule_position
    from treasury_gateway.recommendation_validator import passed
    out = RecommendationAgent(None).run(_lab(adapter, situation), situation['pack'])
    assert out['prompt_version'] == 'recommendation-agent/3' and out['origin'] == 'rule_based'
    assert out['position'] == rule_position(situation['pack']) and len(out['position']['headline']) == 2
    assert 3 <= len(out['decisions']) <= 5 and [d['rank'] for d in out['decisions']] == list(range(1, len(out['decisions']) + 1))
    assert all(d['owner']['role'] in OWNER_ROLES and d['owner']['kind'] == 'business function' for d in out['decisions'])
    from treasury_gateway.recommendation_agent import PLAIN_PLAYBOOK
    plain = {(v, sc) for v, sc, _ in PLAIN_PLAYBOOK.values()}
    assert any((d['verb'], d['scope']) in plain for d in out['decisions'])                 # from the engine playbook
    assert passed(_check(out, situation)), _check(out, situation)
    assert out['how_reached']['rounding_note'].startswith('Figures may be rounded')
    assert out['runs_used'] <= out['run_budget']


@spec
def test_bunker_hedges_are_alternatives_when_the_engine_does_not_mark_fuel_exceptional(adapter, situation):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    assert situation['pack']['facts']['F.eng.bunker_exceptional']['value'] is False
    out = RecommendationAgent(None).run(_lab(adapter, situation), situation['pack'])
    fuel = [a for a in out['rejected_alternatives'] if 'fuel_all' in a['title']]
    assert fuel and all('exceptional' in a['reason']['text'] for a in fuel)
    assert not any(d['kind'] == 'hedge' and d['expected_effect']['engine']['module'] == 'bunker' for d in out['decisions'])


@spec
def test_lab_tests_the_news_situation_and_the_mirror_reverses_the_news(adapter, situation):
    lab = _lab(adapter, situation)
    assert lab.params.bunker.vlsfo_shock_pct == pytest.approx(20)
    assert lab.mirrored.bunker.vlsfo_shock_pct == pytest.approx(-20)
    assert not lab.params.fx.move_pct                                            # no observed moves added


@spec
def test_closed_policy_keys_are_refused_without_spending_runs(adapter, situation):
    from treasury_gateway.recommendation_agent import ToolError
    lab = _lab(adapter, situation)
    for key in ('fx.hedge_ratio.eur_dividend', 'fx.hedge_ratio.eur_operating_costs'):
        with pytest.raises(ToolError, match='closed by policy'):
            lab.test_hedge_ratio(key, 0.5)
    assert lab.runs_used == 0


@spec
def test_model_set_is_accepted_and_titles_are_built_by_the_server(adapter, situation):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    transport, seen = _scripted([_submit(1, _valid_set)])
    out = RecommendationAgent(CONFIG, transport=transport).run(_lab(adapter, situation), situation['pack'], 'model')
    assert out['origin'] == 'model' and out['model'] == 'test-model'
    assert out['how_reached']['model_calls'] == 1 and out['how_reached']['checks']['repaired'] == 0 and not out['removed_by_checks']
    cost = out['how_reached']['ai_cost']
    assert (cost['calls'], cost['cost_usd'], cost['prompt_tokens'], cost['completion_tokens']) == (1, 0.001, 100, 10)
    d = out['decisions'][0]
    assert d['title'].startswith(d['verb'] + ' ' + d['scope']) and d['title'].endswith(f"({d['owner']['role']})")
    first = seen[0]
    assert 'untrusted' in first[0]['content'] and '"FACTS"' in first[1]['content'] and '"diag"' in first[1]['content']


@spec
def test_a_bad_item_is_returned_with_its_reason_in_one_message_and_repaired(adapter, situation):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    transport, seen = _scripted([_submit(1, _with_invented_number), _submit(2, _valid_set)])
    out = RecommendationAgent(CONFIG, transport=transport).run(_lab(adapter, situation), situation['pack'], 'model')
    tool_msgs = [m for m in seen[1] if m['role'] == 'tool']
    assert len(tool_msgs) == 1 and 'Rejected items' in tool_msgs[0]['content'] and 'number 640' in tool_msgs[0]['content']
    assert out['origin'] == 'model' and out['how_reached']['checks']['repaired'] == 1 and not out['removed_by_checks']


@spec
def test_an_item_failing_after_two_repairs_is_removed_and_the_rest_kept(adapter, situation):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    from treasury_gateway.recommendation_validator import passed
    transport, seen = _scripted([_submit(i, _with_invented_number) for i in (1, 2, 3)])
    out = RecommendationAgent(CONFIG, transport=transport).run(_lab(adapter, situation), situation['pack'], 'model')
    assert out['how_reached']['model_calls'] == 3 and out['how_reached']['checks']['repaired'] == 2
    [gone] = out['removed_by_checks']
    assert out['how_reached']['removed_by_checks'] == out['removed_by_checks']
    assert gone['kind'] == 'decision' and any('number 640' in r for r in gone['reasons'])
    assert len(out['decisions']) == len(_valid_set(seen[0])['decisions']) - 1
    assert passed(_check(out, situation))


@spec
def test_provider_failure_falls_back_to_the_rule_set(adapter, situation):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    from treasury_gateway.recommendation_validator import passed
    transport = httpx.MockTransport(lambda r: httpx.Response(500, json={}))
    out = RecommendationAgent(CONFIG, transport=transport).run(_lab(adapter, situation), situation['pack'], 'auto')
    assert out['origin'] == 'rule_based' and 'RuntimeError' in out['how_reached']['note']
    assert passed(_check(out, situation))


FORBIDDEN = re.compile(r"\\b(preset|presets|run|runs|engine)\\b", re.I)


def _user_texts(out):
    texts = list(out['position']['headline'])
    for d in out['decisions']:
        texts += [d['verb'], d['scope'], d['title'], d['cash_at_stake']['text'], d['timing']['text'], d['usual_practice'],
                  d['if_wrong']['text']] + [w['text'] for w in d['why'] + d['escalate_if']]
    texts += [a['reason']['text'] for a in out['rejected_alternatives']]
    return texts


@spec
def test_rule_fallback_uses_plain_business_wording_and_sizes_every_decision(adapter, situation):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    out = RecommendationAgent(None).run(_lab(adapter, situation), situation['pack'])
    bad = [t for t in _user_texts(out) if FORBIDDEN.search(t)]
    assert not bad, bad
    assert all(d['cash_at_stake']['cites'] for d in out['decisions'])
    assert all(d['timing']['text'].startswith('before the low point on ') for d in out['decisions'])


@spec
def test_rule_position_monitors_when_the_net_effect_takes_ten_percent_of_baseline_headroom(situation):
    import copy
    from treasury_gateway.recommendation_agent import rule_position
    from treasury_gateway.recommendation_validator import Context, check_position
    pack = copy.deepcopy(situation['pack'])
    for k in ('F.liq.base.status', 'F.sit.status'):
        pack['facts'][k]['value'] = 'LOW'
    share = pack['facts']['F.der.sit.net_effect_pct_of_base_headroom']
    share['value'] = -12.0
    pos = rule_position(pack)
    assert pos['value'] == 'heightened_monitoring' and '12.0%' in pos['headline'][1]
    assert check_position(pos, Context(pack)) == []
    share['value'] = -5.0
    assert rule_position(pack)['value'] == 'business_as_usual'


@spec
def test_the_lab_reuses_the_fact_packs_situation_run(adapter, situation):
    lab = _lab(adapter, situation)
    lab.seed(situation['situation_result'])
    d = lab.diagnose()
    assert lab.runs_used == 1                                   # only the mirror; the situation is not run again
    assert d['scenario_min_headroom_usd_m'] == pytest.approx(situation['pack']['facts']['F.sit.min_headroom']['value'], abs=0.06)
    seeded = _lab(adapter, situation)
    seeded.seed(situation['situation_result'], lab._base_m)
    seeded.diagnose()
    assert seeded.runs_used == 0


@spec
def test_model_sees_a_compact_fact_summary_and_can_ask_for_details(adapter, situation):
    from treasury_gateway.recommendation_agent import RecommendationAgent
    steps = [lambda m: {'content': None, 'tool_calls': [_call(1, 'get_fact_detail', {'fact_ids': ['F.policy.bunker', 'F.sit.status']})]},
             _submit(2, _valid_set)]
    transport, seen = _scripted(steps)
    RecommendationAgent(CONFIG, transport=transport).run(_lab(adapter, situation), situation['pack'], 'model')
    facts = _context(seen[0])['FACTS']
    assert set(facts) == set(situation['pack']['facts'])
    row = facts['F.policy.bunker']
    assert {'l', 'v', 'p'} <= set(row) and 'source' not in row and 'rationale' not in row
    assert not any('articles' in r or 'summary' in r for r in facts.values())
    full = len(json.dumps(situation['pack']['facts'], default=str))
    assert len(json.dumps(facts, default=str)) < full / 2          # the summary is less than half of the full facts
    detail = _last_tool(seen[1])
    want = {k: situation['pack']['facts'][k] for k in ('F.policy.bunker', 'F.sit.status')}
    assert detail == json.loads(json.dumps(want, default=str))


@spec
def test_consumption_driven_fuel_scenario_tests_no_price_hedge(adapter):
    from treasury_gateway.recommendation_agent import HedgeLab, ToolError
    scenario = {'bunker': {'consumption_uplift_pct': .2}, 'name': 'More fuel burn', 'start_date': AS_OF.isoformat()}
    lab = HedgeLab(adapter, AS_OF, scenario)
    assert lab.diagnose()['bunker_driver'] == 'consumption'
    used = lab.runs_used
    with pytest.raises(ToolError, match='consumption'):
        lab.test_hedge('bunker', 'swap', 'fuel_all', 25, 12)
    assert lab.runs_used == used
    assert HedgeLab(adapter, AS_OF, FUEL_UP).bunker_driver() == 'price'


@spec
def test_rule_fallback_proposes_an_operations_review_when_consumption_drives_fuel(situation):
    import copy
    from treasury_gateway.recommendation_agent import rule_set
    from treasury_gateway.recommendation_validator import passed, validate_set
    pack = copy.deepcopy(situation['pack'])
    pack['facts']['F.sit.bunker_driver']['value'] = 'consumption'
    out = rule_set(pack, [])
    ops = [d for d in out['decisions'] if 'routing' in d['scope']]
    assert ops and ops[0]['owner']['role'] == 'Procurement' and 'surcharge recovery' in ops[0]['scope']
    assert passed(validate_set(out, pack, {}, min_decisions=0))


@spec
def test_fact_detail_lookups_are_capped_at_eight_per_session(adapter, situation):
    from treasury_gateway.recommendation_agent import DETAIL_LIMIT, RecommendationAgent
    ids = list(situation['pack']['facts'])[:10]
    steps = [lambda m: {'content': None, 'tool_calls': [_call(1, 'get_fact_detail', {'fact_ids': ids})]},
             lambda m: {'content': None, 'tool_calls': [_call(2, 'get_fact_detail', {'fact_ids': ids[:1]})]},
             _submit(3, _valid_set)]
    transport, seen = _scripted(steps)
    RecommendationAgent(CONFIG, transport=transport).run(_lab(adapter, situation), situation['pack'], 'model')
    first = _last_tool(seen[1])
    assert DETAIL_LIMIT == 8 and len([k for k in first if not k.startswith('_')]) == 8 and 'limit reached' in first['_note']
    assert 'limit reached' in _last_tool(seen[2])['error']


def test_prompt_rules_for_assumptions_and_neutral_example():
    from treasury_gateway.recommendation_agent import SYSTEM
    assert "supporting assumptions (assumption_ids) must all be likely" in SYSTEM
    assert "never list it in assumption_ids" in SYSTEM
    assert "Asia-Europe" not in SYSTEM and "collection terms for overdue accounts" in SYSTEM


# ---------------------------------------------------------------- API (recommendation-agent/3)
def _v8_result():
    """A valid contract-8 result: 10 scored Events (two drive the forecast), each with its own source Article."""
    def ev(eid, title, impact, relevance, urgency):
        aid = 'A' + eid[1:]
        return ({'event_id': eid, 'title': title, 'category': 'industry', 'summary': title, 'article_ids': [aid],
                 'score': {'impact': impact, 'relevance': relevance, 'urgency': urgency,
                           'total': 8 * impact + 7 * relevance + 5 * urgency,
                           'reasoning': {'impact': 'x', 'relevance': 'x', 'urgency': 'x'}},
                 'why_it_matters': [], 'what_to_do': []},
                {'url': f'https://example.com/{aid}', 'title': title, 'text': title, 'publisher': 'Example Wire',
                 'published_at': '2026-10-30T00:00:00Z', 'article_id': aid, 'discovered_at': '2026-11-01T00:00:00Z',
                 'normalized_url': f'https://example.com/{aid}', 'content_hash': aid})
    items = [ev('E01', 'Bunker prices surge after refinery outage', 4, 4, 3),
             ev('E02', 'Red Sea attacks force carriers to reroute via the Cape', 4, 3, 3)]
    items += [ev(f'E{i:02d}', f'Port congestion update {i}', 1, 1, 1) for i in range(3, 11)]
    return {'schema_version': 8, 'run_id': 'run_v3_test', 'status': 'completed', 'events': [e for e, _ in items],
            'sources': [s for _, s in items], 'errors': [], 'metrics': {}, 'overall_summary': 'Test'}


@pytest.fixture(scope='module')
def v3_client(tmp_path_factory, adapter):
    import os
    from fastapi.testclient import TestClient
    from treasury_gateway.app import create_app
    from treasury_gateway.assistant import Assistant
    from treasury_gateway.dashboard_adapter import horizon_cutoff
    from treasury_gateway.news_v8 import NewsV8Reader
    from treasury_sensitivity.audit import AuditStore
    from treasury_sensitivity.service import SensitivityService
    saved_env = os.environ.pop('NEWS_V8_DATABASE', None)   # use the imported result, not a live news database
    tmp = tmp_path_factory.mktemp('v3')
    NewsV8Reader(tmp / 'news').import_result(_v8_result())
    svc = SensitivityService(audit=AuditStore(':memory:'),
                             clock=lambda: datetime(2026, 11, 3, 9, tzinfo=ZoneInfo('Europe/Berlin')))
    app = create_app(service=svc, news_dir=tmp / 'news', state_db=tmp / 'state.sqlite', assistant=Assistant(None),
                     cash_engine='spec')
    app.state.store_version = lambda adapter: 'fixed-for-tests'   # the cache key no longer depends on the store's file time
    horizon = (date.fromisoformat(horizon_cutoff(AS_OF, 6)) - AS_OF).days
    f = _saved('E01', 'bunker.vlsfo_shock_pct', 20, horizon=horizon, pool='run_v3_test')
    st = app.state.intelligence
    with st.lock, st.db:
        st.db.execute('INSERT INTO intelligence_forecasts VALUES(?,?,?)', (f['forecast_id'], '2026-11-03T08:00:00Z', json.dumps(f)))
    yield TestClient(app), app
    if saved_env is not None:
        os.environ['NEWS_V8_DATABASE'] = saved_env


@spec
def test_active_events_lists_the_top_eight_and_the_rest(v3_client):
    c, _ = v3_client
    r = c.get('/api/v1/recommendations/active-events').json()
    assert len(r['included']) == 8 and r['more_count'] == 2 and len(r['not_included']) == 2
    by = {e['event_id']: e for e in r['included']}
    assert by['E01']['forecast'] == 'model_saved' and by['E02']['forecast'] == 'rule_based'


@spec
def test_session_uses_the_ticked_events_without_dates_and_is_saved(v3_client):
    c, _ = v3_client
    r = c.post('/api/v1/recommendations/agent', json={})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s['prompt_version'] == 'recommendation-agent/3' and s['session_id'].startswith('recsession_')
    assert len(s['events']['ticked']) == 8 and 'E01' in s['events']['ticked']
    origin = {x['event_id']: x['origin'] for x in s['events']['news_inputs']}
    assert origin['E01'] == 'model_saved' and origin['E02'] == 'rule_based'
    assert 3 <= len(s['decisions']) <= 5
    listed = c.get('/api/v1/recommendations/agent').json()['sessions'][0]
    assert listed['session_id'] == s['session_id'] and listed['position'] == s['position']['value'] and not listed['legacy']
    more = c.get('/api/v1/recommendations/active-events').json()['not_included'][0]['event_id']
    r = c.post('/api/v1/recommendations/agent', json={'event_ids': ['E01', more]})
    assert r.status_code == 200 and r.json()['events']['ticked'] == ['E01', more]
    assert c.post('/api/v1/recommendations/agent', json={'event_ids': ['E99']}).status_code == 422


@spec
def test_decisions_are_audited_and_change_nothing_else(v3_client):
    c, app = v3_client
    s = c.post('/api/v1/recommendations/agent', json={}).json()
    st = app.state.intelligence
    before = (json.dumps(st.adjustments(), default=str),
              st.db.execute('SELECT count(*) FROM intelligence_forecasts').fetchone()[0])
    dec = s['decisions'][0]['decision_id']
    r = c.post(f"/api/v1/recommendations/agent/{s['session_id']}/decisions",
               json={'decision_id': dec, 'decision': 'defer', 'reviewer': 'Reviewer', 'comment': 'Next week'})
    assert r.status_code == 200 and r.json()['decision'] == 'defer'
    got = c.get(f"/api/v1/recommendations/agent/{s['session_id']}/decisions").json()['decisions']
    assert got[0]['decision_id'] == dec and got[0]['reviewer'] == 'Reviewer' and got[0]['comment'] == 'Next week'
    after = (json.dumps(st.adjustments(), default=str),
             st.db.execute('SELECT count(*) FROM intelligence_forecasts').fetchone()[0])
    assert before == after
    assert c.post(f"/api/v1/recommendations/agent/{s['session_id']}/decisions",
                  json={'decision_id': 'dec_x', 'decision': 'approve', 'reviewer': 'R'}).status_code == 404



@spec
def test_fact_pack_and_a_session_are_prepared_ahead_and_shown_as_latest(v3_client):
    c, app = v3_client
    app.state.prepare_recommendations()
    status = c.get('/api/v1/recommendations/active-events').json()['fact_pack']
    assert status['status'] == 'ready' and status['as_of'] == AS_OF.isoformat()
    assert status['built_at'] and status['build_seconds'] >= 0
    latest = c.get('/api/v1/recommendations/agent/latest').json()['session']
    assert latest['prepared'] is True and latest['prepared_at'] and latest['origin'] == 'rule_based'   # no model configured
    assert latest['session_id'] == app.state.recommendation_fact_state['session_id']
    s = c.post('/api/v1/recommendations/agent', json={}).json()
    assert s['fact_pack']['cached'] is True and s['fact_pack']['built_at'] == status['built_at'] and s['prepared'] is False
    assert s['session_seconds'] >= 0 and s['how_reached']['engine_runs'] == s['runs_used'] + 4
    assert c.get('/api/v1/recommendations/agent/latest').json()['session']['session_id'] == s['session_id']


@spec
def test_a_news_import_rebuilds_the_fact_pack_in_the_background(v3_client):
    import time
    c, app = v3_client
    result = _v8_result()
    result['run_id'] = 'run_v3_refreshed'
    result['events'][0]['title'] = result['events'][0]['summary'] = 'Bunker prices surge again after refinery outage'
    assert c.post('/api/v1/intelligence/import-result', json=result).status_code == 200
    thread = app.state.recommendation_fact_state['thread']
    thread.join(timeout=600)                                 # wait for the background preparation itself, not a poll
    assert not thread.is_alive() and app.state.recommendation_fact_state['last_error'] is None
    status = c.get('/api/v1/recommendations/active-events').json()['fact_pack']
    assert status['status'] == 'ready', status
    latest = c.get('/api/v1/recommendations/agent/latest').json()['session']
    assert latest['prepared'] is True and latest['fact_pack']['ticked'] == status['ticked']
