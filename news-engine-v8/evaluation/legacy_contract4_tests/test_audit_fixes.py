"""Audit regressions: source-backed basis, identity preservation and run clock."""
import unittest,tempfile
from pathlib import Path
from datetime import datetime,timezone
from news_engine.models import ChangeDraft
from news_engine.extract import _validate_change
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from test_mvp import RecordedLLM,draft
from evaluation.real_corpus import load_sources

class AuditTests(unittest.TestCase):
 def test_source_backed_status_basis_and_no_public_field_leak(self):
  text=load_sources()['real-a114d8803587']['source']['body']
  passage=text.split('\n\n')[1]
  value=ChangeDraft(**draft(passage,claim_type='proposed'),claim_type_basis='effective_rule',status_evidence_quote='The rule is effective immediately')
  with self.assertRaisesRegex(ValueError,'contradicts'):_validate_change(value,text)
  _validate_change(value.model_copy(update={'claim_type':'actual'}),text)
  with self.assertRaisesRegex(ValueError,'exact'):_validate_change(value.model_copy(update={'claim_type':'actual','status_evidence_quote':'not present'}),text)

 def test_future_effective_adoption_not_proposal_and_ambiguous_basis(self):
  # Synthetic boundary tests, not real-world benchmark evidence.
  text='The adopted final rule will modify reporting from next year.'
  value=ChangeDraft(**draft(text,claim_type='actual'),claim_type_basis='adopted_rule',status_evidence_quote=text)
  _validate_change(value,text)
  for label,basis in [('proposed','proposed_rule'),('unverified','insufficient_evidence'),('announced','announced_action')]:
   _validate_change(ChangeDraft(**draft('The authority discussed a rule.',claim_type=label),claim_type_basis=basis,status_evidence_quote='The authority discussed a rule.'),'The authority discussed a rule.')

 def test_child_actor_run_clock_and_attributed_summary(self):
  clock=datetime(2026,11,1,tzinfo=timezone.utc)
  child=dict(subject='SBA',action='clarified',occurrence='PPP clarification',scope='PPP')
  parent=dict(subject='Board',action='modifies',occurrence='PPP issue',scope='PPP')
  def handle(stage,ctx):
   if stage=='extract':return dict(complete=True,events=[dict(title='PPP issue',identity=parent,category='regulatory',source_id=ctx['source_id'],changes=[draft(ctx['text'],identity=child)])])
  llm=RecordedLLM(handle)
  with tempfile.TemporaryDirectory() as folder,Store(Path(folder)/'new.sqlite') as store:
   result=run_pipeline(store,llm,[dict(url='https://example.org/sba',title='SBA',text='SBA clarified eligibility.',published_at='2020-04-01T00:00:00Z')],reference_time=clock)
   self.assertEqual(result.status,'completed',result.errors)
   c=result.events[0].what_changed[0]
   self.assertEqual(c.identity.subject,'SBA')
   self.assertEqual(result.events[0].identity.subject,'Board')
   self.assertNotIn('claim_type_basis',c.model_dump())
   context=next(ctx for stage,ctx in llm.calls if stage=='analyze')
   self.assertEqual(context['reference_time'],clock.isoformat())
   self.assertEqual(context['source_times'][0]['published_at'],'2020-04-01T00:00:00+00:00')
   self.assertIn('[actual; source=',result.overall_summary)
   self.assertIn('published=2020-04-01',result.overall_summary)

 def test_fixed_event_novelty_accepts_optional_child_identity(self):
  from news_engine.events import group_change
  from test_w3_rounds import W3Tests,Steps,novelty_reply
  helper=W3Tests();helper.setUp()
  old=helper.change('Board changed eligibility.')
  child=helper.change('A new restriction applies.',key='C2').model_copy(update={'identity':None})
  event=helper.event(old)
  llm=Steps(lambda schema,ctx,stage:novelty_reply(ctx))
  result=group_change(child,[event],llm,selected_event=event)
  self.assertEqual(result.relation,'material')
  self.assertEqual([s for s,_ in llm.calls],['group_novelty'])
