"""W3 bounded rounds and authentic development comparisons, without provider calls."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from evaluation.real_corpus import load_sources
from news_engine.events import group_change, retrieve_candidates
from news_engine.models import MajorChange, Evidence, Event, EventIdentity
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from test_mvp import NOW, draft, analysis_reply


class Steps:
    model='offline-w3'
    def __init__(self, handler):
        self.handler=handler
        self.calls=[]
        self.receipts=[]
    def complete(self,schema,prompt,context,*,stage):
        self.calls.append((stage,context))
        self.receipts.append(dict(stage=stage,prompt_tokens=10,completion_tokens=5,
            cost_usd=0.001,latency_seconds=0.1))
        value = self.handler(schema,context,stage)
        if schema.__name__ == 'EventExtractionDraft' and 'changes' in value:
            value = dict(complete=value['complete'],events=[dict(title=c['description'][:180],
                identity=c.get('identity') or draft(c['description'])['identity'],category='regulatory',
                source_id=context['source_id'],changes=[c]) for c in value['changes']])
        if stage=='extract' and 'events' in value:
            for event in value['events']:
                for change in event['changes']:
                    change.setdefault('claim_type_basis',{'actual':'observed_action','announced':'announced_action','proposed':'proposed_rule','forecast':'forecast','unverified':'insufficient_evidence'}[change['claim_type']])
                    change.setdefault('status_evidence_quote',change['evidence_quote'])
        return schema.model_validate(value)


def identity_reply(context,decision='same',event_id=None):
    candidate=context['candidates'][0] if context['candidates'] else None
    return dict(decision=decision,event_id=(event_id or candidate['event_id']) if decision=='same' else None,
        identity=context['change']['identity'],reason='Compared the supplied specific occurrence and instrument.')


def novelty_reply(context,relation='material',**updates):
    return dict(relation=relation,compared_change_id=context['candidates'][0]['changes'][0]['change_id'],
        reason='Compared the accepted development and qualifications, not just the evidence context.',**updates)


class W3Tests(unittest.TestCase):
    def setUp(self):
        self.sources=load_sources()
        self.identity=EventIdentity(subject='Federal Reserve Board',action='rule modification',
            occurrence='PPP insider lending rule change',scope='PPP insider lending')
    def change(self,description,claim='announced',source='A1',key='C1',identity=None,evidence=None):
        quote=evidence or description
        return MajorChange(change_id=key,source_id=source,description=description,claim_type=claim,
            direction='unknown',numeric=[],identity=identity or self.identity,
            evidence=Evidence(quote=quote,start=0,end=len(quote)))
    def event(self,change,key='E1',age=0):
        return Event(category='regulatory',event_id=key,title=change.description,identity=change.identity,
            what_changed=[change],latest_change_id=change.change_id,created_at=NOW,
            last_seen_at=NOW,last_material_at=NOW+timedelta(days=age))

    def test_actual_clarification_gets_one_targeted_reconsideration(self):
        text=self.sources['real-5e84489fdd4c']['source']['body']
        announcement=text.split('\n\n')[0].split('. The change')[0]+'.'
        clarification=text.split('\n\n')[-1].split(" The Board's change")[0]
        original=self.change(announcement)
        change=self.change(clarification,'actual',key='C2',evidence=text.split('\n\n')[-1])
        def handle(schema,ctx,stage):
            if stage=='group_identity':return identity_reply(ctx)
            if stage=='group_novelty':return novelty_reply(ctx,'corroboration')
            self.assertEqual(stage,'group_reconsider')
            self.assertIn('claim type',ctx['reconsideration']['violated_invariant'])
            self.assertEqual(ctx['change']['description'],clarification)
            self.assertEqual(ctx['reconsideration']['rejected_decision']['relation'],'corroboration')
            return novelty_reply(ctx)
        llm=Steps(handle)
        result=group_change(change,[self.event(original)],llm)
        self.assertEqual(result.relation,'material')
        self.assertIsNone(result.duplicate_of)
        self.assertEqual(len(llm.calls),3)

    def test_eligibility_is_material_even_inside_old_context_quote(self):
        text=self.sources['real-5e84489fdd4c']['source']['body'].split('\n\n')[0]
        announcement,modification=text.split(' The change',1)
        original=self.change(announcement,evidence=text)
        change=self.change('The change'+modification,key='C2',evidence=text)
        llm=Steps(lambda schema,ctx,stage:identity_reply(ctx) if stage=='group_identity' else novelty_reply(ctx))
        result=group_change(change,[self.event(original)],llm)
        self.assertEqual(result.relation,'material')
        self.assertEqual(len(llm.calls),2)
        self.assertIn('will temporarily modify',llm.calls[1][1]['change']['description'])

    def test_retrieval_includes_old_occurrence_and_budgets_small_database(self):
        change=self.change('Clarification of the PPP insider lending decision.')
        target=self.event(change,'OLD',-3000)
        distractors=[self.event(change.model_copy(update={'identity':EventIdentity(subject='Another authority',
            occurrence=f'Other decision {i}',action='adopts',scope='Other facility')}),f'D{i}') for i in range(40)]
        self.assertEqual(retrieve_candidates(change,distractors+[target])[0].event_id,'OLD')
        self.assertEqual(len(retrieve_candidates(change,distractors+[target])),30)
        unrelated=change.model_copy(update={'description':'Unrelated subject','identity':None})
        self.assertEqual(len(retrieve_candidates(unrelated,distractors[:4])),4)

    def test_rerank_then_identity_novelty_and_reconsideration_cap(self):
        old=self.change('The Board announced the rule change.')
        change=self.change('The SBA clarified eligibility.','actual',key='C2')
        events=[self.event(old,f'E{i}') for i in range(6)]
        def handle(schema,ctx,stage):
            if stage=='group_retrieve':return {'event_ids':['E3','E1']}
            if stage=='group_identity':
                self.assertEqual([c['event_id'] for c in ctx['candidates']],['E3','E1'])
                return identity_reply(ctx)
            return novelty_reply(ctx,'duplicate' if stage=='group_novelty' else 'material')
        llm=Steps(handle)
        result=group_change(change,events,llm)
        self.assertEqual(result.event_id,'E3')
        self.assertEqual(result.relation,'material')
        self.assertEqual([s for s,_ in llm.calls],['group_retrieve','group_identity','group_novelty','group_reconsider'])

    def test_one_shared_reconsideration_no_second_retry(self):
        old=self.change('The Board announced the rule change.')
        change=self.change('The SBA clarified eligibility.','actual',key='C2')
        events=[self.event(old,f'E{i}') for i in range(6)]
        def handle(schema,ctx,stage):
            if stage=='group_retrieve':return {'event_ids':['invented']}
            if stage=='group_reconsider':return {'event_ids':['E1']}
            if stage=='group_identity':return identity_reply(ctx)
            return novelty_reply(ctx,'duplicate')
        llm=Steps(handle)
        with self.assertRaisesRegex(ValueError,'claim type'):group_change(change,events,llm)
        self.assertEqual(len(llm.calls),4)
        self.assertEqual(sum(s=='group_reconsider' for s,_ in llm.calls),1)

    def test_new_uncertain_empty_rerank_and_invalid_ids_stop(self):
        change=self.change('A new decision.')
        for decision in ('new','uncertain'):
            llm=Steps(lambda schema,ctx,stage:identity_reply(ctx,decision))
            result=group_change(change,[self.event(change)],llm)
            self.assertEqual(result.relation,decision)
            self.assertEqual(len(llm.calls),1)
        for decision in ('new','uncertain'):
            def empty_shortlist(schema,ctx,stage):
                if stage=='group_retrieve':return {'event_ids':[]}
                self.assertEqual(stage,'group_identity')
                self.assertEqual(ctx['candidates'],[])
                return identity_reply(ctx,decision)
            llm=Steps(empty_shortlist)
            self.assertEqual(group_change(change,[self.event(change,f'E{i}') for i in range(6)],llm).relation,decision)
            self.assertEqual([stage for stage,_ in llm.calls],['group_retrieve','group_identity'])
        for ids in (['invented'],['E1','E1']):
            llm=Steps(lambda schema,ctx,stage:{'event_ids':ids})
            with self.assertRaisesRegex(ValueError,'unique IDs'):group_change(change,[self.event(change,f'E{i}') for i in range(6)],llm)
            self.assertEqual(len(llm.calls),2)

    def test_corroboration_requires_independent_source(self):
        change=self.change('The Board announced the rule change.')
        llm=Steps(lambda schema,ctx,stage:identity_reply(ctx) if stage=='group_identity' else novelty_reply(ctx,'corroboration'))
        with self.assertRaisesRegex(ValueError,'independent source'):group_change(change,[self.event(change)],llm)
        self.assertEqual(len(llm.calls),3)
        independent=change.model_copy(update={'source_id':'A2','change_id':'C2'})
        self.assertEqual(group_change(independent,[self.event(change)],llm).relation,'corroboration')

    def test_actual_and_announced_changes_survive_restart_without_lifecycle(self):
        source=self.sources['real-50339f5e620a']['source']
        intro,mechanism=source['body'].split('\n\n')
        operational=intro.split(', which')[0]
        clause="The Federal Reserve's facility"+mechanism.split("The Federal Reserve's facility",1)[1].split(' Supplying')[0]
        ident=dict(subject='Federal Reserve',action='facility launch',occurrence='PPPLF launch',scope='PPP liquidity facility')
        def handle(schema,ctx,stage):
            if stage=='extract':
                return {'complete':True,'changes':[draft(operational,identity=ident,evidence_quote=intro),
                    draft(clause,identity=ident,evidence_quote=mechanism,claim_type='announced')]}
            if stage=='group_identity':return identity_reply(ctx)
            if stage=='group_novelty':return novelty_reply(ctx)
            return analysis_reply(ctx)
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'news.sqlite'
            llm=Steps(handle)
            with Store(database) as store:
                result=run_pipeline(store,llm,[{'url':source['url'],'title':source['title'],'text':source['body']}])
                self.assertEqual(result.status,'completed')
                self.assertEqual(len(store.events()),1)
                event=store.events()[0]

                self.assertEqual(len(result.events[0].what_changed),2)
                for substep in ('group_identity','group_novelty'):
                    self.assertEqual(result.metrics['w3'][substep]['calls'],1)
                    self.assertAlmostEqual(result.metrics['w3'][substep]['wall_seconds'],0.1)
                    self.assertEqual(result.metrics['w3'][substep]['cost_usd'],0.001)
            with Store(database) as store:
                repeated=run_pipeline(store,llm,[])
                self.assertEqual(store.events()[0].event_id,event.event_id)

                self.assertEqual(repeated.metrics['llm_calls'],0)

    def test_empty_shortlist_cannot_select_an_absent_event(self):
        change=self.change('New shipping issue.')
        def handler(schema,ctx,stage):
            if stage=='group_retrieve':return {'event_ids':[]}
            return identity_reply(ctx,event_id='E1')
        llm=Steps(handler)
        with self.assertRaisesRegex(ValueError,'outside supplied candidates'):
            group_change(change,[self.event(change,f'E{i}') for i in range(6)],llm,identity_only=True)
        self.assertEqual([stage for stage,_ in llm.calls],['group_retrieve','group_identity','group_reconsider'])

    def test_new_topic_after_six_events_keeps_whole_proposal_and_restart_id(self):
        def handler(schema,ctx,stage):
            if stage=='extract':
                ident=dict(subject=ctx['title'],action='announces',occurrence=ctx['title'],scope=ctx['title'])
                return dict(complete=True,events=[dict(title=ctx['title'],identity=ident,
                    category='industry' if ctx['title']=='Suez routing' else 'regulatory',source_id=ctx['source_id'],
                    changes=[draft(text,identity=ident) for text in ctx['text'].split(' | ')])])
            if stage=='group_retrieve':return {'event_ids':[]}
            if stage=='group_identity':
                if not ctx['candidates']:
                    self.assertEqual(len(ctx['event_proposal']['changes']),2)
                    self.assertIn('Suez',ctx['event_proposal']['identity']['subject'])
                return identity_reply(ctx,'new')
            if stage=='analyze':return analysis_reply(ctx)
            self.fail('New proposal should not need child novelty')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new-topic.sqlite';llm=Steps(handler)
            with Store(path) as store:
                seed=run_pipeline(store,llm,[dict(url=f'https://example.org/seed{i}',title=f'Sanction rule {i}',text=f'Authority adopted rule {i}.') for i in range(6)])
                self.assertEqual(seed.status,'completed',seed.errors)
                self.assertEqual(len(store.events()),6)
            before=len(llm.calls)
            raw=[dict(url='https://example.org/suez',title='Suez routing',text='Suez routing resumed. | Carrier schedules changed.')]
            with Store(path) as store:
                result=run_pipeline(store,llm,raw)
                self.assertEqual(result.status,'completed',result.errors)
                self.assertEqual(len(store.events()),7)
                self.assertEqual(len(result.events[0].what_changed),2)
                self.assertEqual(result.events[0].category,'industry')
                key=result.events[0].event_id
                self.assertTrue(all(c.event_id for c in store.changes()))
                self.assertEqual([stage for stage,_ in llm.calls[before:] if stage.startswith('group_')],['group_retrieve','group_identity'])
            calls=len(llm.calls)
            with Store(path) as store:
                repeated=run_pipeline(store,llm,raw)
                self.assertEqual(repeated.events[0].event_id,key)
                self.assertEqual(len(llm.calls),calls)
