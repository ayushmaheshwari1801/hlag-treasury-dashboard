"""Regressions for authentic citation failure and minimal persistent identity."""
from pathlib import Path
from datetime import datetime, timezone
import json
import tempfile
import unittest

from test_mvp import RecordedLLM, article, draft, grouping, policy_timing, analysis_reply
from news_engine.extract import extract_article
from news_engine.events import group_change
from news_engine.models import Event, EventIdentity
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from evaluation.real_corpus import load_sources

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

class EventFixTests(unittest.TestCase):
    def test_exact_authentic_ellipsis_failure_and_targeted_repair(self):
        text = load_sources()['real-a114d8803587']['source']['body']
        rejected = json.loads((Path(__file__).parent/'fixtures/capital_rule_ellipsis_reply.json').read_text(encoding='utf-8'))
        invalid = RecordedLLM(lambda stage,ctx: rejected)
        with self.assertRaisesRegex(ValueError,'exact nonempty substring'):
            extract_article(article(text),invalid)
        self.assertEqual(len(invalid.calls),2)
        self.assertEqual(invalid.calls[1][1]['rejected_response']['changes'][0]['evidence_quote'],rejected['changes'][0]['evidence_quote'])
        paragraph = text.split('\n\n')[1]
        repaired = {'complete':True,'changes':[draft(paragraph,direction='unknown',numeric=[{'value':'0','unit':'percent','kind':'level','source_quote':'zero percent risk weight'}])]}
        repaired['changes'].append(policy_timing(text))
        llm = RecordedLLM(lambda stage,ctx: repaired if 'repair' in ctx else rejected)
        change = extract_article(article(text),llm)[0]
        self.assertEqual(change.numeric[0].value,'0')
        self.assertEqual(text[change.evidence.start:change.evidence.end],paragraph)
        self.assertEqual(len(llm.calls),2)

    def test_new_event_uses_extraction_identity_without_extra_call(self):
        identity={'subject':'Bank regulators','action':'capital rule','occurrence':'PPP capital treatment rule','scope':'PPP loans','location':'US'}
        changes=extract_article(article('Regulators announced a capital rule for PPP loans.'),RecordedLLM(lambda stage,ctx:{'complete':True,'changes':[draft(ctx['text'],identity=identity,claim_type='announced')]}))
        llm=RecordedLLM()
        result=group_change(changes[0],[],llm)
        self.assertEqual(result.identity.action,'capital rule')
        self.assertEqual(result.identity.scope,'PPP loans')
        self.assertEqual(len(llm.calls),0)

    def test_unknown_scope_cannot_force_a_merge(self):
        change=extract_article(article(),RecordedLLM())[0]
        event=Event(category='regulatory',event_id='E1',title='Policy decision',identity=EventIdentity(subject='Central bank',action='decision',occurrence='Policy decision',scope='unknown'),what_changed=[change],latest_change_id=change.change_id,created_at=NOW,last_seen_at=NOW,last_material_at=NOW)
        llm=RecordedLLM(lambda stage,ctx:grouping(event_id='E1',relation='material',confidence='high'))
        result=group_change(change,[event],llm)
        self.assertEqual(result.relation,'uncertain')
        self.assertIsNone(result.event_id)



    def test_late_material_evidence_keeps_maximum_source_timestamp(self):
        def handle(stage,ctx):
            if stage=='group':
                return grouping(event_id=ctx['candidates'][0]['event_id'],relation='material',confidence='high')
        with tempfile.TemporaryDirectory() as folder:
            with Store(Path(folder)/'news.sqlite') as store:
                llm=RecordedLLM(handle)
                first=run_pipeline(store,llm,[{'url':'https://example.org/new','title':'New policy decision','text':'Central bank policy decision changed funding conditions.','published_at':'2026-01-02T00:00:00Z'}])
                second=run_pipeline(store,llm,[{'url':'https://example.org/old','title':'Older policy decision','text':'Central bank policy decision had changed funding conditions earlier.','published_at':'2026-01-01T00:00:00Z'}])
                self.assertEqual(second.events[0].event_id,first.events[0].event_id)
                self.assertEqual(second.events[0].last_material_at,first.events[0].last_material_at)

                self.assertEqual(len(second.events[0].what_changed),2)


    def test_analysis_cannot_add_an_unsourced_regulation_identifier(self):
        from news_engine.analyze import analyze_event
        change=extract_article(article('The Board announced changes to its rules.'),RecordedLLM())[0]
        event=Event(category='regulatory',event_id='E1',title='Rule',identity=change.identity,what_changed=[change],latest_change_id=change.change_id,created_at=NOW,last_seen_at=NOW,last_material_at=NOW)
        llm=RecordedLLM(lambda stage,ctx:analysis_reply(ctx,why_it_matters=['Regulation O was modified.']))
        with self.assertRaisesRegex(ValueError,'legal identifier'):
            analyze_event(event,llm,NOW.isoformat())
        self.assertEqual(len(llm.calls),2)
