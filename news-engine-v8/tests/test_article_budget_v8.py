"""Bounded user-facing replay selects work without losing deferred originals."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from evaluation.offline_classification import ScriptedLabels
from news_engine.models import ArticleInput, Event
from news_engine.group_first import StoredProposal, EventProposal
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store


def source(index, issue=None):
    return dict(url=f'https://example.test/article/{index}',title=(issue or f'Issue {index}')+' | report',
                text=f'Original Article text {index}.',published_at='2026-01-01T00:00:00Z')


class ArticleBudgetTests(unittest.TestCase):
    def test_new_plus_resumed_limit_and_cached_inputs_do_not_starve_backlog(self):
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            llm=ScriptedLabels();inputs=[source(i) for i in range(23)]
            first=run_pipeline(store,llm,articles=inputs,max_articles=10)
            self.assertEqual(first.status,'completed',first.errors)
            self.assertEqual((first.metrics['articles_selected'],first.metrics['articles_attempted'],first.metrics['articles_deferred']),(10,10,13))
            self.assertEqual(len(store.articles()),23)
            first_ids={a.article_id for a in first.sources}
            second=run_pipeline(store,llm,articles=inputs,max_articles=10)
            self.assertEqual(second.status,'completed',second.errors)
            self.assertEqual((second.metrics['articles_selected'],second.metrics['articles_deferred']),(10,3))
            self.assertFalse(first_ids & {a.article_id for a in second.sources})
            third=run_pipeline(store,llm,articles=[],max_articles=10)
            self.assertEqual((third.metrics['articles_selected'],third.metrics['articles_deferred']),(3,0))
            self.assertEqual(len(store.assignments()),23)
            self.assertFalse(store.pending_proposals())

    def test_pending_proposal_is_deferred_as_one_unit(self):
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            articles=[store.add_article(ArticleInput(**source(i))) for i in range(4)]
            ids=[a.article_id for a in articles[:3]]
            proposal=StoredProposal(proposal_key='atomic',event=EventProposal(title='Atomic issue',summary='One pending issue.',category='regulatory',article_ids=ids))
            store.save_group_proposals(ids,[proposal])
            llm=ScriptedLabels()
            first=run_pipeline(store,llm,articles=[],max_articles=2)
            self.assertEqual(first.status,'completed',first.errors)
            self.assertEqual(first.metrics['articles_selected'],1)
            self.assertEqual(first.metrics['articles_deferred'],3)
            self.assertEqual(first.metrics['budget_blocked_units'][0]['article_count'],3)
            self.assertEqual(store.pending_proposals()[0].proposal_key,'atomic')
            self.assertFalse(set(ids) & store.assignments().keys())
            second=run_pipeline(store,llm,articles=[],max_articles=3)
            self.assertEqual(second.status,'completed',second.errors)
            self.assertEqual(second.metrics['articles_selected'],3)
            self.assertEqual(len(store.assignments()),4)
            self.assertFalse(store.pending_proposals())

    def test_oversized_unscored_event_is_not_partially_resumed(self):
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            ids=[]
            for i in range(11):
                a=store.add_article(ArticleInput(**source(i)))
                ids.append(a.article_id)
                store.db.execute("UPDATE articles SET state='accepted' WHERE article_id=?",(a.article_id,))
            store.db.commit()
            store.save_event(Event(event_id='Eold',title='Earlier issue',summary='Earlier unscored issue.',category='regulatory',article_ids=ids))
            llm=ScriptedLabels()
            first=run_pipeline(store,llm,articles=[],max_articles=10)
            self.assertEqual(first.status,'completed',first.errors)
            self.assertEqual(first.metrics['articles_selected'],0)
            self.assertEqual(first.metrics['articles_deferred'],11)
            self.assertEqual(first.metrics['budget_blocked_units'][0]['id'],'Eold')
            self.assertFalse(llm.calls)
            second=run_pipeline(store,llm,articles=[],max_articles=11)
            self.assertEqual(second.status,'completed',second.errors)
            self.assertIsNotNone(second.events[0].score)
            self.assertEqual(second.metrics['analysis_context_articles'],11)

    def test_new_article_limit_does_not_truncate_matched_historical_evidence(self):
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            llm=ScriptedLabels()
            initial=run_pipeline(store,llm,articles=[source(i,'Facility') for i in range(11)])
            followup=run_pipeline(store,llm,articles=[source(12,'Facility')],max_articles=1)
            self.assertEqual(followup.status,'completed',followup.errors)
            self.assertEqual(followup.metrics['articles_selected'],1)
            self.assertEqual(followup.metrics['analysis_context_articles'],12)
            self.assertEqual(followup.events[0].event_id,initial.events[0].event_id)
            self.assertEqual(len(followup.events[0].article_ids),12)

    def test_matched_historical_context_clears_its_initially_deferred_backlog(self):
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            ids=[]
            for i in range(11):
                a=store.add_article(ArticleInput(**source(i,'Facility')))
                ids.append(a.article_id)
                store.db.execute("UPDATE articles SET state='accepted' WHERE article_id=?",(a.article_id,))
            store.db.commit()
            store.save_event(Event(event_id='Eold',title='Facility',summary='Earlier unscored facility.',category='regulatory',article_ids=ids))
            result=run_pipeline(store,ScriptedLabels(),articles=[source(12,'Facility')],max_articles=1)
            self.assertEqual(result.status,'completed',result.errors)
            self.assertEqual(result.metrics['articles_selected'],1)
            self.assertEqual(result.metrics['analysis_context_articles'],12)
            self.assertEqual(result.metrics['articles_deferred'],0)
            self.assertEqual(result.metrics['budget_blocked_units'],[])

    def test_invalid_budgets_fail_before_work(self):
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            for value in [0,-1,True,1.5,'10']:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    run_pipeline(store,ScriptedLabels(),articles=[],max_articles=value)
            self.assertEqual(store.db.execute('SELECT count(*) FROM runs').fetchone()[0],0)
