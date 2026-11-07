"""Batch analysis contracts, real thread overlap and caller-thread persistence; no API."""
import json
import tempfile
from pathlib import Path
from threading import Barrier,Lock,get_ident
from unittest import TestCase
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from news_engine.analyze import analysis_batches,analyze_batch,validate_analysis,AnalysisDraft,ANALYSIS_PROMPT,r5_policy_guidance
from news_engine.api import create_app
from news_engine.clients import LLMClient
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from news_engine.models import Evidence,SourceEvidence
from test_event_only import Fake,raw,analysis,NOW


def batch_result(ctx):
    return {'results':[dict(event_id=item['current_event']['event_id'],analysis=analysis(item)) for item in ctx['events']]}


class AnalysisBatchTests(TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/'batch.sqlite'
        self.store=Store(self.path)

    def tearDown(self):
        self.store.close();self.folder.cleanup()

    def seed(self,count=3):
        names=['alpha','beta','gamma','delta','epsilon','zeta'][:count]
        run_pipeline(self.store,Fake(),[raw(f'Authority adopted rule {name}.',f'https://example.org/{name}',title=name)
                                      for name in names],reference_time=NOW)
        events=self.store.events()
        for event in events:
            event.score=None;event.why_it_matters=[];event.what_to_do=[];self.store.save_event(event)
        return events

    def job(self,events):
        jobs,errors=analysis_batches(events,{a.article_id:a for a in self.store.articles()},NOW.isoformat())
        self.assertFalse(errors);self.assertEqual(len(jobs),1)
        return jobs[0]

    def test_reversed_results_match_by_id_not_position(self):
        events,ctx=self.job(self.seed())
        expected={item['current_event']['event_id']:item['current_event']['title'] for item in ctx['events']}
        def handler(stage,context):
            if stage=='analyze_batch':
                value=batch_result(context)
                for item in value['results']:
                    item['analysis']['why_it_matters']=[expected[item['event_id']]]
                value['results'].reverse();return value
        llm=Fake(handler);accepted,errors=analyze_batch(events,llm,ctx)
        self.assertFalse(errors);self.assertEqual(set(accepted),set(expected))
        for key,event in accepted.items():self.assertEqual(event.why_it_matters,[expected[key]])
        self.assertEqual([stage for stage,c in llm.calls],['analyze_batch'])

    def test_batch_input_preserves_claim_types_but_analysis_returns_only_selector(self):
        event=self.seed(1)[0]
        original=event.what_changed[0]
        entries=[original.model_copy(update={'claim_type':'actual'}),original.model_copy(update={'claim_type':'forecast'})]
        event=event.model_copy(update={'what_changed':entries,'summary':' '.join(e.text for e in entries)})
        members,ctx=self.job([event])
        supplied=ctx['events'][0]
        self.assertEqual([e['claim_type'] for e in supplied['current_event']['what_changed']],['actual','forecast'])
        self.assertEqual(supplied['allowed_values']['primary_entry_index'],[0,1])
        output=analysis(supplied)
        self.assertEqual(output['primary_entry_index'],0)
        self.assertNotIn('primary_claim_type',output);self.assertNotIn('claim_type',output)
        accepted,errors=analyze_batch(members,Fake(),ctx)
        self.assertFalse(errors);self.assertIn(event.event_id,accepted)

    def test_identical_quotes_inherit_forecast_and_proposed_caps_from_selected_entry(self):
        event=self.seed(1)[0];entry=event.what_changed[0]
        for claim_type,impact,cap in [('forecast',2,1),('proposed',4,3)]:
            entries=[entry.model_copy(update={'claim_type':'actual'}),entry.model_copy(update={'claim_type':claim_type})]
            mixed=event.model_copy(update={'what_changed':entries,'summary':' '.join(e.text for e in entries)})
            value={**analysis({'current_event':mixed.model_dump(mode='json')}),
                   'impact':impact,'relevance':4,'urgency':2,'impact_basis':'constrained_access',
                   'relevance_basis':'direct_channel','urgency_basis':'routine_or_unknown'}
            with self.subTest(claim_type=claim_type),patch('news_engine.models.R5_CAPS_ENABLED',False):
                self.assertEqual(validate_analysis(mixed,{**value,'primary_entry_index':0}).score.impact,impact)
                self.assertEqual(validate_analysis(mixed,{**value,'primary_entry_index':1}).score.impact,impact)
            with self.subTest(restored_claim_type=claim_type),patch('news_engine.models.R5_CAPS_ENABLED',True):
                self.assertIsNotNone(validate_analysis(mixed,{**value,'primary_entry_index':0}).score)
                with self.assertRaisesRegex(ValueError,f'{claim_type} scoring claim caps'):
                    validate_analysis(mixed,{**value,'primary_entry_index':1})
                accepted=validate_analysis(mixed,{**value,'primary_entry_index':1,'impact':cap})
                self.assertEqual(accepted.score.impact,cap)

    def test_disabled_caps_allow_independent_analysis_dimensions(self):
        event=self.seed(1)[0]
        value={**analysis({'current_event':event.model_dump(mode='json')}),
            'impact':5,'relevance':0,'urgency':5,'impact_basis':'none','relevance_basis':'none',
            'urgency_basis':'none','has_treasury_action_or_constraint':False}
        accepted=validate_analysis(event,value)
        self.assertEqual((accepted.score.impact,accepted.score.relevance,accepted.score.urgency,accepted.score.total),(5,0,5,65))
        with patch('news_engine.models.R5_CAPS_ENABLED',True),self.assertRaises(ValueError):validate_analysis(event,value)

    def test_prompt_explicitly_disables_caps_and_can_restore_policy_guidance(self):
        self.assertIn('Hard caps and cross-dimensional restrictions are disabled',ANALYSIS_PROMPT)
        self.assertIn('Score P, R and U independently',ANALYSIS_PROMPT)
        self.assertNotIn('Caps:',ANALYSIS_PROMPT)
        self.assertNotIn('relevance1 caps impact/urgency',ANALYSIS_PROMPT)
        self.assertNotIn('a none basis requires zero',ANALYSIS_PROMPT)
        with patch('news_engine.models.R5_CAPS_ENABLED',True):
            guidance=r5_policy_guidance()
        self.assertIn('restrictions are enabled',guidance)
        self.assertIn('forecast primary entry caps P at1',guidance)
        self.assertIn('R=0 requires P=U=0',guidance)

    def test_primary_entry_selector_is_strict_and_in_range(self):
        event=self.seed(1)[0];value=analysis({'current_event':event.model_dump(mode='json')})
        self.assertIsNotNone(validate_analysis(event,value).score)
        for selector in (True,False,1.0,'0',-1,1,99,None):
            with self.subTest(selector=selector),self.assertRaises(ValueError):
                validate_analysis(event,{**value,'primary_entry_index':selector})

    def test_scoring_quote_text_is_not_matched_to_selected_entry(self):
        event=self.seed(1)[0];entry=event.what_changed[0];body=entry.evidence.quote
        first='Authority';second='rule alpha.'
        quotes=[Evidence(quote=q,start=body.index(q),end=body.index(q)+len(q)) for q in (first,second)]
        entries=[entry.model_copy(update={'evidence':quote}) for quote in quotes]
        event=event.model_copy(update={'what_changed':entries,'summary':' '.join(e.text for e in entries),
            'evidence':[SourceEvidence(source_id=entry.source_id,evidence=quote) for quote in quotes]})
        value=analysis({'current_event':event.model_dump(mode='json')})
        value['primary_evidence_quote']=second
        self.assertIsNotNone(validate_analysis(event,value).score)
        self.assertIsNotNone(validate_analysis(event,{**value,'primary_entry_index':1}).score)

    def test_selected_entry_still_rejects_another_entrys_source(self):
        events=self.seed(2);first,second=events
        event=first.model_copy(update={'source_ids':[*first.source_ids,*second.source_ids],
            'what_changed':[*first.what_changed,*second.what_changed],
            'summary':' '.join(e.text for e in [*first.what_changed,*second.what_changed]),
            'evidence':[*first.evidence,*second.evidence]})
        value=analysis({'current_event':event.model_dump(mode='json')})
        value['primary_source_id']=second.what_changed[0].source_id
        value['primary_evidence_quote']='Normalized supporting text, not an exact quotation.'
        with self.assertRaises(ValueError):validate_analysis(event,value)
        self.assertIsNotNone(validate_analysis(event,{**value,'primary_entry_index':1}).score)

    def test_normalized_scoring_quote_passes_batch_without_repair(self):
        events,ctx=self.job(self.seed())
        def handler(stage,context):
            if stage=='analyze_batch':
                value=batch_result(context)
                for item in value['results']:item['analysis']['primary_evidence_quote']='The authority has adopted new requirements.'
                return value
        llm=Fake(handler);accepted,errors=analyze_batch(events,llm,ctx)
        self.assertFalse(errors);self.assertEqual(len(accepted),3)
        self.assertEqual([stage for stage,c in llm.calls],['analyze_batch'])

    def test_analysis_rejects_obsolete_duplicate_claim_type_output_fields(self):
        event=self.seed(1)[0];value=analysis({'current_event':event.model_dump(mode='json')})
        for field in ('claim_type','primary_claim_type'):
            with self.subTest(field=field),self.assertRaises(ValueError):
                AnalysisDraft.model_validate({**value,field:'actual'})

    def test_bad_nested_item_does_not_discard_valid_siblings(self):
        events,ctx=self.job(self.seed())
        bad=ctx['events'][1]['current_event']['event_id']
        def handler(stage,context):
            if stage=='analyze_batch':
                value=batch_result(context);value['results'][1]['analysis']['impact']='invalid';return value
        llm=Fake(handler);accepted,errors=analyze_batch(events,llm,ctx)
        self.assertFalse(errors);self.assertEqual(len(accepted),3)
        repairs=[c['current_event']['event_id'] for stage,c in llm.calls if stage=='analyze']
        self.assertEqual(repairs,[bad])

    def test_source_claim_and_r5_failures_remain_item_local(self):
        events,ctx=self.job(self.seed())
        bad=ctx['events'][0]['current_event']['event_id']
        other=ctx['events'][1]['current_event']['evidence'][0]
        cases=[{'impact':6},{'primary_evidence_quote':'   '},
               {'primary_entry_index':99},
               {'primary_source_id':other['source_id'],'primary_evidence_quote':other['evidence']['quote']}]
        for invalid in cases:
            def handler(stage,context):
                if stage=='analyze_batch':
                    value=batch_result(context);value['results'][0]['analysis'].update(invalid);return value
                if stage=='analyze':return {**analysis(context),**invalid}
            llm=Fake(handler);accepted,errors=analyze_batch(events,llm,ctx)
            with self.subTest(invalid=invalid):
                self.assertNotIn(bad,accepted);self.assertEqual(len(accepted),2)
                self.assertEqual(len(errors),1);self.assertEqual(errors[0].item_id,bad)
                self.assertEqual([s for s,c in llm.calls],['analyze_batch','analyze'])

    def test_missing_duplicate_and_unknown_ids_never_attach_by_position(self):
        events,ctx=self.job(self.seed())
        ids=[e.event_id for e in events]
        def handler(stage,context):
            if stage=='analyze_batch':
                value=batch_result(context);items=value['results']
                value['results']=[items[0],items[0],items[2],{**items[2],'event_id':'E-unknown'}]
                return value
        llm=Fake(handler);accepted,errors=analyze_batch(events,llm,ctx)
        self.assertEqual(set(accepted),set(ids))
        repaired={c['current_event']['event_id'] for s,c in llm.calls if s=='analyze'}
        self.assertEqual(repaired,{ctx['events'][0]['current_event']['event_id'],ctx['events'][1]['current_event']['event_id']})
        self.assertEqual([e.code for e in errors],['unexpected_event_id'])
        self.assertNotIn('E-unknown',accepted)

    def test_malformed_batch_has_one_repair_per_event_not_two(self):
        events,ctx=self.job(self.seed())
        def handler(stage,context):
            if stage=='analyze_batch':raise ValueError('Malformed JSON')
        llm=Fake(handler);accepted,errors=analyze_batch(events,llm,ctx)
        self.assertFalse(errors);self.assertEqual(len(accepted),3)
        self.assertEqual([s for s,c in llm.calls].count('analyze_batch'),1)
        self.assertEqual([s for s,c in llm.calls].count('analyze'),3)

    def test_provider_failure_does_not_add_a_retry_loop(self):
        events,ctx=self.job(self.seed())
        def handler(stage,context):raise RuntimeError('Provider unavailable')
        llm=Fake(handler);accepted,errors=analyze_batch(events,llm,ctx)
        self.assertEqual(accepted,{});self.assertEqual(len(errors),3)
        self.assertEqual([s for s,c in llm.calls],['analyze_batch'])

    def test_batches_overlap_and_sqlite_writes_stay_on_caller_thread(self):
        self.seed(6)
        barrier=Barrier(2);lock=Lock();active=0;maximum=0;caller=get_ident();writes=[]
        def handler(stage,ctx):
            nonlocal active,maximum
            if stage=='analyze_batch':
                self.assertEqual(len(ctx['events']),3)
                with lock:active+=1;maximum=max(maximum,active)
                try:barrier.wait(timeout=5)
                finally:
                    with lock:active-=1
        original=self.store.save_event
        def write(event):
            writes.append(get_ident());self.assertEqual(get_ident(),caller);original(event)
        llm=Fake(handler)
        with patch.object(self.store,'save_event',side_effect=write):
            result=run_pipeline(self.store,llm,[],reference_time=NOW)
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual(maximum,2);self.assertEqual(len(writes),6)
        self.assertEqual(result.metrics['analysis_batches'],2)
        self.assertEqual(result.metrics['llm_calls_by_stage'],{'analyze_batch':2})
        self.assertTrue(all(e.score is not None for e in result.events))

    def test_partial_score_persistence_retries_only_failed_event_after_restart(self):
        events=self.seed();bad=sorted(e.event_id for e in events)[1]
        def handler(stage,ctx):
            if stage=='analyze_batch':
                value=batch_result(ctx)
                for item in value['results']:
                    if item['event_id']==bad:item['analysis']['impact']=6
                return value
            if stage=='analyze':return {**analysis(ctx),'impact':6}
        first=run_pipeline(self.store,Fake(handler),[],reference_time=NOW)
        self.assertEqual(first.status,'partial')
        self.assertEqual(sum(e.score is not None for e in self.store.events()),2)
        self.store.close();self.store=Store(self.path)
        llm=Fake();second=run_pipeline(self.store,llm,[],reference_time=NOW)
        self.assertEqual(second.status,'completed',second.errors)
        batches=[c for s,c in llm.calls if s=='analyze_batch']
        self.assertEqual([[e['current_event']['event_id'] for e in c['events']] for c in batches],[[bad]])
        self.assertTrue(all(e.score is not None for e in self.store.events()))

    def test_long_analysis_context_is_forwarded_without_length_cap(self):
        events=self.seed(2);articles={a.article_id:a for a in self.store.articles()}
        long_summary='source context '*4000
        events[0]=events[0].model_copy(update={'summary':long_summary})
        jobs,errors=analysis_batches(events,articles,NOW.isoformat(),max_chars=1)
        self.assertFalse(errors);self.assertEqual(len(jobs),1)
        members,ctx=jobs[0]
        self.assertEqual(len(members),2)
        self.assertGreater(len(json.dumps(ctx,ensure_ascii=False)),32000)
        member=next(item for item in ctx['events'] if item['current_event']['event_id']==events[0].event_id)
        self.assertEqual(member['current_event']['summary'],long_summary)
        llm=Fake();accepted,errors=analyze_batch(members,llm,ctx)
        self.assertFalse(errors);self.assertEqual(len(accepted),2)
        self.assertEqual(llm.calls[0][1]['events'],ctx['events'])

    def test_unsupported_narrative_numbers_do_not_repair_a_batch_item(self):
        events,ctx=self.job(self.seed())
        def handler(stage,context):
            if stage=='analyze_batch':
                value=batch_result(context)
                value['results'][0]['analysis']['why_it_matters']=['The 2026 estimate is USD 99999 and 17 percent.']
                return value
        llm=Fake(handler);accepted,errors=analyze_batch(events,llm,ctx)
        self.assertFalse(errors);self.assertEqual(len(accepted),3)
        self.assertEqual([stage for stage,context in llm.calls],['analyze_batch'])

    def test_batch_request_has_strict_schema_without_output_token_limit(self):
        events,ctx=self.job(self.seed());requests=[]
        def respond(request):
            payload=json.loads(request.content);requests.append(payload)
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(batch_result(ctx))}}]})
        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            llm=LLMClient('offline','fake','https://example.org/v1',http_client=transport)
            accepted,errors=analyze_batch(events,llm,ctx)
        self.assertFalse(errors);self.assertEqual(len(accepted),3)
        self.assertEqual(len(requests),1)
        for key in ('max_tokens','max_completion_tokens','max_output_tokens'):self.assertNotIn(key,requests[0])
        schema=requests[0]['response_format']['json_schema']['schema']
        nested=schema['$defs']['BatchAnalysisItem']['properties']['analysis']
        self.assertNotIn('primary_claim_type',nested['properties'])
        self.assertNotIn('claim_type',nested['properties'])
        self.assertEqual(nested['properties']['primary_entry_index']['type'],'integer')
        self.assertIn(json.dumps(schema,ensure_ascii=False,separators=(',',':')),requests[0]['messages'][0]['content'])

    def test_python_and_http_batch_limits_and_api_forwarding(self):
        for kwargs in [dict(analysis_batch_size=0),dict(analysis_batch_size=5),dict(analysis_workers=0),dict(analysis_workers=9)]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):run_pipeline(self.store,Fake(),[],**kwargs)
        with TestClient(create_app(database=self.path,token='test',llm=Fake())) as client:
            headers={'Authorization':'Bearer test'}
            for value in [0,5]:
                self.assertEqual(client.post('/api/news/runs',headers=headers,json={'articles':[raw()],'analysis_batch_size':value}).status_code,422)
            response=client.post('/api/news/runs',headers=headers,json={'articles':[raw()],'analysis_batch_size':2,'analysis_workers':1})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['metrics']['analysis_batch_size'],2)
            self.assertEqual(response.json()['metrics']['analysis_workers'],1)
