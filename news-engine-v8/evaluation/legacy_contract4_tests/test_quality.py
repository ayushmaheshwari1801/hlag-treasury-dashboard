"""Held-fixed persistent retrieval cases, defined before altering retrieval."""
from pathlib import Path
import tempfile
import unittest
from test_mvp import RecordedLLM, draft, grouping, policy_timing, analysis_reply
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from news_engine.events import retrieve_candidates
from news_engine.extract import extract_article
from test_mvp import article

CASES = [
    ('A central bank announced a small-business credit line.', 'PPPLF is fully operational.',
     {'subject':'Federal Reserve','action':'facility launch','occurrence':'Paycheck Protection Program Liquidity Facility','scope':'PPP loans'}),
    ('Harbour officials closed Dock Seven following an incident.', 'The suspension was lifted.',
     {'subject':'Port Delta authority','action':'berth restriction','occurrence':'Dock Seven incident closure','scope':'Port Delta Dock Seven'}),
    ('The prudential authority adopted Rule 27 for lenders.', 'The waiver takes effect.',
     {'subject':'Prudential authority','action':'capital rule','occurrence':'Rule 27 capital waiver','scope':'Rule 27 lender capital'})]

class RetrievalQualityTests(unittest.TestCase):
    def test_reworded_occurrences_survive_restart_without_splitting(self):
        for first_text, second_text, identity in CASES:
            with self.subTest(occurrence=identity['occurrence']), tempfile.TemporaryDirectory() as folder:
                database=Path(folder)/'news.sqlite'
                seen=[]
                def handle(stage,ctx):
                    if stage=='extract':
                        return {'complete':True,'changes':[draft(ctx['text'].split('\n')[0],evidence_quote=ctx['text'],identity=identity)]}
                    if stage=='group':
                        seen.append([c['event_id'] for c in ctx['candidates']])
                        return grouping(event_id=ctx['candidates'][0]['event_id'],identity=identity,confidence='high',relation='material')
                source_identity='\n'+identity['subject']+'; '+identity['occurrence']+'; '+identity['scope']
                first_text+=source_identity
                second_text+=source_identity
                llm=RecordedLLM(handle)
                with Store(database) as store:
                    first=run_pipeline(store,llm,[{'url':'https://example.org/first','title':identity['occurrence'],'text':first_text}])
                    event_id=first.events[0].event_id
                with Store(database) as store:
                    change=extract_article(article(second_text),RecordedLLM(lambda stage,ctx:{'complete':True,'changes':[draft(ctx['text'].split('\n')[0],evidence_quote=ctx['text'],identity=identity)]}))[0]
                    candidates=retrieve_candidates(change,store.events())
                    self.assertIn(event_id,[e.event_id for e in candidates],'Correct persisted Event absent from Top-5 keyword candidates')
                    second=run_pipeline(store,llm,[{'url':'https://example.org/second','title':identity['occurrence'],'text':second_text}])
                    self.assertEqual(second.status,'completed')
                    self.assertEqual(second.events[0].event_id,event_id)
                    self.assertEqual(len(store.events()),1)
                    self.assertEqual(len(store.changes()),2)
                    self.assertIn(event_id,seen[-1])

    def test_shared_actor_candidates_do_not_authorize_a_distinct_merge(self):
        identity={'subject':'Prudential authority','action':'capital rule','occurrence':'Rule 27 capital waiver','scope':'Rule 27 lender capital'}
        other={**identity,'occurrence':'Rule 28 lending cap','scope':'Rule 28 lending limits'}
        seen=[]
        def handle(stage,ctx):
            if stage=='extract':
                return {'complete':True,'changes':[draft(ctx['text'],identity=other if 'Different' in ctx['title'] else identity)]}
            if stage=='group':
                seen.extend(ctx['candidates'])
                return grouping(identity=other)
        with tempfile.TemporaryDirectory() as folder:
            with Store(Path(folder)/'news.sqlite') as store:
                llm=RecordedLLM(handle)
                one=run_pipeline(store,llm,[{'url':'https://example.org/rule27','title':'Rule 27','text':'Prudential authority adopted Rule 27 capital waiver.'}])
                two=run_pipeline(store,llm,[{'url':'https://example.org/rule28','title':'Different Rule 28','text':'Prudential authority adopted Rule 28 lending limits.'}])
                self.assertTrue(seen)
                self.assertNotEqual(one.events[0].event_id,two.events[0].event_id)
                self.assertEqual(len(store.events()),2)


