"""R7 false-positive regression and deterministic repair-budget contracts."""
import copy
import json
from pathlib import Path
import unittest

from news_engine.analyze import analyze_event
from news_engine.models import Event
from test_mvp import RecordedLLM, analysis_reply

FIXTURE=json.loads((Path(__file__).parent/'fixtures/r7_analysis_failure.json').read_text(encoding='utf-8'))


class AnalysisRepairTests(unittest.TestCase):
    def event(self):
        legacy=copy.deepcopy(FIXTURE['event'])
        legacy['category']='regulatory'
        legacy['what_changed']=legacy.pop('major_changes')
        legacy.pop('summary',None)
        legacy.pop('status',None)
        legacy.pop('treasury_implications',None)
        legacy['why_it_matters']=[]
        legacy['what_to_do']=[]
        return Event.model_validate(legacy)

    def grounded_response(self):
        return analysis_reply(primary_change_id=FIXTURE['first_context']['changes'][0]['change_id'],
            impact=1,relevance=3,urgency=2,impact_basis='financial_action',
            relevance_basis='local_unproven',urgency_basis='routine_or_unknown',
            has_treasury_action_or_constraint=False,reasoning=copy.deepcopy(FIXTURE['repair_response']['reasoning']),
            why_it_matters=['Capital neutrality may affect eligible lenders; company applicability is not established.'],
            what_to_do=[])

    def test_r7_evidence_qualified_score_reason_is_not_a_no_impact_claim(self):
        response=self.grounded_response()
        self.assertIn('evidenced company obligations',response['reasoning']['impact'])
        llm=RecordedLLM(lambda stage,ctx:response)
        result=analyze_event(self.event(),llm,FIXTURE['first_context']['reference_time'])
        self.assertEqual(len(llm.calls),1)
        self.assertEqual(result.score.reasoning['impact'],response['reasoning']['impact'])
        self.assertEqual(result.score.total,39)
        self.assertEqual(result.what_to_do,[])

    def test_source_uncertainty_is_not_a_company_relationship_assertion(self):
        response=self.grounded_response()
        response['why_it_matters']=['The source does not establish company counterparties or subsidiary exposure.']
        llm=RecordedLLM(lambda stage,ctx:response)
        self.assertEqual(analyze_event(self.event(),llm,FIXTURE['first_context']['reference_time']).why_it_matters,response['why_it_matters'])
        self.assertEqual(len(llm.calls),1)

    def test_missing_implementation_evidence_is_not_an_implementation_claim(self):
        event=self.event().model_copy(deep=True)
        for change in event.what_changed:
            change.claim_type='announced'
        response=self.grounded_response()
        response['why_it_matters']=[]
        response['reasoning']['urgency']='The source does not establish a currently operative implementation or a company deadline.'
        llm=RecordedLLM(lambda stage,ctx:response)
        self.assertIsNotNone(analyze_event(event,llm,FIXTURE['first_context']['reference_time']).score)
        self.assertEqual(len(llm.calls),1)

    def test_numeric_repair_remains_single_and_does_not_weaken_grounding(self):
        valid=self.grounded_response()
        invalid={**valid,'what_to_do':['If applicable, review the 60 days comment window.']}
        llm=RecordedLLM(lambda stage,ctx:valid if 'repair' in ctx else invalid)
        result=analyze_event(self.event(),llm,FIXTURE['first_context']['reference_time'])
        self.assertEqual(len(llm.calls),2)
        self.assertIn('60',llm.calls[1][1]['repair'])
        self.assertEqual(result.what_changed[-1].numeric[0].value,'30')
        failed=RecordedLLM(lambda stage,ctx:invalid)
        with self.assertRaisesRegex(ValueError,'after one repair'):
            analyze_event(self.event(),failed,FIXTURE['first_context']['reference_time'])
        self.assertEqual(len(failed.calls),2)

    def test_analysis_receives_canonical_evidence_context_and_preserves_reasoning(self):
        event=self.event()
        response=self.grounded_response()
        response.update(impact=1,relevance=1,urgency=1,
            impact_basis='commentary_or_forecast',relevance_basis='speculative',
            urgency_basis='routine_or_unknown',
            reasoning={'impact':'Potential company decision impact remains speculative.',
                'relevance':'The evidence does not establish company applicability.',
                'urgency':'No company response deadline is established.'},
            why_it_matters=['Company applicability is unknown; the evidence does not establish a direct Treasury channel.'],
            what_to_do=['Continue low-priority monitoring if company relevance emerges.'])
        llm=RecordedLLM(lambda stage,ctx:response)
        result=analyze_event(event,llm,'2026-11-01T00:00:00Z')
        context=llm.calls[0][1]
        self.assertEqual(context['reference_time'],'2026-11-01T00:00:00Z')
        self.assertIsNone(context['treasury_context']['company_exposure_data'])
        self.assertEqual(context['current_event']['identity'],event.identity.model_dump(mode='json'))
        supplied=context['current_event']['what_changed']
        self.assertTrue(supplied)
        for change in supplied:
            self.assertIn('claim_type',change)
            self.assertIn('numeric',change)
            self.assertIn('corroborating_sources',change)
            self.assertTrue(change['evidence']['quote'])
        self.assertEqual(result.what_changed,event.what_changed)
        self.assertEqual(result.score.total,20)
        self.assertEqual(result.score.reasoning,response['reasoning'])
        self.assertEqual(result.what_to_do,response['what_to_do'])
