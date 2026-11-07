"""Scoped source/identity contracts; recorded replies do not prove model semantics."""
from pathlib import Path
import tempfile
import unittest
from evaluation.real_corpus import load_sources
from news_engine.extract import extract_article
from news_engine.events import retrieve_candidates
from news_engine.models import Event, EventIdentity
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from test_mvp import article, draft, grouping, RecordedLLM, NOW


class TargetedCorrectnessTests(unittest.TestCase):
    def test_authentic_insider_clarification_and_future_action_keep_separate_claim_types(self):
        text=load_sources()['real-5e84489fdd4c']['source']['body']
        paragraph=text.split('\n\n')[-1]
        actual,future=paragraph.split(" The Board's change",1)
        future="The Board's change"+future
        replies=[draft(actual,evidence_quote=paragraph,claim_type='actual'),
            draft(future,evidence_quote=paragraph,claim_type='announced')]
        llm=RecordedLLM(lambda stage,ctx:{'complete':True,'changes':replies})
        changes=extract_article(article(text),llm)
        self.assertEqual([c.claim_type for c in changes],['actual','announced'])
        self.assertNotIn('will allow',changes[0].description)
        self.assertIn('will allow',changes[1].description)
        self.assertIn('subject to certain limits and without favoritism',changes[0].description)
        for c in changes:
            self.assertEqual(text[c.evidence.start:c.evidence.end],paragraph)
        self.assertEqual(len(llm.calls),1)

    def test_occurrence_identity_beats_keyword_heavy_distractors_in_top_five(self):
        identity={'subject':'Prudential authority','action':'capital rule',
            'occurrence':'Interim final Rule Alpha','scope':'Lender capital'}
        original=extract_article(article('Capital treatment is neutral.',key='original'),
            RecordedLLM(lambda stage,ctx:{'complete':True,'changes':[draft(ctx['text'],identity=identity)]}))[0]
        target=Event(category='regulatory',event_id='TARGET',title='Capital treatment',identity=EventIdentity(**identity),
            what_changed=[original],latest_change_id=original.change_id,created_at=NOW,last_seen_at=NOW,last_material_at=NOW)
        new=extract_article(article('A zero risk weight applies to program loans.',key='new'),
            RecordedLLM(lambda stage,ctx:{'complete':True,'changes':[draft(ctx['text'],identity={**identity,'action':'clarifies','scope':'Program loan risk weight'})]}))[0]
        distractors=[target.model_copy(update={'event_id':f'D{i}',
            'title':new.description+' Prudential authority loan risk capital program',
            'identity':EventIdentity(subject='Other authority',action='facility launch',occurrence='Credit facility',scope='Program loans')}) for i in range(6)]
        candidates=retrieve_candidates(new,distractors+[target])
        self.assertEqual(candidates[0].event_id,'TARGET')
        self.assertEqual(len(candidates),7)

    def test_same_rule_provisions_restart_and_separate_instruments(self):
        sources=load_sources()
        capital=sources['real-a114d8803587']['source']['body']
        identity={'subject':'federal bank regulatory agencies','action':'modifies',
            'occurrence':'interim final rule','scope':'PPP regulatory capital treatment'}
        paragraphs=capital.split('\n\n')
        neutral,risk=paragraphs[1].split(' Consistent',1)
        risk='Consistent'+risk
        timing=paragraphs[-1]
        proposals=[draft(neutral,identity=identity),draft(risk,identity={**identity,'action':'clarifies','scope':'PPP loan risk weight'},
            numeric=[{'value':'0','unit':'percent','kind':'level','source_quote':'zero percent'}]),
            draft(timing,identity=identity,numeric=[{'value':'30','unit':'days','kind':'threshold',
                'comparison_basis':'after publication in the Federal Register','source_quote':'30 days'}])]
        seen=[]
        def handler(stage,ctx):
            if stage=='extract':
                if ctx['title']=='capital': return {'complete':True,'changes':proposals}
                return {'complete':True,'changes':[draft(ctx['text'],identity=identity if ctx['title']=='update' else {**identity,'occurrence':ctx['title'],'scope':ctx['title']})]}
            if stage=='group':
                seen.append(ctx)
                if ctx['change']['identity']['occurrence']=='interim final rule':
                    matching=next(c for c in ctx['candidates'] if c['identity']['occurrence']=='interim final rule')
                    return grouping(event_id=matching['event_id'],confidence='high',relation='material',identity=identity)
                return grouping(identity=ctx['change']['identity'])
        llm=RecordedLLM(handler)
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'news.sqlite'
            with Store(database) as store:
                first=run_pipeline(store,llm,[{'url':'https://example.org/capital','title':'capital','text':capital}])
                self.assertEqual(first.status,'completed')
                self.assertEqual(len(store.events()),1)
                event_id=store.events()[0].event_id
                self.assertEqual(len(store.events()[0].what_changed),3)
                self.assertEqual(seen[0]['candidates'][0]['event_id'],event_id)
            with Store(database) as store:
                update=run_pipeline(store,llm,[{'url':'https://example.org/update','title':'update','text':'The interim final rule capital treatment remains in force.'}])
                self.assertEqual(update.events[0].event_id,event_id)
                for title,text in [('PPP credit facility','The authority launched a PPP credit facility.'),
                    ('Different final capital decision','The authority adopted a different final capital decision.')]:
                    result=run_pipeline(store,llm,[{'url':'https://example.org/'+title.replace(' ','-'),'title':title,'text':text}])
                    self.assertEqual(result.status,'completed')
                    self.assertNotEqual(result.events[0].event_id,event_id)
                self.assertEqual(len(store.events()),3)
                self.assertEqual(len(next(e for e in store.events() if e.event_id==event_id).what_changed),4)
