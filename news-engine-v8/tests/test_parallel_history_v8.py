"""Historical workers overlap; SQLite writes remain caller-owned."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock, get_ident
import unittest
from unittest.mock import patch
from evaluation.offline_classification import ScriptedLabels
from news_engine.models import ArticleInput, Event
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store

class ParallelHistoryTests(unittest.TestCase):
    def test_same_historical_event_keeps_both_parallel_updates(self):
        class MatchingLabels(ScriptedLabels):
            def __init__(self):
                super().__init__()
                self.barrier=Barrier(2);self.lock=Lock()
                self.active=self.peak=0;self.threads=set()
            def complete(self,schema,prompt,context,*,stage):
                if stage=='group_identity' and context.get('candidate_kind')=='historical_event':
                    with self.lock:
                        self.active+=1;self.peak=max(self.peak,self.active);self.threads.add(get_ident())
                    try:
                        self.barrier.wait(timeout=5)
                        return schema.model_validate(dict(decision='same',event_id='Ehistory',reason='Fixture historical match.'))
                    finally:
                        with self.lock:self.active-=1
                return super().complete(schema,prompt,context,stage=stage)
        llm=MatchingLabels();caller=get_ident()
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            original=store.add_article(ArticleInput(url='https://example.test/original',title='Earlier issue',text='Original historical Article.'))
            store.db.execute("UPDATE articles SET state='accepted' WHERE article_id=?",(original.article_id,));store.db.commit()
            store.save_event(Event(event_id='Ehistory',title='Earlier issue',summary='Historical context.',category='regulatory',article_ids=[original.article_id]))
            writes=[];save=store.save_association
            def record(keys,event):
                writes.append(get_ident());self.assertEqual(get_ident(),caller)
                return save(keys,event)
            inputs=[dict(url='https://example.test/'+issue,title=issue+' | update',text=issue+' original unique report.') for issue in ['Alpha','Bravo']]
            with patch.object(store,'save_association',side_effect=record):
                result=run_pipeline(store,llm,articles=inputs,workers=2)
            self.assertEqual(result.status,'completed',result.errors)
            self.assertEqual(llm.peak,2);self.assertNotIn(caller,llm.threads)
            self.assertEqual(writes,[caller,caller]);self.assertEqual(len(result.events),1)
            self.assertEqual(result.events[0].event_id,'Ehistory');self.assertEqual(len(result.events[0].article_ids),3)
            self.assertEqual(set(store.assignments().values()),{'Ehistory'})
            self.assertEqual(result.metrics['association_workers'],2);self.assertFalse(store.pending_proposals())

    def test_repaired_id_diagnostic_is_saved_with_run_metrics(self):
        import json
        import httpx
        from news_engine.clients import LLMClient
        from evaluation.offline_classification import analysis_reply
        retrievals=[]
        def respond(request):
            payload=json.loads(request.content)
            context=json.loads(payload['messages'][1]['content'])
            schema_name=payload['response_format']['json_schema']['name']
            if 'sources' in context:
                value=dict(complete=True,events=[dict(issue_id='P1',title='Facility update',summary='A facility update.',category='regulatory')],
                    assignments={a['article_id']:'P1' for a in context['sources']})
            elif schema_name=='RetrievalDraft':
                retrievals.append(context)
                value=dict(event_ids=['Eseed0','Eseed0'] if len(retrievals)==1 else ['Eseed0'])
            elif schema_name=='AssociationDraft':
                value=dict(decision='same',event_id='Eseed0',reason='Same issue fixture.')
            else:
                value=dict(results=[dict(event_id=e['current_event']['event_id'],analysis=analysis_reply(e['current_event'])) for e in context['events']])
            return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(value)))],usage=dict(prompt_tokens=5,completion_tokens=5,cost=.001)))
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store, httpx.Client(transport=httpx.MockTransport(respond)) as http:
            for i in range(6):
                source=store.add_article(ArticleInput(url=f'https://example.test/seed/{i}',title=f'Issue {i}',text=f'Original issue {i}.'))
                store.db.execute("UPDATE articles SET state='accepted' WHERE article_id=?",(source.article_id,));store.db.commit()
                store.save_event(Event(event_id=f'Eseed{i}',title=f'Issue {i}',summary='Historical context.',category='regulatory',article_ids=[source.article_id]))
            llm=LLMClient('offline','mock','https://example.test/v1',http_client=http)
            result=run_pipeline(store,llm,articles=[dict(url='https://example.test/new',title='Facility update',text='A new facility report.')])
            self.assertEqual(result.status,'completed',result.errors)
            self.assertEqual(len(retrievals),2)
            diagnostics=result.metrics['id_validation_errors']
            self.assertEqual(len(diagnostics),1)
            self.assertEqual(diagnostics[0]['duplicate_ids'],['Eseed0'])
            self.assertEqual(store.get_run(result.run_id).metrics['id_validation_errors'],diagnostics)
            self.assertEqual(len(llm.receipts),6)  # Four W3 calls and two W4 batches; the audit adds no calls.
