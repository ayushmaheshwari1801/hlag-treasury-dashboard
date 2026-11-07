"""Offline W4 regression contracts; all model responses are scripted."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from news_engine.analyze import (AnalysisDraft, ANALYSIS_PROMPT, analyze_batch,
    analysis_batches, numeric_quantities, validate_analysis, validate_numeric_grounding)
from news_engine.models import Article, Event
from news_engine import models as score_policy


def article(key='A001', text='The facility provides USD 5 million at 2.5 percent for 30 days.'):
    return Article(article_id=key,url='https://example.test/'+key,title='Liquidity facility',
        text=text,publisher='Original source',published_at=datetime(2026,1,1,tzinfo=timezone.utc),
        discovered_at=datetime(2026,1,2,tzinfo=timezone.utc),normalized_url='https://example.test/'+key,content_hash=key)


def event(key='E001', source='A001'):
    return Event(event_id=key,title='Liquidity facility',category='regulatory',
                 article_ids=[source],summary='Reports about a liquidity facility.')


def analysis(**updates):
    value=dict(summary='The facility provides USD 5 million at 2.5 percent for 30 days.',
        why_it_matters=['If applicable, the facility may affect financing options.'],
        what_to_do=['Check eligibility before incorporating it into funding plans.'],
        impact=3,relevance=4,urgency=3,
        reasoning={'impact':'Funding terms may affect financing plans.',
                   'relevance':'Company eligibility needs checking.',
                   'urgency':'Monitor reported availability.'},
        impact_basis='financial_action',relevance_basis='direct_channel',
        urgency_basis='operative_control_or_8_30_days',has_treasury_action_or_constraint=True)
    value.update(updates)
    return value


class Scripted:
    def __init__(self, responses):
        self.responses=list(responses); self.calls=[]
    def complete(self,schema,prompt,context,stage):
        self.calls.append((stage,deepcopy(context)))
        value=self.responses.pop(0)
        if isinstance(value,Exception):
            raise value
        return value


class AnalysisV8Tests(unittest.TestCase):
    def test_original_articles_directly_and_r5(self):
        source=article(); output=validate_analysis(event(),analysis(),{'A001':source})
        self.assertEqual(output.score.total,67)
        self.assertEqual(output.summary,analysis()['summary'])
        self.assertNotIn('primary_entry_index',AnalysisDraft.model_fields)
        self.assertNotIn('what_changed',ANALYSIS_PROMPT)
        self.assertIn('Hard caps and cross-dimensional restrictions are disabled',ANALYSIS_PROMPT)

    @patch("news_engine.models.NUMERIC_GROUNDING_ENABLED", True)
    def test_explicitly_enabled_number_checks_fail_every_prose_field(self):
        for field in ['summary','why_it_matters','what_to_do','reasoning']:
            with self.subTest(field=field):
                updates={field:'The amount is USD 99 million.'}
                if field in {'why_it_matters','what_to_do'}:
                    updates[field]=[updates[field]]
                elif field=='reasoning':
                    updates[field]={**analysis()['reasoning'],'impact':updates[field]}
                with self.assertRaisesRegex(ValueError,'unsupported'):
                    validate_analysis(event(),analysis(**updates),{'A001':article()})

    @patch("news_engine.models.NUMERIC_GROUNDING_ENABLED", True)
    def test_numeric_currency_scale_percent_and_units(self):
        sources=[article(text='USD5 million, EUR 7 billion, 30 days, and 2.5 percent.')]
        validate_numeric_grounding(['$5m and EUR 7,000 million; 30 days and 2.5%.'],sources)
        for text in ['USD 7 billion','EUR 5 million','30%','2.5 days','$6 million','USD 5 billion','-2.5%']:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError,'unsupported'):
                validate_numeric_grounding([text],sources)

    @patch("news_engine.models.NUMERIC_GROUNDING_ENABLED", True)
    def test_currency_symbols_and_original_source_only(self):
        sources=[article(text='The amount is EUR 7 million, GBP 8 million and JPY 9 million.')]
        validate_numeric_grounding(['\u20ac7 million, \u00a38 million and \u00a59 million.'],sources)
        with self.assertRaisesRegex(ValueError,'unsupported'):
            validate_numeric_grounding(['\u20ac8 million.'],sources)
        value=article(text='No amount is reported.').model_copy(update={
            'url':'https://example.test/999','article_id':'A999','content_hash':'999'})
        with self.assertRaisesRegex(ValueError,'unsupported'):
            validate_numeric_grounding(['999'],[value])

    @patch("news_engine.models.NUMERIC_GROUNDING_ENABLED", True)
    def test_does_not_accept_computed_delta(self):
        sources=[article(text='The rate moved from 2% to 5%.')]
        with self.assertRaisesRegex(ValueError,'unsupported'):
            validate_numeric_grounding(['The rate rose by 3 percentage points.'],sources)

    @patch("news_engine.models.NUMERIC_GROUNDING_ENABLED", True)
    def test_explicitly_enabled_checks_use_only_assigned_sources(self):
        sources={'A001':article(),'A002':article('A002','The amount is USD 99 million.')}
        with self.assertRaisesRegex(ValueError,'unsupported'):
            validate_analysis(event(),analysis(summary='USD 99 million.'),sources)

    def test_default_numeric_grounding_disabled_for_all_prose(self):
        self.assertFalse(score_policy.NUMERIC_GROUNDING_ENABLED)
        source=article(text='No monetary amounts, dates, rates or durations are reported.')
        original=source.model_dump(mode='json')
        for text in ['USD 99 billion at -7.5% for 999 days on 2099-12-31.',
                     'EUR 50 million rather than GBP 99 trillion; a 3 percentage point increase.']:
            with self.subTest(text=text):
                value=analysis(summary=text,why_it_matters=[text],what_to_do=[text],
                    reasoning=dict(impact=text,relevance=text,urgency=text))
                output=validate_analysis(event(),value,{'A001':source})
                self.assertEqual(output.summary,text)
                self.assertEqual(output.why_it_matters,[text])
                self.assertEqual(output.what_to_do,[text])
                self.assertEqual(output.score.reasoning,value['reasoning'])
                self.assertEqual(output.score.total,67)
        self.assertEqual(source.model_dump(mode='json'),original)

    def test_disabled_numeric_helper_is_not_called_but_structure_and_r5_remain(self):
        with patch('news_engine.analyze.validate_numeric_grounding',side_effect=AssertionError('inactive checker called')):
            self.assertEqual(validate_analysis(event(),analysis(summary='USD 99 million.'),{'A001':article()}).score.total,67)
            for updates in [dict(impact=6),dict(urgency=True),dict(summary=''),
                            dict(reasoning=dict(impact='',relevance='Reason.',urgency='Reason.'))]:
                with self.subTest(updates=updates),self.assertRaises(ValueError):
                    validate_analysis(event(),analysis(**updates),{'A001':article()})
            with self.assertRaisesRegex(ValueError,'missing assigned'):
                validate_analysis(event(),analysis(),{})

    def test_full_original_context_no_length_limit(self):
        source=article(text='Full original '+('text '*100000)+' USD 5 million.')
        e=event(); batches,errors=analysis_batches([e],{'A001':source},'2026-01-02',max_chars=1)
        self.assertFalse(errors)
        self.assertEqual(batches[0][1]['events'][0]['articles'][0]['text'],source.text)
        self.assertEqual(batches[0][1]['events'][0]['articles'][0]['published_at'],'2026-01-01T00:00:00Z')
        self.assertNotIn('what_changed',batches[0][1]['events'][0]['current_event'])

    def test_reversed_outputs_bind_by_id(self):
        events=[event(),event('E002','A002')]
        batches,_=analysis_batches(events,{'A001':article(),'A002':article('A002')},'2026-01-02')
        llm=Scripted([{'results':[{'event_id':'E002','analysis':analysis()},
                                   {'event_id':'E001','analysis':analysis()}]}])
        accepted,errors=analyze_batch(events,llm,batches[0][1])
        self.assertEqual(set(accepted),{'E001','E002'}); self.assertFalse(errors)
        self.assertEqual(len(llm.calls),1)

    def test_bad_sibling_bounded_repairs_good_sibling_retained(self):
        events=[event(),event('E002','A002')]
        batches,_=analysis_batches(events,{'A001':article(),'A002':article('A002')},'2026-01-02')
        llm=Scripted([{'results':[{'event_id':'E001','analysis':analysis()},
                    {'event_id':'E002','analysis':analysis(urgency=6)} ]},
                    analysis(urgency=6),analysis(urgency=6)])
        accepted,errors=analyze_batch(events,llm,batches[0][1])
        self.assertEqual(set(accepted),{'E001'})
        self.assertEqual(len(errors),1)
        self.assertEqual([stage for stage,_ in llm.calls],['analyze_batch','analyze','analyze'])
        self.assertEqual([c['current_event']['event_id'] for stage,c in llm.calls if stage=='analyze'],['E002','E002'])
        self.assertIn('2 automatic repair attempts',errors[0].message)

    def test_missing_duplicate_ids_repair_only_affected(self):
        events=[event(),event('E002','A002')]
        batches,_=analysis_batches(events,{'A001':article(),'A002':article('A002')},'2026-01-02')
        for malformed in [[],[{'event_id':'E002','analysis':analysis()}]*2]:
            with self.subTest(malformed=malformed):
                llm=Scripted([{'results':[{'event_id':'E001','analysis':analysis()},*malformed]},analysis()])
                accepted,errors=analyze_batch(events,llm,batches[0][1])
                self.assertEqual(set(accepted),{'E001','E002'}); self.assertFalse(errors)
                self.assertEqual(len(llm.calls),2)
                self.assertEqual(llm.calls[1][1]['current_event']['event_id'],'E002')

    def test_malformed_outer_item_does_not_repair_valid_sibling(self):
        events=[event(),event('E002','A002')]
        batches,_=analysis_batches(events,{'A001':article(),'A002':article('A002')},'2026-01-02')
        llm=Scripted([{'results':[{'event_id':'E001','analysis':analysis()},
                                   {'event_id':'E002'}]},analysis()])
        accepted,errors=analyze_batch(events,llm,batches[0][1])
        self.assertEqual(set(accepted),{'E001','E002'}); self.assertFalse(errors)
        self.assertEqual(len(llm.calls),2)
        self.assertEqual(llm.calls[1][1]['current_event']['event_id'],'E002')

    def test_provider_failure_is_explicit_without_retry(self):
        batches,_=analysis_batches([event()],{'A001':article()},'2026-01-02')
        llm=Scripted([RuntimeError('provider failed')])
        accepted,errors=analyze_batch([event()],llm,batches[0][1])
        self.assertFalse(accepted); self.assertEqual(len(errors),1); self.assertEqual(len(llm.calls),1)

    def test_caps_flag_restores_applicable_r5_rules(self):
        value=analysis(impact=5,relevance=1,urgency=5,impact_basis='commentary_or_forecast')
        self.assertEqual(validate_analysis(event(),value,{'A001':article()}).score.total,72)
        with patch('news_engine.models.R5_CAPS_ENABLED',True), self.assertRaises(ValueError):
            validate_analysis(event(),value,{'A001':article()})

    def test_batch_size_validation_and_missing_sources(self):
        with self.assertRaises(ValueError):
            analysis_batches([event()],{},'2026-01-02',batch_size=5)
        batches,errors=analysis_batches([event()],{},'2026-01-02')
        self.assertFalse(batches); self.assertEqual(errors[0].code,'missing_article')


if __name__=='__main__':
    unittest.main()
