"""MVP contracts with recorded providers; no network or real-model quality claims."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError
from news_engine.models import Article, ArticleInput, Event, EventIdentity, ExtractionDraft
from news_engine.extract import extract_article
from news_engine.events import group_change, retrieve_candidates
from news_engine.analyze import analyze_event


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def article(text='The central bank cut its policy rate by 25 basis points.', key='A1', title='Rate decision'):
    return Article(url=f'https://example.org/{key}', title=title, text=text, article_id=key,
                   discovered_at=NOW, normalized_url=f'https://example.org/{key}', content_hash=key)


def draft(text, **changes):
    return {'description': text, 'direction': 'unknown', 'claim_type': 'actual',
            'identity': {'subject':'Central bank','action':'policy decision','occurrence':'Policy rate decision','scope':'Funding conditions'},
            'numeric': [], 'evidence_quote': text, **changes}


def policy_timing(text):
    quote = text.strip().split("\n\n")[-1]
    return draft(quote, numeric=[{'value':'30','unit':'days','kind':'level',
        'comparison_basis':'after publication in the Federal Register','source_quote':'30 days'}])


def grouping(**changes):
    return {'event_id': None, 'confidence': 'new', 'relation': 'new',
            'identity': {'subject': 'Central bank', 'occurrence': 'Policy rate decision',
                         'scope': 'Funding conditions'}, **changes}


def analysis_reply(context=None, **changes):
    primary = context['current_event']['what_changed'][0]['change_id'] if context else 'C1'
    value = {'why_it_matters':['Funding assumptions could be affected if applicable.'],
        'what_to_do':['If applicable, review financing assumptions.'],
        'impact':3,'relevance':4,'urgency':3,
        'reasoning':{'impact':'Changes funding assumptions.','relevance':'Relevant to Treasury funding.','urgency':'Monitor current conditions.'},
        'primary_change_id':primary,'impact_basis':'financial_action',
        'relevance_basis':'direct_channel','urgency_basis':'operative_control_or_8_30_days',
        'has_treasury_action_or_constraint':True}
    value.update(changes)
    return value


class RecordedLLM:
    model = 'offline-recorded'

    def __init__(self, handler=None):
        self.handler = handler
        self.calls = []
        self.receipts = []
        self.lock = threading.Lock()

    def complete(self, response_model, prompt, context, *, stage):
        with self.lock:
            self.calls.append((stage, context))
            self.receipts.append({'service': 'llm', 'stage': stage, 'status': 'completed',
                'prompt_tokens': 10, 'completion_tokens': 5, 'cost_usd': 0.001})
        value = self.handler('group' if stage.startswith('group_') else stage, context) if self.handler else None
        if value is None and stage == 'extract':
            value = {'complete': True, 'changes': [draft(context['text'])]}
        if value is None and stage.startswith('group_'):
            value = grouping()
        # Recorded fixture builders supply both decisions; production has separate schemas.
        if stage.startswith('group_') and value is not None and 'confidence' in value:
            if response_model.__name__ == 'IdentityDraft':
                decision = 'uncertain' if value['confidence'] == 'uncertain' else 'same' if value.get('event_id') else 'new'
                value = dict(decision=decision,event_id=value.get('event_id') if decision == 'same' else None,
                    identity=value['identity'],reason='Recorded occurrence comparison.')
            elif response_model.__name__ == 'NoveltyDraft':
                compared = value.get('duplicate_of') or next(c['change_id'] for c in context['candidates'][0]['changes'])
                value = dict(relation=value['relation'],compared_change_id=compared,
                    reason='Recorded development comparison.')
            elif response_model.__name__ == 'RetrievalDraft':
                value = dict(event_ids=[c['event_id'] for c in context['candidates'][:5]])
        if value is None and stage == 'analyze':
            value = analysis_reply(context)
        if response_model.__name__ == 'EventExtractionDraft' and value is not None and 'changes' in value:
            value = dict(complete=value['complete'],events=[dict(title=c['description'][:180],
                identity=c.get('identity') or draft(c['description'])['identity'], category='regulatory',
                source_id=context['source_id'],changes=[c]) for c in value['changes']])
        if response_model.__name__ == 'QueryPlan' and value:
            value = {'queries':[dict(query=q,topic='macroeconomics',geography='Global') if isinstance(q,str) else q for q in value['queries']]}
        if stage=='extract' and 'events' in value:
            for event in value['events']:
                for change in event['changes']:
                    change.setdefault('claim_type_basis',{'actual':'observed_action','announced':'announced_action','proposed':'proposed_rule','forecast':'forecast','unverified':'insufficient_evidence'}[change['claim_type']])
                    change.setdefault('status_evidence_quote',change['evidence_quote'])
        return response_model.model_validate(value)


class ExtractionTests(unittest.TestCase):
    def test_source_numeric_and_units(self):
        source = article()
        number = {'value': '25', 'unit': 'bps', 'kind': 'delta',
                  'comparison_basis': None, 'source_quote': '25 basis points'}
        llm = RecordedLLM(lambda stage, ctx: {'complete': True,
            'changes': [draft(source.text, direction='decrease', numeric=[number])]})
        changes = extract_article(source, llm)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(changes[0].numeric[0].value, '25')
        evidence = changes[0].evidence
        self.assertEqual(source.text[evidence.start:evidence.end], evidence.quote)
        self.assertEqual(changes[0].source_id, source.article_id)

    def test_unsupported_value_unit_scale_currency_and_description_rejected(self):
        cases = [('26', 'bps', 'Rate cut by 25 basis points.'),
                 ('25', 'percent', 'Rate cut by 25 basis points.'),
                 ('25', 'million USD', 'Rate cut by 25 basis points.'),
                 ('25', 'USD', 'Cost was $25.'),
                 ('NaN', 'bps', 'Rate cut by 25 basis points.')]
        for value, unit, text in cases:
            with self.subTest(value=value, unit=unit):
                llm = RecordedLLM(lambda stage, ctx: {'complete': True, 'changes': [draft(text,
                    numeric=[{'value': value, 'unit': unit, 'kind': 'level', 'source_quote': text}])]})
                with self.assertRaises(ValueError):
                    extract_article(article(text), llm)
                self.assertEqual(len(llm.calls), 2)
                self.assertIn('repair', llm.calls[-1][1])
        llm = RecordedLLM(lambda stage, ctx: {'complete': True,
            'changes': [draft('It rose by 900.', evidence_quote=ctx['text'])]})
        with self.assertRaises(ValueError):
            extract_article(article(), llm)

    def test_qualitative_claims_and_unknown_time_survive(self):
        for claim in ('actual', 'announced', 'proposed', 'forecast', 'unverified'):
            text = 'The facility may improve lending capacity, subject to approval.'
            llm = RecordedLLM(lambda stage, ctx: {'complete': True,
                'changes': [draft(text, claim_type=claim, direction='increase')]})
            change = extract_article(article(text), llm)[0]
            self.assertEqual(change.claim_type, claim)
            self.assertEqual(change.numeric, [])
            self.assertIsNone(change.occurrence_at)
            self.assertIn('subject to approval', change.description)

    def test_empty_success_differs_from_incomplete_and_transport_failure(self):
        empty = RecordedLLM(lambda stage, ctx: {'complete': True, 'changes': []})
        self.assertEqual(extract_article(article('Background information.'), empty), [])
        incomplete = RecordedLLM(lambda stage, ctx: {'complete': False, 'changes': []})
        with self.assertRaisesRegex(ValueError, 'one repair'):
            extract_article(article(), incomplete)
        self.assertEqual(len(incomplete.calls), 2)
        def unavailable(stage, ctx):
            raise RuntimeError('provider unavailable')
        failure = RecordedLLM(unavailable)
        with self.assertRaises(RuntimeError):
            extract_article(article(), failure)
        self.assertEqual(len(failure.calls), 1)

    def test_repair_once_and_every_contiguous_chunk_processed(self):
        def handler(stage, ctx):
            if 'repair' not in ctx:
                return {'complete': True, 'changes': [draft('Invented quotation')]}
            return {'complete': True, 'changes': [draft(ctx['text'])]}
        source = article('Opening news.\nMiddle news.\nClosing news.')
        llm = RecordedLLM(handler)
        changes = extract_article(source, llm, chunk_size=15)
        primaries = [ctx['text'] for _, ctx in llm.calls if 'repair' not in ctx]
        self.assertEqual(''.join(primaries), source.text)
        self.assertEqual(len(llm.calls), len(primaries) * 2)
        for change in changes:
            evidence = change.evidence
            self.assertEqual(source.text[evidence.start:evidence.end], evidence.quote)

    def test_unknown_fields_and_naive_dates_rejected(self):
        with self.assertRaises(ValidationError):
            ArticleInput(url='https://example.org', title='News', text='body', fact_catalog=[])
        with self.assertRaises(ValidationError):
            ArticleInput(url='https://example.org', title='News', text='body', published_at='2026-01-01T00:00:00')


class GroupingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.change = extract_article(article(), RecordedLLM())[0]
        self.event = Event(category='regulatory',event_id='E1', title='Central bank policy rate decision',
            what_changed=[self.change], latest_change_id=self.change.change_id,
            identity=EventIdentity(subject='Central bank', occurrence='Policy rate decision', scope='Funding'),
            created_at=NOW, last_seen_at=NOW, last_material_at=NOW)

    def test_no_candidate_needs_no_call_and_retrieval_is_bounded(self):
        llm = RecordedLLM()
        self.assertEqual(group_change(self.change, [], llm).relation, 'new')
        self.assertEqual(llm.calls, [])
        candidates = [self.event.model_copy(update={'event_id': f'E{n}'}) for n in range(12)]
        self.assertEqual(len(retrieve_candidates(self.change, candidates)), 12)

    def test_ambiguity_separates_and_invented_identity_rejected(self):
        llm = RecordedLLM(lambda stage, ctx: grouping(event_id='E1', confidence='uncertain', relation='material'))
        result = group_change(self.change, [self.event], llm)
        self.assertEqual(result.relation, 'uncertain')
        self.assertIsNone(result.event_id)
        llm = RecordedLLM(lambda stage, ctx: grouping(event_id='invented', confidence='high', relation='material'))
        with self.assertRaisesRegex(ValueError, 'outside supplied'):
            group_change(self.change, [self.event], llm)

    def test_duplicate_requires_supplied_id_and_matching_claim_qualification(self):
        llm = RecordedLLM(lambda stage, ctx: grouping(event_id='E1', confidence='high', relation='duplicate',
                                            duplicate_of=self.change.change_id))
        result = group_change(self.change, [self.event], llm)
        self.assertEqual((result.event_id, result.duplicate_of), ('E1', self.change.change_id))
        proposed = self.change.model_copy(update={'claim_type': 'proposed'})
        with self.assertRaisesRegex(ValueError, 'claim type'):
            group_change(proposed, [self.event], llm)

    def test_analysis_calculates_total_and_one_schema_repair(self):
        llm = RecordedLLM()
        event = analyze_event(self.event, llm, NOW.isoformat())
        self.assertEqual(event.score.total, 67)
        self.assertEqual(len(llm.calls), 1)
        broken = RecordedLLM(lambda stage, ctx: analysis_reply(ctx,impact=6,reasoning={}))
        with self.assertRaises(ValueError):
            analyze_event(self.event, broken, NOW.isoformat())
        self.assertEqual(len(broken.calls), 2)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        from news_engine.storage import Store
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'mvp.sqlite'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def run_news(self, llm=None, inputs=None, **kwargs):
        from news_engine.pipeline import run_pipeline
        return run_pipeline(self.store, llm or RecordedLLM(), articles=inputs or [
            ArticleInput(url='https://example.org/news', title='News', text='Funding conditions improved.')], **kwargs)

    def test_valid_result_score_and_repeat_are_idempotent(self):
        llm = RecordedLLM()
        first = self.run_news(llm)
        self.assertEqual(first.status, 'completed')
        self.assertEqual(first.events[0].score.total, 67)
        calls = len(llm.calls)
        second = self.run_news(llm)
        self.assertEqual(second.status, 'completed')
        self.assertEqual(len(llm.calls), calls)
        self.assertEqual(second.events[0].event_id, first.events[0].event_id)
        self.assertEqual(second.events[0].what_changed[0].change_id, first.events[0].what_changed[0].change_id)
        self.assertEqual(second.metrics['articles_cached'], 1)

    def test_article_failure_isolated_and_complete_empty_counts_as_success(self):
        def handler(stage, ctx):
            if stage == 'extract' and ctx['title'] == 'Bad':
                raise RuntimeError('provider failure')
            if stage == 'extract' and ctx['title'] == 'Empty':
                return {'complete': True, 'changes': []}
        inputs = [ArticleInput(url=f'https://example.org/{title}', title=title, text=f'{title} article news.')
                  for title in ('Good', 'Bad', 'Empty')]
        result = self.run_news(RecordedLLM(handler), inputs)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.metrics['articles_failed'], 1)
        self.assertTrue(result.errors)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(len(result.events[0].what_changed), 1)

    def test_all_failed_and_empty_success_are_distinct(self):
        broken = RecordedLLM(lambda stage, ctx: {'complete': False, 'changes': []})
        self.assertEqual(self.run_news(broken).status, 'failed')
        valid = RecordedLLM(lambda stage, ctx: {'complete': True, 'changes': []})
        result = self.run_news(valid)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.events, [])

    def test_bounded_extraction_workers(self):
        lock = threading.Lock()
        active = peak = 0
        def handler(stage, ctx):
            nonlocal active, peak
            if stage == 'extract':
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.03)
                with lock:
                    active -= 1
        inputs = [ArticleInput(url=f'https://example.org/{n}', title=f'Item {n}',
                              text=f'Development number {n} happened.') for n in range(6)]
        result = self.run_news(RecordedLLM(handler), inputs, workers=2)
        self.assertEqual(result.status, 'completed')
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 2)

    def test_url_and_content_deduplication(self):
        llm = RecordedLLM()
        inputs = [ArticleInput(url='https://example.org/story?utm_source=x#top', title='One',
                               text='Funding conditions improved.'),
                  ArticleInput(url='https://example.org/story', title='One', text='Funding conditions improved.'),
                  ArticleInput(url='https://another.example.org/story', title='Syndicated',
                               text='Funding  conditions\nimproved.')]
        result = self.run_news(llm, inputs)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(sum(stage == 'extract' for stage, _ in llm.calls), 1)
        self.assertEqual(len(result.events), 1)

    def test_failed_search_does_not_advance_history(self):
        from news_engine.search import search
        plan = RecordedLLM(lambda stage, ctx: {'queries': ['rates', 'funding']})
        class Exa:
            def search(self, query, *, start, end):
                if query == 'rates':
                    raise RuntimeError('unavailable')
                return {'results': []}
        _, errors = search(plan, Exa(), self.store, max_queries=2, now=NOW)
        self.assertEqual(len(errors), 1)
        self.assertIsNone(self.store.search_since('rates'))
        self.assertEqual(self.store.search_since('funding'), NOW.isoformat())

    def test_invalid_source_keeps_search_watermark_unchanged(self):
        from news_engine.search import search
        class Exa:
            def search(self, query, *, start, end):
                return {'results': [{'url': 'https://example.org/missing-text', 'title': 'Unreadable'}]}
        _, errors = search(RecordedLLM(lambda stage, ctx: {'queries': ['rates']}),
                           Exa(), self.store, max_queries=1, now=NOW)
        self.assertEqual(len(errors), 1)
        self.assertIsNone(self.store.search_since('rates'))

    def test_legacy_database_is_refused_without_writes(self):
        from news_engine.storage import Store
        legacy = Path(self.temporary.name) / 'legacy.sqlite'
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute('CREATE TABLE user_data (value TEXT)')
            connection.execute("INSERT INTO user_data VALUES ('preserve')")
            connection.commit()
        prior = legacy.read_bytes()
        with self.assertRaises((ValueError, RuntimeError)):
            Store(legacy)
        self.assertEqual(legacy.read_bytes(), prior)

    def test_six_runs_track_identity_duplicate_restart_distinct_and_uncertain(self):
        from news_engine.storage import Store
        mode = 'material'
        def handler(stage, ctx):
            if stage != 'group':
                return None
            candidate = ctx['candidates'][0]
            if mode == 'new':
                return grouping()
            if mode == 'uncertain':
                return grouping(confidence='uncertain', relation='uncertain')
            if mode == 'duplicate':
                return grouping(event_id=candidate['event_id'], confidence='high', relation='duplicate',
                    duplicate_of=candidate['changes'][-1]['change_id'])
            return grouping(event_id=candidate['event_id'], confidence='high', relation='material')
        llm = RecordedLLM(handler)
        def input_for(name, text, day):
            return [ArticleInput(url=f'https://example.org/{name}', title=name, text=text,
                                 published_at=f'2026-01-{day:02}T00:00:00Z')]
        first_input = input_for('announcement', 'The central bank liquidity facility was announced.', 1)
        first = self.run_news(llm, first_input)
        event_id = first.events[0].event_id
        created = first.events[0].created_at
        second_input = input_for('operation', 'The central bank liquidity facility became operational.', 2)
        second = self.run_news(llm, second_input)
        self.assertEqual(second.events[0].event_id, event_id)
        self.assertEqual(len(second.events[0].what_changed), 2)

        self.assertGreater(second.events[0].last_material_at, first.events[0].last_material_at)
        material_at = second.events[0].last_material_at
        mode = 'duplicate'
        analysis_calls = sum(stage == 'analyze' for stage, _ in llm.calls)
        third = self.run_news(llm, input_for('repeat',
            'The central bank liquidity facility became operational, according to another release.', 3))
        self.assertEqual(third.events[0].event_id, event_id)
        self.assertEqual(third.events[0].last_material_at, material_at)
        self.assertEqual(len(third.events[0].what_changed), 2)
        self.assertTrue(third.events[0].what_changed[-1].corroborating_sources)
        self.assertEqual(sum(stage == 'analyze' for stage, _ in llm.calls), analysis_calls)
        self.store.close()
        self.store = Store(self.path)
        fourth = self.run_news(llm, second_input)
        self.assertEqual(fourth.events[0].event_id, event_id)
        self.assertEqual(fourth.events[0].created_at, created)
        self.assertEqual(fourth.metrics['llm_calls'], 0)
        mode = 'new'
        fifth = self.run_news(llm, input_for('distinct',
            'The central bank liquidity facility for bond dealers was announced.', 4))
        self.assertNotEqual(fifth.events[0].event_id, event_id)
        self.assertEqual(len(self.store.events()), 2)
        mode = 'uncertain'
        sixth = self.run_news(llm, input_for('uncertain',
            'The central bank liquidity facility changed eligibility for participants.', 5))
        self.assertEqual(sixth.status, 'partial')
        self.assertTrue(any(error.stage == 'group' for error in sixth.errors))
        self.assertEqual(len(self.store.events()), 2)
        self.assertEqual(sum(change.event_id is None for change in self.store.changes()), 1)

    def test_association_transaction_rolls_back_and_unmatched_work_retries(self):
        self.store.db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'injected storage failure'); END")
        llm = RecordedLLM()
        first = self.run_news(llm)
        self.assertEqual(first.status, 'partial')
        self.assertEqual(self.store.events(), [])
        self.assertIsNone(self.store.changes()[0].event_id)
        extraction_calls = sum(stage == 'extract' for stage, _ in llm.calls)
        self.store.db.execute('DROP TRIGGER reject_event')
        second = self.run_news(llm)
        self.assertEqual(second.status, 'completed')
        self.assertEqual(len(second.events), 1)
        self.assertEqual(sum(stage == 'extract' for stage, _ in llm.calls), extraction_calls)

    def test_changed_event_never_exports_stale_score_after_analysis_failure(self):
        initial = self.run_news()
        def handler(stage, ctx):
            if stage == 'group':
                return grouping(event_id=ctx['candidates'][0]['event_id'], confidence='high', relation='material')
            if stage == 'analyze':
                raise RuntimeError('provider unavailable')
        update = [ArticleInput(url='https://example.org/update', title='Update',
                               text='Funding conditions improved further.')]
        result = self.run_news(RecordedLLM(handler), update)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.events[0].event_id, initial.events[0].event_id)
        self.assertIsNone(result.events[0].score)
        self.assertEqual(result.events[0].why_it_matters, [])
        self.assertEqual(result.events[0].what_to_do, [])
        self.assertIsNone(self.store.events()[0].score)


class CorpusTests(unittest.TestCase):
    def test_all_authentic_sources_and_frozen_labels_intact(self):
        from evaluation.real_corpus import validate_corpus, load_sources, load_scenario
        result = validate_corpus()
        self.assertEqual(result['sources'], 72)
        self.assertEqual(result['event_occurrences'], 23)
        self.assertEqual(len(load_scenario('smoke')), 4)
        self.assertEqual(len(load_scenario('full')), 72)
        for row in load_sources().values():
            self.assertNotIn('gold', row)
            self.assertNotIn('w2', row)

    def test_missing_predictions_remain_in_pair_recall_denominator(self):
        from evaluation.mvp import grouping_metrics
        ids = ['real-7645ea4d8dda', 'real-50339f5e620a']
        result = grouping_metrics({}, ids)
        self.assertEqual(result['false_split_or_missing'], 1)
        self.assertEqual(result['pair_recall'], 0)


class HTTPTests(unittest.TestCase):
    def test_authenticated_json_roundtrip_persists_across_app_restart(self):
        from fastapi.testclient import TestClient
        from news_engine.api import create_app
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / 'http.sqlite'
            llm = RecordedLLM()
            app = create_app(database=database, token='test-token', llm=llm)
            headers = {'Authorization': 'Bearer test-token'}
            with TestClient(app) as client:
                self.assertEqual(client.get('/healthz').status_code, 200)
                self.assertEqual(client.post('/api/news/runs', json={}).status_code, 401)
                self.assertEqual(client.post('/api/news/runs', headers=headers,
                                            json={'workers': 99}).status_code, 422)
                self.assertEqual(client.post('/api/news/runs', headers=headers,
                                            json={'unknown_option': True}).status_code, 422)
                response = client.post('/api/news/runs', headers=headers, json={'articles': [
                    {'url': 'https://example.org/notice', 'title': 'Funding notice',
                     'text': 'Funding conditions improved.'}]})
                self.assertEqual(response.status_code, 200, response.text)
                saved = response.json()
                self.assertEqual(saved['status'], 'completed')
                self.assertEqual(saved['events'][0]['score']['total'], 67)
            with TestClient(create_app(database=database, token='test-token', llm=RecordedLLM())) as client:
                response = client.get('/api/news/runs/' + saved['run_id'], headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), saved)
                self.assertEqual(client.get('/api/news/runs/missing', headers=headers).status_code, 404)

    def test_second_process_writer_lock_is_busy(self):
        from news_engine.storage import BusyError, Store
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'lock.sqlite'
            with Store(path) as left, Store(path) as right:
                with left.writer_lock():
                    with self.assertRaises(BusyError):
                        with right.writer_lock():
                            self.fail('Second writer must not enter')


class ProviderTests(unittest.TestCase):
    def test_llm_structured_transport_receipts_and_credit_stop(self):
        import httpx
        from news_engine.clients import LLMClient
        requests = []
        def responder(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                'message': {'content': '{"complete":true,"changes":[]}'}}],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 4, 'cost': 0.01}})
        with httpx.Client(transport=httpx.MockTransport(responder)) as transport:
            client = LLMClient('key', 'model', 'https://example.org/v1', http_client=transport)
            result = client.complete(ExtractionDraft, 'prompt', {'text': 'article'}, stage='extract')
            self.assertTrue(result.complete)
            self.assertEqual(requests[0]['response_format']['type'], 'json_schema')
            self.assertEqual(client.receipts[0]['cost_usd'], 0.01)
            self.assertEqual(client.receipts[0]['prompt_tokens'], 7)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(402))) as transport:
            client = LLMClient('key', 'model', 'https://example.org/v1', http_client=transport)
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    client.complete(ExtractionDraft, 'prompt', {}, stage='extract')
            self.assertEqual(len(client.receipts), 1)

    def test_malformed_output_retains_usage_and_failed_receipt(self):
        import httpx
        from news_engine.clients import LLMClient
        def responder(request):
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                'message': {'content': '{"complete":true,"changes":[],"unknown":true}'}}],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 3}})
        with httpx.Client(transport=httpx.MockTransport(responder)) as transport:
            client = LLMClient('secret-key', 'model', 'https://example.org/v1', http_client=transport)
            with self.assertRaises(ValueError) as caught:
                client.complete(ExtractionDraft, 'prompt', {}, stage='extract')
            self.assertNotIn('secret-key', str(caught.exception))
            self.assertEqual(len(client.receipts), 1)
            self.assertEqual(client.receipts[0]['status'], 'failed')
            self.assertEqual(client.receipts[0]['prompt_tokens'], 8)
            self.assertNotIn('cost_usd', client.receipts[0])

    def test_environment_alias_overrides_local_primary_setting(self):
        from news_engine.config import load_credentials
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / '.env'
            env_file.write_text('OPENROUTER_API_KEY=local-key\nLLM_MODEL=local-model\n', encoding='utf8')
            credentials = load_credentials(env_file, require_exa=False,
                environ={'LLM_API_KEY': 'environment-key', 'OPENROUTER_MODEL': 'environment-model'})
            self.assertEqual(credentials.llm_api_key, 'environment-key')
            self.assertEqual(credentials.llm_model, 'environment-model')
            self.assertNotIn('environment-key', repr(credentials))


if __name__ == '__main__':
    unittest.main()
