"""Reference metrics use authentic source text; deterministic predicted mistakes are synthetic."""
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.semantic import evaluate, load_outputs, load_reference, JudgeResult, validate_judge, DIMENSIONS


class SemanticEvaluationTests(unittest.TestCase):
    def test_frozen_reference_anchors_are_original_and_not_model_labels(self):
        reference,sources=load_reference()
        self.assertGreaterEqual(len(reference['claims']),12)
        for claim in reference['claims']:
            self.assertIn(claim['anchor'],sources[claim['source_id']]['source']['body'])
        self.assertIn('not human-adjudicated',reference['annotation_provenance'])

    def test_wrong_status_and_missing_claims_remain_in_denominator(self):
        _,sources=load_reference(); original=sources['real-a114d8803587']['source']
        quote=original['body'].split('\n\n')[1].split(' Consistent')[0]
        with tempfile.TemporaryDirectory() as folder:
            payload=dict(run_id='R-test',status='completed',sources=[dict(article_id='A-test',url=original['url'],text=original['body'])],events=[dict(event_id='E-test',category='regulatory',score=None,what_changed=[dict(change_id='C-test',source_id='A-test',description=quote,claim_type='proposed',evidence=dict(start=original['body'].index(quote),end=original['body'].index(quote)+len(quote),quote=quote))])])
            Path(folder,'run.json').write_text(json.dumps(payload),encoding='utf-8')
            report,_=evaluate(folder)
        self.assertEqual(report['status']['unambiguous_denominator'],9)
        self.assertEqual(report['status']['matched_unambiguous'],1)
        self.assertEqual(report['status']['proposed_false_positives'],1)
        self.assertEqual(report['status']['confusion_matrix']['actual']['proposed'],1)
        self.assertEqual(report['status']['accuracy_including_missing'],0)
        self.assertEqual(report['provenance']['invalid_spans'],0)

    def test_latest_event_snapshot_removes_old_canonical_claim_from_metrics(self):
        reference,sources=load_reference(); original=sources['real-a114d8803587']['source']
        quote=original['body'].split('\n\n')[1].split(' Consistent')[0]
        source=dict(article_id='A-test',url=original['url'],text=original['body'])
        change=dict(change_id='C-test',source_id='A-test',description=quote,claim_type='actual',
                    evidence=dict(start=original['body'].index(quote),end=original['body'].index(quote)+len(quote),quote=quote))
        first=dict(run_id='R-first',status='completed',sources=[source],events=[dict(event_id='E-test',
            category='regulatory',score=None,what_changed=[change])])
        latest=dict(run_id='R-latest',status='completed',sources=[source],events=[dict(event_id='E-test',
            category='regulatory',score=None,what_changed=[])])
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'2020-01.json').write_text(json.dumps(first),encoding='utf-8')
            Path(folder,'2020-02.json').write_text(json.dumps(latest),encoding='utf-8')
            _,_,_,changes=load_outputs(folder)
            self.assertEqual(changes,{})
            report,context=evaluate(folder)
        self.assertEqual(report['status']['matched_unambiguous'],0)
        self.assertEqual(report['provenance']['checked_changes'],0)
        self.assertEqual({a['source_id'] for a in context['original_articles']},set(reference['workflow_source_ids']))
        self.assertEqual(context['observed_source_ids'],['real-a114d8803587'])

    def test_obsolete_historical_prediction_is_counted_without_coercion(self):
        _,sources=load_reference(); original=sources['real-a114d8803587']['source']
        quote=original['body'].split('\n\n')[1].split(' Consistent')[0]
        payload=dict(run_id='R-old',status='completed',schema_version=5,
            sources=[dict(article_id='A-old',url=original['url'],text=original['body'])],
            events=[dict(event_id='E-old',category='regulatory',score=None,what_changed=[
                dict(text=quote,source_id='A-old',claim_type='unverified',evidence=dict(
                    start=original['body'].index(quote),end=original['body'].index(quote)+len(quote),quote=quote))])])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,'old.json');path.write_text(json.dumps(payload),encoding='utf-8')
            before=path.read_bytes();report,_=evaluate(folder)
            self.assertEqual(path.read_bytes(),before)
        self.assertEqual(report['status']['confusion_matrix']['actual']['unsupported'],1)
        self.assertNotIn('unverified',report['status']['per_class'])
        self.assertEqual(report['status']['accuracy_including_missing'],0)
        self.assertIn(['unverified'],[row['predictions'] for row in report['claims']])

    def test_nullable_offsets_are_unlocated_not_verified_spans(self):
        _,sources=load_reference(); original=sources['real-a114d8803587']['source']
        payload=dict(schema_version=7,run_id='R-null',status='completed',
            sources=[dict(article_id='A-null',url=original['url'],text=original['body'])],
            events=[dict(event_id='E-null',category='regulatory',score=None,what_changed=[
                dict(text='A paraphrased development',source_id='A-null',claim_type='actual',
                     evidence=dict(start=None,end=None,quote='Normalized supporting passage'))])])
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'run.json').write_text(json.dumps(payload),encoding='utf-8')
            report,_=evaluate(folder)
        self.assertEqual(report['provenance']['checked_changes'],1)
        self.assertEqual(report['provenance']['unlocated_evidence'],1)
        self.assertEqual(report['provenance']['spans_checked'],0)
        self.assertEqual(report['provenance']['invalid_spans'],0)
        self.assertEqual(report['provenance']['original_article_mismatches'],0)

    def test_judge_does_not_accept_invented_source_references(self):
        value={dimension:dict(verdict='uncertain',explanation='Needs adjudication',evidence=[]) for dimension in DIMENSIONS}
        value['regulatory_status']['evidence']=[dict(source_id='unknown',quote='fabricated')]
        result=JudgeResult.model_validate(value)
        with self.assertRaisesRegex(ValueError,'exact original-source'):
            validate_judge(result,dict(original_articles=[dict(source_id='A',body='Original evidence')]))
        value['regulatory_status']['evidence']=[dict(source_id='A',quote='Original evidence')]
        validate_judge(JudgeResult.model_validate(value),dict(original_articles=[dict(source_id='A',body='Original evidence')]))
