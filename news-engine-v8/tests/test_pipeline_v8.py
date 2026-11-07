"""End-to-end offline contracts with explicit fixed replies, never model accuracy."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
from fastapi.testclient import TestClient
from evaluation.offline_classification import ScriptedLabels, replay
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store, BusyError
from news_engine.api import create_app


def article(issue, n, day=0):
    return dict(url=f'https://example.com/{issue}/{n}', title=issue+' | report',
        text=f'{issue} policy financing liquidity operating announcement report {n}.',
        published_at=(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=day)).isoformat())


class PipelineV8Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'v8.sqlite'
        self.store=Store(self.path); self.llm=ScriptedLabels()
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def run_news(self, articles, **kwargs):
        return run_pipeline(self.store,self.llm,articles=articles,**kwargs)
    def test_cardinality_scenarios(self):
        for labels, expected in ((['facility']*4,1),(['facility']*2+['capital']*2,2),
                (['facility','capital','rates','port'],4)):
            with self.subTest(labels=labels), tempfile.TemporaryDirectory() as folder, Store(Path(folder)/'v8.sqlite') as store:
                result=run_pipeline(store,ScriptedLabels(),articles=[article(label,n) for n,label in enumerate(labels)])
                self.assertEqual(result.status,'completed',result.errors)
                self.assertEqual((len(result.sources),len(result.events)),(4,expected))
                self.assertEqual(result.metrics['evaluated_event_count'],expected)
                self.assertEqual(result.metrics['unique_relevant_articles'],4)
    def test_cross_window_consolidation_and_historical_update(self):
        first=self.run_news([article('facility',0),article('facility',1,day=30)])
        self.assertEqual(len(first.events),1);self.assertEqual(first.metrics['article_groups'],2)
        event_id=first.events[0].event_id
        second=self.run_news([article('facility',2,day=60)])
        self.assertEqual(second.status,'completed',second.errors)
        self.assertEqual(second.events[0].event_id,event_id)
        self.assertEqual(len(second.events[0].article_ids),3)
        self.assertEqual(second.metrics['unique_relevant_articles'],1)
        self.assertEqual(second.metrics['unique_events'],1)
        source_context=[c for stage,c in self.llm.calls if stage=='analyze_batch'][-1]
        self.assertEqual(len(source_context['events'][0]['articles']),3)
    def test_cached_duplicate_restart_does_not_rescore(self):
        raw=article('facility',0);first=self.run_news([raw]);calls=len(self.llm.calls)
        self.store.close();self.store=Store(self.path)
        second=self.run_news([raw,raw])
        self.assertEqual(second.status,'completed')
        self.assertEqual(second.events,first.events);self.assertEqual(len(self.llm.calls),calls)
        self.assertEqual(second.metrics['unique_relevant_articles'],1)
    def test_unrelated_history_not_emitted_or_counted(self):
        self.run_news([article('facility',0),article('capital',1)])
        second=self.run_news([article('rates',2)])
        self.assertEqual(len(self.store.events()),3)
        self.assertEqual(len(second.events),1)
        self.assertEqual(second.metrics['unique_events'],1)
    def test_classification_failure_explicit_and_retryable(self):
        self.llm.fail_stage='classify';failed=self.run_news([article('facility',0)])
        self.assertEqual(failed.status,'failed');self.assertTrue(failed.errors)
        self.llm.fail_stage=None;success=self.run_news([])
        self.assertEqual(success.status,'completed');self.assertEqual(len(success.events),1)
    def test_pending_identity_retry_with_empty_input(self):
        self.run_news([article('facility',0)])
        self.llm.fail_stage='group_identity';failed=self.run_news([article('facility',1)])
        self.assertEqual(failed.status,'partial');self.assertTrue(self.store.pending_proposals())
        self.llm.fail_stage=None;recovered=self.run_news([])
        self.assertEqual(recovered.status,'completed',recovered.errors)
        self.assertEqual(recovered.metrics['unique_relevant_articles'],1)
        self.assertEqual(recovered.metrics['unique_events'],1)
        self.assertEqual(len(recovered.events[0].article_ids),2)
        self.assertFalse(self.store.pending_proposals())
    def test_failed_analysis_retry_with_empty_input(self):
        self.llm.fail_stage='analyze_batch';failed=self.run_news([article('facility',0)])
        self.assertEqual(failed.status,'partial');self.assertIsNone(failed.events[0].score)
        self.llm.fail_stage=None;recovered=self.run_news([])
        self.assertEqual(recovered.status,'completed');self.assertIsNotNone(recovered.events[0].score)
        self.assertEqual(recovered.events[0].event_id,failed.events[0].event_id)
    def test_atomic_failed_association_keeps_all_proposals(self):
        with patch.object(self.store,'_save_event',side_effect=RuntimeError('write failed')):
            failed=self.run_news([article('facility',0),article('facility',1,day=30)])
        self.assertTrue(failed.errors);self.assertEqual(len(self.store.pending_proposals()),2)
        self.assertFalse(self.store.events());self.assertFalse(self.store.assignments())
        recovered=self.run_news([])
        self.assertEqual(recovered.status,'completed',recovered.errors)
        self.assertEqual((len(recovered.events),recovered.metrics['unique_relevant_articles']),(1,2))
    def test_invalid_assignments_fail_without_truncating(self):
        original=self.llm.complete
        def duplicate(schema,prompt,context,*,stage):
            response=original(schema,prompt,context,stage=stage)
            if stage=='classify': response.events.append(response.events[0].model_copy())
            return response
        self.llm.complete=duplicate;result=self.run_news([article('facility',0)])
        self.assertEqual(result.status,'failed');self.assertFalse(result.events)
        self.assertFalse(self.store.assignments())
    def test_concrete_id_errors_persist_in_result_and_saved_run(self):
        def foreign_assignment(schema,prompt,context,*,stage):
            self.assertEqual(stage,'classify')
            return dict(complete=True,events=[],assignments={'Aforeign':None})
        self.llm.complete=foreign_assignment
        result=self.run_news([article('facility',0)])
        self.assertEqual(result.status,'failed')
        saved=self.store.get_run(result.run_id)
        self.assertEqual(saved.errors,result.errors)
        diagnostic=json.loads(result.errors[0].message.split(': ',1)[1])
        self.assertEqual(diagnostic['foreign_ids'],['Aforeign'])
        self.assertEqual(diagnostic['missing_ids'],[self.store.articles()[0].article_id])
        self.assertFalse(self.store.assignments())
    def test_irrelevant_articles_cached_without_events(self):
        calls=[]
        def irrelevant(schema,prompt,context,*,stage):
            calls.append(stage)
            return schema.model_validate(dict(complete=True,events=[],assignments={a['article_id']:None for a in context['sources']}))
        self.llm.complete=irrelevant;raw=article('irrelevant',0)
        for _ in range(2):
            result=self.run_news([raw]);self.assertEqual(result.status,'completed');self.assertFalse(result.events)
        self.assertEqual(calls,['classify'])
    def test_writer_lock_and_interrupted_run(self):
        with self.store.writer_lock(),Store(self.path) as other:
            with self.assertRaises(BusyError):
                with other.writer_lock(): pass
        self.store.start_run('Rinterrupted')
        self.run_news([])
        self.assertEqual(self.store.get_run('Rinterrupted').status,'failed')
    def test_http_auth_saved_contract_and_public_schema(self):
        app=create_app(database=self.path,token='test-token',llm=self.llm)
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/news/runs',json={'articles':[]}).status_code,401)
            response=client.post('/api/news/runs',json={'articles':[article('facility',0)]},headers={'Authorization':'Bearer test-token'})
            self.assertEqual(response.status_code,200,response.text);payload=response.json()
            self.assertEqual(payload['schema_version'],8)
            self.assertNotIn('what_changed',payload['events'][0]);self.assertIn('article_ids',payload['events'][0])
            saved=client.get('/api/news/runs/'+payload['run_id'],headers={'Authorization':'Bearer test-token'})
            self.assertEqual(saved.json(),payload)
    def test_authentic_frozen_corpus_scripted_contract_replay(self):
        report=replay()
        self.assertEqual(report['status'],'completed',report['errors'])
        self.assertTrue(report['cardinality_passed']);self.assertTrue(report['historical_identity_stable'])
        self.assertEqual(report['grouping']['false_merge'],0)
        self.assertEqual(report['grouping']['false_split_or_missing'],0)
