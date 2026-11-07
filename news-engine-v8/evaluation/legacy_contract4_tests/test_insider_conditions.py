"""Authentic qualified insider-rule Changes survive W3 and public projection."""
from pathlib import Path
import tempfile
import unittest
from evaluation.real_corpus import load_sources
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from news_engine.events import group_change
from test_mvp import draft, analysis_reply
from test_w3_rounds import Steps, identity_reply, novelty_reply


class InsiderConditionsTests(unittest.TestCase):
    def test_authentic_board_conditions_remain_canonical_separate_from_sba(self):
        source=load_sources()['real-5e84489fdd4c']['source']
        intro,_,last=source['body'].split('\n\n')
        announcement,eligibility=intro.split(' The change',1)
        eligibility='The change'+eligibility
        clarification,qualified=last.split(" The Board's change",1)
        qualified="The Board's change"+qualified
        board=dict(subject='Federal Reserve Board',action='temporary rule modification',
            occurrence='Board PPP insider lending rule modification',scope='Board PPP insider lending')
        sba=dict(subject='Small Business Administration',action='clarification',
            occurrence='SBA PPP lending clarification',scope='SBA PPP insider loan conditions')
        compared=[]
        def handler(schema,ctx,stage):
            if stage=='extract':
                return {'complete':True,'changes':[
                    draft(announcement,identity=board,claim_type='announced',evidence_quote=intro),
                    draft(eligibility,identity=board,claim_type='announced',evidence_quote=intro),
                    draft(clarification,identity=sba,claim_type='actual',evidence_quote=last),
                    draft(qualified,identity=board,claim_type='announced',evidence_quote=last)]}
            if stage=='group_identity':
                if ctx['change']['identity']['subject']==sba['subject']:
                    return identity_reply(ctx,'new')
                event=next(c for c in ctx['candidates'] if c['identity']['subject']==board['subject'])
                return identity_reply(ctx,event_id=event['event_id'])
            if stage=='group_novelty':
                if ctx['change']['description']==qualified:
                    accepted=ctx['candidates'][0]['changes']
                    self.assertFalse(any('SBA\'s rules and restrictions' in c['description'] for c in accepted))
                    compared.append(next(c['change_id'] for c in accepted if c['description']==eligibility))
                    return {**novelty_reply(ctx),'compared_change_id':compared[-1]}
                return novelty_reply(ctx)
            return analysis_reply(ctx)
        llm=Steps(handler)
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'news.sqlite'
            raw={'url':source['url'],'title':source['title'],'text':source['body']}
            with Store(database) as store:
                result=run_pipeline(store,llm,[raw],workers=1)
                self.assertEqual(result.status,'completed')
                self.assertEqual(len(store.events()),2)
                event=next(e for e in result.events if e.identity.subject==board['subject'])
                changes=event.what_changed
                self.assertEqual(len(changes),3)
                condition=next(c for c in changes if c.description==qualified)
                self.assertIsNone(condition.duplicate_of)
                self.assertEqual(condition.claim_type,'announced')
                self.assertIn("consistent with SBA's rules and restrictions",condition.description)
                self.assertIn('The change only applies to PPP loans.',condition.description)
                self.assertEqual(source['body'][condition.evidence.start:condition.evidence.end],last)
                actual=next(e for e in result.events if e.identity.subject==sba['subject'])
                self.assertEqual(actual.what_changed[0].claim_type,'actual')
                self.assertNotEqual(actual.event_id,event.event_id)
                # Once qualifications are captured, the equivalent report may be duplicate.
                repeated=condition.model_copy(update={'change_id':'C-repeat','source_id':'A-repeat'})
                duplicate=Steps(lambda schema,ctx,stage:identity_reply(ctx) if stage=='group_identity' else
                    {**novelty_reply(ctx,'duplicate'),'compared_change_id':condition.change_id})
                decision=group_change(repeated,[event],duplicate)
                self.assertEqual(decision.duplicate_of,condition.change_id)
                self.assertTrue(compared)
                event_id=event.event_id
            count=len(llm.calls)
            with Store(database) as store:
                rerun=run_pipeline(store,llm,[raw],workers=1)
                self.assertEqual(rerun.status,'completed')
                self.assertEqual(len(llm.calls),count)
                self.assertEqual(len(store.events()),2)
                self.assertTrue(any(e.event_id==event_id for e in store.events()))
                self.assertEqual(len(store.changes()),4)