class GroundingQualityTests(unittest.TestCase):
    def test_immediate_effect_and_comment_window_are_required_with_one_repair(self):
        from evaluation.real_corpus import load_sources
        text=load_sources()['real-a114d8803587']['source']['body']
        capital=draft(text.split('\n\n')[1],direction='unknown',numeric=[{'value':'0','unit':'percent','kind':'level','source_quote':'zero percent risk weight'}])
        omitted={'complete':True,'changes':[capital]}
        llm=RecordedLLM(lambda stage,ctx:omitted)
        with self.assertRaisesRegex(ValueError,'immediate-effect'):
            extract_article(article(text),llm)
        self.assertEqual(len(llm.calls),2)
        self.assertIn(text.strip().split('\n\n')[-1],llm.calls[0][1]['material_passages'])
        accepted={'complete':True,'changes':[capital,policy_timing(text)]}
        repaired=RecordedLLM(lambda stage,ctx:accepted if 'repair' in ctx else omitted)
        changes=extract_article(article(text),repaired)
        self.assertEqual(len(repaired.calls),2)
        self.assertIn('effective immediately',changes[1].description)
        self.assertIn('after publication in the Federal Register',changes[1].description)
        self.assertEqual(changes[1].numeric[0].value,'30')
        self.assertIsNone(changes[1].occurrence_at)
        self.assertEqual(changes[0].numeric[0].value,'0')
        missing_number={'complete':True,'changes':[capital,{**policy_timing(text),'numeric':[]}]}
        with self.assertRaisesRegex(ValueError,'comment period'):
            extract_article(article(text),RecordedLLM(lambda stage,ctx:missing_number))
        bad_basis={**policy_timing(text),'description':policy_timing(text)['description'].split(' after publication')[0]}
        with self.assertRaisesRegex(ValueError,'comment period'):
            extract_article(article(text),RecordedLLM(lambda stage,ctx:{'complete':True,'changes':[capital,bad_basis]}))

    def test_collateral_requires_its_own_evidence_span(self):
        from evaluation.real_corpus import load_sources
        text=load_sources()['real-50339f5e620a']['source']['body']
        introduction=text.split('\n\n')[0]
        description='The facility is fully operational and provides credit using PPP loans as collateral.'
        invalid={'complete':True,'changes':[draft(description,evidence_quote=introduction)]}
        llm=RecordedLLM(lambda stage,ctx:invalid)
        with self.assertRaisesRegex(ValueError,'source extract'):
            extract_article(article(text),llm)
        self.assertEqual(len(llm.calls),2)
        sentence=text[text.index("The Federal Reserve's facility"):text.index('using such loans as collateral.')+len('using such loans as collateral.')]
        valid={'complete':True,'changes':[draft(introduction,evidence_quote=introduction),draft(sentence,evidence_quote=text,claim_type='forecast')]}
        change=extract_article(article(text),RecordedLLM(lambda stage,ctx:valid))[1]
        self.assertIn('collateral',change.evidence.quote)
        self.assertEqual(text[change.evidence.start:change.evidence.end],text)

    def test_analysis_receives_accepted_structured_data_and_exact_evidence(self):
        from news_engine.models import Event
        from news_engine.analyze import analyze_event
        from test_mvp import NOW
        change=extract_article(article('The facility is fully operational.'),RecordedLLM())[0]
        event=Event(category='regulatory',event_id='E1',title=change.description,identity=change.identity,what_changed=[change],latest_change_id=change.change_id,created_at=NOW,last_seen_at=NOW,last_material_at=NOW)
        def handler(stage,ctx):
            fact=ctx['current_event']['what_changed'][0]
            self.assertEqual(fact['description'],change.description)
            self.assertEqual(fact['evidence']['quote'],change.evidence.quote)
            self.assertEqual(fact['claim_type'],'actual')
            self.assertIn('funding costs',ctx['treasury_context']['channels'])
            return analysis_reply(ctx,why_it_matters=[],what_to_do=[])
        llm=RecordedLLM(handler)
        result=analyze_event(event,llm,NOW.isoformat())
        self.assertEqual(result.what_changed[0].evidence,change.evidence)
        self.assertEqual(len(llm.calls),1)

    def test_description_cannot_borrow_a_capital_claim_from_another_paragraph(self):
        text='The rule neutralizes regulatory capital effects.\n\nThe rule is effective immediately.'
        response={'complete':True,'changes':[draft('The rule neutralizes regulatory capital effects.',evidence_quote='The rule is effective immediately.')]}
        llm=RecordedLLM(lambda stage,ctx:response)
        with self.assertRaisesRegex(ValueError,'source extract'):
            extract_article(article(text),llm)
        self.assertEqual(len(llm.calls),2)



    def test_operational_source_cannot_omit_collateral_or_label_future_action_actual(self):
        from evaluation.real_corpus import load_sources
        text=load_sources()['real-50339f5e620a']['source']['body']
        intro=text.split('\n\n')[0]
        omitted={'complete':True,'changes':[draft(intro)]}
        llm=RecordedLLM(lambda stage,ctx:omitted)
        with self.assertRaisesRegex(ValueError,'missing material collateral'):
            extract_article(article(text),llm)
        self.assertEqual(len(llm.calls),2)
        future="The change will temporarily modify the Board's rules."
        llm=RecordedLLM(lambda stage,ctx:{'complete':True,'changes':[draft(future,claim_type='actual')]})
        with self.assertRaisesRegex(ValueError,'future rule/facility action'):
            extract_article(article(future),llm)
        self.assertEqual(len(llm.calls),2)


    def test_unknown_direction_does_not_discard_valid_duplicate_evidence(self):
        from news_engine.models import Event
        from news_engine.events import group_change
        from test_mvp import NOW
        text='The Federal Reserve will establish a financing facility backed by PPP loans.'
        original=extract_article(article(text),RecordedLLM(lambda stage,ctx:{'complete':True,'changes':[draft(text,claim_type='announced',direction='unknown')]}))[0]
        current=original.model_copy(update={'change_id':'C2','direction':'increase'})
        event=Event(category='regulatory',event_id='E1',title='Facility',identity=original.identity,what_changed=[original],latest_change_id=original.change_id,created_at=NOW,last_seen_at=NOW,last_material_at=NOW)
        llm=RecordedLLM(lambda stage,ctx:grouping(event_id='E1',relation='duplicate',confidence='high',duplicate_of=original.change_id))
        self.assertEqual(group_change(current,[event],llm).duplicate_of,original.change_id)
        explicit=event.model_copy(update={'what_changed':[original.model_copy(update={'direction':'decrease'})]})
        with self.assertRaisesRegex(ValueError,'direction'):
            group_change(current,[explicit],llm)
        with self.assertRaisesRegex(ValueError,'claim type'):
            group_change(current.model_copy(update={'claim_type':'actual'}),[event],llm)
