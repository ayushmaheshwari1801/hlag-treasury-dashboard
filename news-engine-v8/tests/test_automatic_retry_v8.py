"""Bounded automatic W4 recovery, exercised without provider calls."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_analysis_v8 import article, event, analysis, Scripted
from news_engine.analyze import analyze_batch, analysis_batches
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from evaluation.offline_classification import ScriptedLabels


def batch_reply(value, key='E001'):
    return {'results':[{'event_id':key,'analysis':value}]}


class AutomaticRetryTests(unittest.TestCase):
    def analyze(self, responses):
        current=event(); source=article()
        batches,errors=analysis_batches([current],{'A001':source},'2026-01-02')
        self.assertFalse(errors)
        llm=Scripted(responses)
        accepted,errors=analyze_batch([current],llm,batches[0][1])
        return current,source,llm,accepted,errors

    def test_first_repair_success_stops_automatically(self):
        bad=analysis(summary='')
        _,_,llm,accepted,errors=self.analyze([batch_reply(bad),analysis()])
        self.assertEqual(set(accepted),{'E001'}); self.assertFalse(errors)
        self.assertEqual([stage for stage,_ in llm.calls],['analyze_batch','analyze'])
        self.assertEqual(llm.calls[1][1]['retry_attempt'],1)
        self.assertEqual(llm.calls[1][1]['rejected_response'],bad)
        self.assertNotIn('recovery_guidance',llm.calls[1][1])

    def test_second_repair_uses_latest_error_and_rejected_response(self):
        first=analysis(summary='')
        second=analysis(urgency=6)
        _,_,llm,accepted,errors=self.analyze([batch_reply(first),second,analysis()])
        self.assertEqual(set(accepted),{'E001'}); self.assertFalse(errors)
        self.assertEqual(len(llm.calls),3)
        attempt=llm.calls[2][1]
        self.assertEqual(attempt['retry_attempt'],2)
        self.assertIn('urgency',attempt['repair'])
        self.assertEqual(attempt['rejected_response'],second)
        self.assertNotEqual(attempt['repair'],llm.calls[1][1]['repair'])
        self.assertNotIn('recovery_guidance',attempt)

    @patch("news_engine.models.NUMERIC_GROUNDING_ENABLED", True)
    def test_opt_in_numeric_repair_is_qualitative_and_preserves_full_articles(self):
        first=analysis(summary='USD 99 million.')
        second=analysis(summary='USD 88 million.')
        final=analysis(summary='The reported facility may provide another funding option.')
        _,source,llm,accepted,errors=self.analyze([batch_reply(first),second,final])
        self.assertFalse(errors); self.assertEqual(accepted['E001'].score.total,67)
        attempt=llm.calls[2][1]
        self.assertEqual(attempt['retry_attempt'],2)
        self.assertNotEqual(attempt['repair'],llm.calls[1][1]['repair'])
        self.assertIn('unsupported',attempt['repair'])
        self.assertEqual(attempt['rejected_response'],second)
        guidance=attempt['recovery_guidance'].lower()
        self.assertIn('qualitative',guidance)
        self.assertIn('quantit',guidance)
        self.assertIn('words',guidance)
        self.assertEqual(attempt['articles'],[source.model_dump(mode='json')])
        self.assertEqual(attempt['current_event']['event_id'],'E001')
        self.assertEqual(llm.calls[1][1]['articles'],attempt['articles'])

    def test_default_numeric_mismatch_does_not_trigger_retry_or_qualitative_fallback(self):
        value=analysis(summary='EUR 99 trillion at -20% on 2099-12-31.')
        _,source,llm,accepted,errors=self.analyze([batch_reply(value)])
        self.assertFalse(errors);self.assertEqual(len(llm.calls),1)
        self.assertEqual(accepted['E001'].summary,value['summary'])
        self.assertEqual(accepted['E001'].score.total,67)
        self.assertEqual(llm.calls[0][1]['events'][0]['articles'],[source.model_dump(mode='json')])
        self.assertNotIn('recovery_guidance',llm.calls[0][1])

    def test_exhaustion_retains_strict_r5_and_one_terminal_error(self):
        bad=analysis(impact=6)
        current,_,llm,accepted,errors=self.analyze([batch_reply(bad),deepcopy(bad),deepcopy(bad)])
        self.assertFalse(accepted); self.assertIsNone(current.score)
        self.assertEqual(len(llm.calls),3); self.assertEqual(len(errors),1)
        self.assertEqual(errors[0].item_id,'E001')
        self.assertIn('2 automatic repair attempts',errors[0].message)
        self.assertIn('impact',errors[0].message)

    @patch("news_engine.models.NUMERIC_GROUNDING_ENABLED", True)
    def test_explicit_numeric_validation_is_not_relaxed_on_final_attempt(self):
        bad=analysis(summary='USD 99 million.')
        _,_,llm,accepted,errors=self.analyze([batch_reply(bad),deepcopy(bad),deepcopy(bad)])
        self.assertFalse(accepted); self.assertEqual(len(errors),1)
        self.assertIn('unsupported',errors[0].message)
        self.assertEqual(len(llm.calls),3)

    def test_provider_failure_during_first_repair_stops_immediately(self):
        bad=analysis(summary='')
        _,_,llm,accepted,errors=self.analyze([batch_reply(bad),RuntimeError('authentication failure')])
        self.assertFalse(accepted); self.assertEqual(len(errors),1)
        self.assertEqual(len(llm.calls),2)
        self.assertEqual(errors[0].code,'RuntimeError')
        self.assertIn('provider',errors[0].message.lower())

    def test_provider_failure_during_second_repair_stops_at_budget(self):
        bad=analysis(summary='')
        _,_,llm,accepted,errors=self.analyze([batch_reply(bad),deepcopy(bad),RuntimeError('transport failure')])
        self.assertFalse(accepted); self.assertEqual(len(errors),1)
        self.assertEqual(len(llm.calls),3)
        self.assertEqual(errors[0].code,'RuntimeError')

    def test_second_repair_failure_does_not_touch_valid_sibling(self):
        current=[event(),event('E002','A002')]
        sources={'A001':article(),'A002':article('A002')}
        batches,_=analysis_batches(current,sources,'2026-01-02')
        good=analysis();bad=analysis(summary='')
        llm=Scripted([{'results':[{'event_id':'E001','analysis':good},
            {'event_id':'E002','analysis':bad}]},deepcopy(bad),deepcopy(bad)])
        accepted,errors=analyze_batch(current,llm,batches[0][1])
        self.assertEqual(set(accepted),{'E001'});self.assertEqual(len(errors),1)
        self.assertEqual(accepted['E001'].summary,good['summary'])
        self.assertEqual(accepted['E001'].score.total,67)
        self.assertEqual([c['current_event']['event_id'] for stage,c in llm.calls if stage=='analyze'],['E002','E002'])

    def test_pipeline_second_repair_completes_same_run_then_uses_cache(self):
        class RecoveringLabels(ScriptedLabels):
            def complete(self,schema,prompt,context,*,stage):
                value=super().complete(schema,prompt,context,stage=stage)
                payload=value.model_dump(mode='json',warnings=False)
                if stage=='analyze_batch':
                    for item in payload['results']:
                        item['analysis']['summary']=''
                elif stage=='analyze' and context['retry_attempt']==1:
                    payload['urgency']=6
                return schema.model_validate(payload)
        llm=RecoveringLabels()
        raw={'url':'https://example.test/retry','title':'Liquidity facility | original report',
             'text':'A new liquidity facility was announced. Treasury eligibility remains unverified.'}
        with TemporaryDirectory() as folder,Store(Path(folder)/'v8.sqlite') as store:
            result=run_pipeline(store,llm,articles=[raw])
            self.assertEqual(result.status,'completed',result.errors)
            self.assertEqual(len(result.events),1);self.assertIsNotNone(result.events[0].score)
            stages=[stage for stage,_ in llm.calls]
            self.assertEqual(stages.count('classify'),1)
            self.assertEqual(stages.count('analyze_batch'),1)
            self.assertEqual(stages.count('analyze'),2)
            assignments=store.assignments();calls=len(llm.calls)
            cached=run_pipeline(store,llm,articles=[raw])
            self.assertEqual(cached.status,'completed')
            self.assertEqual(cached.events,result.events)
            self.assertEqual(store.assignments(),assignments)
            self.assertEqual(len(llm.calls),calls)


if __name__=='__main__': unittest.main()
