"""Contract 3: persistent issues, Change qualifications, no Event lifecycle."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from pydantic import ValidationError
from news_engine.events import NoveltyDraft, GroupingDraft
from news_engine.models import Event,RunResult
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from test_mvp import RecordedLLM,draft,grouping

class IssueTests(unittest.TestCase):
    def test_removed_fields_are_rejected_in_all_contracts(self):
        self.assertNotIn('status',Event.model_fields)
        for schema in (NoveltyDraft,GroupingDraft):
            for field in ('status','completion_quote','completion_reliable'):
                self.assertNotIn(field,schema.model_fields)
        with self.assertRaises(ValidationError):
            NoveltyDraft(relation='material',reason='New scope',status='resolved')
        sample=RunResult.model_validate_json(Path('examples/event-result.json').read_text())
        self.assertEqual(sample.schema_version,4)
        legacy=sample.model_dump()
        legacy['events'][0]['status']='ongoing'
        with self.assertRaises(ValidationError):RunResult.model_validate(legacy)
        self.assertEqual(RunResult(run_id='R',status='completed').schema_version,4)
        with self.assertRaises(ValidationError):RunResult(run_id='R',status='completed',schema_version=2)

    def test_contract_two_database_is_untouched_and_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.sqlite'
            db=sqlite3.connect(path)
            db.execute('create table metadata(key text primary key,value text)')
            db.execute("insert into metadata values('schema','news-engine-mvp-2')")
            db.commit();db.close();before=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'fresh database'):Store(path)
            self.assertEqual(path.read_bytes(),before)

    def test_related_actors_share_persistent_issue_and_keep_claims(self):
        def handler(stage,ctx):
            if stage=='extract':
                return {'complete':True,'changes':[draft(ctx['text'],claim_type='announced' if 'Board' in ctx['text'] else 'actual',
                    identity=dict(subject='Board' if 'Board' in ctx['text'] else 'SBA',action='rule clarification',
                        occurrence='PPP insider lending issue',scope='PPP insider eligibility'))]}
            if stage=='group':
                if 'uncertain' in ctx['change']['description']:return grouping(confidence='uncertain',relation='uncertain')
                return grouping(event_id=ctx['candidates'][0]['event_id'],confidence='high',relation='material')
        llm=RecordedLLM(handler)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new.sqlite'
            with Store(path) as store:
                first=run_pipeline(store,llm,[dict(url='https://example.org/sba',title='SBA',text='SBA clarified PPP insider eligibility.')])
                key=first.events[0].event_id
            with Store(path) as store:
                second=run_pipeline(store,llm,[dict(url='https://example.org/board',title='Board',text='Board announced PPP insider eligibility restrictions.')])
                self.assertEqual(second.status,'completed')
                self.assertEqual(len(store.events()),1)
                self.assertEqual(second.events[0].event_id,key)
                self.assertEqual({c.claim_type for c in second.events[0].what_changed},{'actual','announced'})
                self.assertEqual(sum(stage=='analyze' for stage,_ in llm.calls),2)
                self.assertNotIn('status',second.events[0].model_dump())
                self.assertNotIn('status',store.db.execute('select data from events').fetchone()[0])
                third=run_pipeline(store,llm,[dict(url='https://example.org/u',title='Uncertain',text='SBA uncertain PPP insider eligibility.')])
                self.assertEqual(third.status,'partial')
                self.assertTrue(any(c.event_id is None for c in store.changes()))
