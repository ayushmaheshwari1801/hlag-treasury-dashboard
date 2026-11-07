"""Empty local definitions do not invalidate a correct Article partition."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from evaluation.offline_classification import ScriptedLabels
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store

class UnusedDraftWarningTests(unittest.TestCase):
    def test_warning_only_run_completes_and_cached_warning_stays_nonfatal(self):
        class ExtraDefinitions(ScriptedLabels):
            def complete(self,schema,prompt,context,*,stage):
                response=super().complete(schema,prompt,context,stage=stage)
                if stage=='classify':
                    payload=response.model_dump()
                    payload['events'].extend([{**payload['events'][0],'issue_id':key} for key in ['P2','P3']])
                    return schema.model_validate(payload)
                return response
        llm=ExtraDefinitions()
        inputs=[dict(url=f'https://example.test/facility/{i}',title='Facility | announcement',text=f'Facility announcement liquidity policy report {i}.') for i in range(3)]
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            result=run_pipeline(store,llm,articles=inputs,max_articles=10)
            self.assertEqual(result.status,'completed',result.errors)
            self.assertEqual(result.errors,[])
            self.assertEqual(len(result.events),1)
            self.assertEqual(len(result.events[0].article_ids),3)
            self.assertEqual(set(store.assignments().values()),{result.events[0].event_id})
            warning=result.metrics['warnings']
            self.assertEqual(len(warning),1)
            self.assertEqual(warning[0]['code'],'unused_issue_drafts_removed')
            detail=json.loads(warning[0]['message'])
            self.assertEqual(detail['removed_issue_ids'],['P2','P3'])
            self.assertEqual(set(detail['article_ids']),set(result.events[0].article_ids))
            self.assertEqual(store.get_run(result.run_id).metrics['warnings'],warning)
            calls=len(llm.calls)
            cached=run_pipeline(store,llm,articles=inputs,max_articles=10)
            self.assertEqual(cached.status,'completed',cached.errors)
            self.assertEqual(cached.errors,[])
            self.assertEqual(cached.metrics['warnings'],warning)
            self.assertEqual(len(llm.calls),calls)

    def test_all_irrelevant_with_unused_definition_is_success_without_event(self):
        class IrrelevantWithDraft(ScriptedLabels):
            def complete(self,schema,prompt,context,*,stage):
                self.calls.append((stage,context))
                self.assert_stage=stage
                return schema.model_validate(dict(complete=True,
                    events=[dict(issue_id='P1',title='Unneeded draft',summary='No assigned source.',category='industry')],
                    assignments={source['article_id']:None for source in context['sources']}))
        llm=IrrelevantWithDraft()
        with TemporaryDirectory() as folder, Store(Path(folder)/'news.sqlite') as store:
            result=run_pipeline(store,llm,articles=[dict(url='https://example.test/unrelated',title='Unrelated',text='Unrelated original Article.')])
            self.assertEqual(result.status,'completed',result.errors)
            self.assertEqual(result.events,[])
            self.assertEqual(result.errors,[])
            self.assertEqual(len(result.metrics['warnings']),1)
            self.assertEqual([stage for stage,_ in llm.calls],['classify'])
            self.assertEqual(store.classification_status(store.articles()[0].article_id),'irrelevant')
