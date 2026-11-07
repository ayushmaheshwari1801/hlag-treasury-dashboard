"""Contract 8 association integrity, evaluated cardinality and atomic restart checks."""
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from news_engine.models import ArticleInput, Event, RunResult, Score, validate_assignments
from news_engine.storage import Store


class StorageV8Tests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'v8.sqlite'
        self.store=Store(self.path)
        self.articles=[self.store.add_article(ArticleInput(url=f'https://example.org/{index}',title=f'Article {index}',text=f'Original immutable source {index}')) for index in range(4)]

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def event(self,event_id,indices):
        return Event(event_id=event_id,title='Issue',summary='Source-supported issue',category='regulatory',article_ids=[self.articles[i].article_id for i in indices])

    def test_unique_association_is_immutable_across_restart(self):
        first=self.event('E001',[0,1]);self.store.save_event(first)
        self.store.close();self.store=Store(self.path)
        with self.assertRaisesRegex(ValueError,'another Event'):
            self.store.save_event(self.event('E002',[1,2]))
        self.assertEqual(self.store.assignments(),dict.fromkeys(first.article_ids,'E001'))
        self.assertEqual([e.event_id for e in self.store.events()],['E001'])

    def test_cannot_remove_existing_articles_or_change_category(self):
        self.store.save_event(self.event('E001',[0,1]))
        with self.assertRaises(ValueError):self.store.save_event(self.event('E001',[0]))
        changed=self.event('E001',[0,1]);changed.category='industry'
        with self.assertRaises(ValueError):self.store.save_event(changed)
        self.assertEqual(len(self.store.assignments()),2)

    def test_unknown_article_fails_without_partial_event(self):
        event=self.event('E001',[0]);event.article_ids.append('Aunknown')
        with self.assertRaises(ValueError):self.store.save_event(event)
        self.assertEqual(self.store.events(),[])
        self.assertEqual(self.store.assignments(),{})

    def test_current_evaluated_count_excludes_unrelated_history(self):
        events=[self.event('E001',[0,1]),self.event('E002',[2]),self.event('E003',[3])]
        counts=validate_assignments(events,[self.articles[0].article_id])
        self.assertEqual(counts,dict(unique_relevant_articles=1,unique_events=1))
        with self.assertRaises(ValueError):validate_assignments(events,['Amissing'])
        with self.assertRaises(ValueError):validate_assignments([events[0],self.event('E004',[0])],[self.articles[0].article_id])

    def test_run_contract_requires_original_sources(self):
        event=self.event('E001',[0,1])
        with self.assertRaises(ValueError):RunResult(run_id='R1',status='completed',events=[event],sources=[self.articles[0]])
        result=RunResult(run_id='R1',status='completed',events=[event],sources=self.articles[:2])
        self.assertEqual(result.schema_version,8)
        self.assertEqual(set(event.model_dump()),{'event_id','title','category','article_ids','summary','score','why_it_matters','what_to_do'})

    def test_refuses_historical_database_without_modifying_it(self):
        old=Path(self.directory.name)/'old.sqlite'
        db=sqlite3.connect(old);db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        db.execute("INSERT INTO metadata VALUES('schema','news-engine-mvp-7')");db.commit();db.close()
        original=old.read_bytes()
        with self.assertRaises(ValueError):Store(old)
        self.assertEqual(old.read_bytes(),original)

    def test_atomic_group_and_all_proposal_consumption(self):
        from news_engine.group_first import EventProposal, StoredProposal
        def proposal(key,index):
            return StoredProposal(proposal_key=key,event=EventProposal(title='Issue',summary='Source issue',category='regulatory',article_ids=[self.articles[index].article_id]))
        proposals=[proposal('one',0),proposal('two',1)]
        self.store.save_group_proposals([a.article_id for a in self.articles[:2]],proposals)
        with self.assertRaises(ValueError):self.store.save_association(['one','two'],self.event('E001',[0]))
        self.assertEqual(len(self.store.pending_proposals()),2)
        self.assertEqual(self.store.events(),[])
        self.store.save_association(['one','two'],self.event('E001',[0,1]))
        self.assertEqual(self.store.pending_proposals(),[])
        self.assertEqual(len(self.store.assignments()),2)
        with self.assertRaises(KeyError):self.store.save_association(['one'],self.event('E001',[0,1]))

    def test_group_rejects_duplicate_assignments_without_saving(self):
        from news_engine.group_first import EventProposal, StoredProposal
        ids=[a.article_id for a in self.articles[:2]]
        duplicate=[StoredProposal(proposal_key=str(i),event=EventProposal(title='Issue',summary='Issue summary',category='industry',article_ids=ids)) for i in range(2)]
        with self.assertRaises(ValueError):self.store.save_group_proposals(ids,duplicate)
        self.assertEqual(self.store.pending_proposals(),[])
        self.assertEqual(self.store.classification_status(ids[0]),'pending')

    def test_strict_r5(self):
        score=Score(impact=3,relevance=4,urgency=3,total=67,reasoning={'impact':'Supported impact','relevance':'Conditional channel','urgency':'Monitor timing'})
        self.assertEqual(score.total,67)
        for update in ({'impact':True},{'impact':3.0},{'total':66},{'reasoning':{'impact':'','relevance':'x','urgency':'x'}}):
            with self.assertRaises(ValueError):Score.model_validate({**score.model_dump(),**update})

if __name__=='__main__':unittest.main()
