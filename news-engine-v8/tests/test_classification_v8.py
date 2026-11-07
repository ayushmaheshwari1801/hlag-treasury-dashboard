"""Offline contracts use scripted responses; they do not measure model quality."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import unittest
from news_engine.group_first import (group_articles, classify_articles,
    ArticleGroup, EventProposal, StoredProposal, ArticleClassificationDraft, classification_schema)
from news_engine.models import Article
from news_engine.events import associate_event, consolidate_drafts
from news_engine.id_constraints import IDConstraintError


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def article(index, *, text=None, published=True, day=0):
    return Article(article_id=f'A{index:03}', url=f'https://example.com/{index}',
        title=f'Article {index}', text=text or f'Liquidity facility financing announcement detail {index}',
        publisher='Source', published_at=NOW + timedelta(days=day) if published else None,
        discovered_at=NOW + timedelta(days=day), normalized_url=f'https://example.com/{index}',
        content_hash=str(index))


class Reply:
    def __init__(self, events, irrelevant=(), complete=True):
        definitions=[]; assignments={}
        for index,event in enumerate(events):
            handle=f'P{index+1}'
            definitions.append({key:value for key,value in event.items() if key!='article_ids'} | {'issue_id':handle})
            for article_id in event['article_ids']:
                assignments[article_id]=[assignments[article_id],handle] if article_id in assignments else handle
        for article_id in irrelevant:
            assignments[article_id]=[assignments[article_id],None] if article_id in assignments else None
        self.value = dict(complete=complete, events=definitions, assignments=assignments)
        self.context = None

    def complete(self, schema, prompt, context, stage):
        self.context = context
        assert stage == 'classify'
        return self.value


def proposal(ids, title='Liquidity facility', category='regulatory'):
    return dict(title=title, summary=f'Reports concerning {title}.', category=category, article_ids=ids)


class SameIssue:
    def complete(self, schema, prompt, context, stage):
        issue = context['event_proposal']['title']
        match = next((c for c in context['candidates'] if c['title'] == issue), None)
        return dict(decision='same' if match else 'new',
                    event_id=match['event_id'] if match else None, reason='Scripted issue label')


class ClassificationTests(unittest.TestCase):
    def test_incomplete_classification_repairs_once_without_losing_sources(self):
        values = [article(1), article(2)]
        reply = Reply([proposal(['A001','A002'])])
        calls = []
        class IncompleteFirst:
            def complete(inner, schema, prompt, context, stage):
                calls.append(context)
                if len(calls) == 1:
                    return {**reply.value, 'complete': False, 'assignments': {'A001': None, 'A002': None}}
                self.assertIn('repair', context)
                self.assertEqual(context['sources'], calls[0]['sources'])
                return reply.value
        group = ArticleGroup(group_key='G1', topic='news', article_ids=['A001','A002'])
        proposals, _, _ = classify_articles(group, {a.article_id:a for a in values}, {}, IncompleteFirst())
        self.assertEqual(len(calls), 2)
        self.assertEqual(proposals[0].event.article_ids, ['A001','A002'])
        class ProviderFailure:
            def complete(inner, *args, **kwargs):
                calls.append(None)
                raise RuntimeError('Provider unavailable')
        with self.assertRaises(RuntimeError):
            classify_articles(group, {a.article_id:a for a in values}, {}, ProviderFailure())
        self.assertEqual(len(calls), 3)

    def test_matching_uses_originals_despite_misleading_summary(self):
        from news_engine.group_first import DraftCandidate
        original = article(1, text='The central bank establishes the Orion lending facility. ' * 500)
        update = article(2, text='The Orion lending facility is now operational.')
        old = DraftCandidate(event_id='D1', **proposal([original.article_id], title='Credit program'))
        new = EventProposal(**proposal([update.article_id], title='Operational launch'))
        class EvidenceMatcher:
            def complete(inner, schema, prompt, context, stage):
                self.assertEqual(context['event_proposal']['source_articles'][0]['text'], update.text)
                self.assertEqual(context['candidates'][0]['source_articles'][0]['text'], original.text)
                return dict(decision='same', event_id='D1', reason='Same original instrument')
        result = associate_event(new, [old], EvidenceMatcher(), articles={a.article_id:a for a in [original,update]})
        self.assertEqual(result.event_id, 'D1')
        with self.assertRaisesRegex(ValueError, 'original Article'):
            associate_event(new, [old], EvidenceMatcher(), articles={update.article_id:update})

    def classify(self, values, events, **kwargs):
        group = ArticleGroup(group_key='G1', topic='news', article_ids=[a.article_id for a in values])
        return classify_articles(group, {a.article_id: a for a in values}, {}, Reply(events, **kwargs))

    def test_four_related_one_event(self):
        values = [article(i) for i in range(4)]
        result, errors, failed = self.classify(values, [proposal([a.article_id for a in values])])
        self.assertEqual((len(result), errors, failed), (1, [], set()))
        self.assertEqual(len(result[0].event.article_ids), 4)

    def test_four_articles_two_issues_within_one_group(self):
        values = [article(i) for i in range(4)]
        result, _, _ = self.classify(values, [proposal(['A000', 'A001']), proposal(['A002', 'A003'], 'Independent capital rule')])
        self.assertEqual(len(result), 2)

    def test_four_unrelated_articles_four_events(self):
        values = [article(i) for i in range(4)]
        result, _, _ = self.classify(values, [proposal([a.article_id], f'Issue {i}') for i, a in enumerate(values)])
        self.assertEqual(len(result), 4)

    def test_single_multisubject_article_has_one_primary_event(self):
        values = [article(1, text='Main financing announcement. Secondary unrelated tariff subject.')]
        result, _, _ = self.classify(values, [proposal(['A001'])])
        self.assertEqual(len(result), 1)
        with self.assertRaises(ValueError):
            self.classify(values, [proposal(['A001']), proposal(['A001'], 'Secondary subject')])

    def test_complete_partition_rejects_foreign_missing_duplicate_and_incomplete(self):
        values = [article(1), article(2)]
        for events, kwargs in [([proposal(['A001'])], {}),
                               ([proposal(['A001', 'Aforeign'])], {}),
                               ([proposal(['A001', 'A002']), proposal(['A001'])], {}),
                               ([proposal(['A001', 'A002'])], {'irrelevant': ['A001']}),
                               ([proposal(['A001', 'A002'])], {'complete': False})]:
            with self.subTest(events=events, kwargs=kwargs), self.assertRaises(ValueError):
                self.classify(values, events, **kwargs)
        result, _, _ = self.classify(values, [proposal(['A001'])], irrelevant=['A002'])
        self.assertEqual(result[0].event.article_ids, ['A001'])

    def test_categories_are_validated(self):
        for category in ('regulatory', 'macroeconomics', 'industry'):
            self.classify([article(1)], [proposal(['A001'], category=category)])
        with self.assertRaises(ValueError):
            self.classify([article(1)], [proposal(['A001'], category='unknown')])

    def test_full_source_passthrough_and_no_persistent_ids(self):
        value = article(1, text='Complete source ' + ('retained original content ' * 20000))
        llm = Reply([proposal(['A001'])])
        group = ArticleGroup(group_key='G1', topic='news', article_ids=['A001'])
        accepted, _, _ = classify_articles(group, {'A001': value}, {}, llm, chunk_size=1)
        self.assertEqual(llm.context['sources'][0]['text'], value.text)
        self.assertNotIn('event_id', accepted[0].event.model_dump())
        self.assertNotIn('candidates', llm.context)
        self.assertNotIn('existing_event_ids', llm.context['group'])

    def test_runtime_schema_requires_each_article_and_only_allowed_issue_ids(self):
        schema = classification_schema(['A001', 'A002'])
        contract = schema.model_json_schema()
        assignment_schema = contract['$defs']['ArticleAssignments']
        self.assertEqual(set(assignment_schema['required']), {'A001', 'A002'})
        self.assertFalse(assignment_schema['additionalProperties'])
        self.assertEqual(assignment_schema['properties']['A001']['anyOf'][0]['enum'], ['P1', 'P2'])
        self.assertNotIn('article_ids', contract['$defs']['AllowedIssueDefinition']['properties'])

    def raw_classify(self, payload, values=None):
        class Raw:
            def complete(self, *args, **kwargs): return payload
        values = values or [article(1), article(2)]
        group = ArticleGroup(group_key='G1', topic='news', article_ids=[a.article_id for a in values])
        return classify_articles(group, {a.article_id: a for a in values}, {}, Raw())

    def test_exact_missing_and_foreign_article_ids_are_recorded(self):
        with self.assertRaises(IDConstraintError) as raised:
            self.raw_classify(dict(complete=True, events=[], assignments={'A001':None, 'Aforeign':None}))
        details = raised.exception.details
        self.assertEqual(details['missing_ids'], ['A002'])
        self.assertEqual(details['foreign_ids'], ['Aforeign'])
        with self.assertRaises(IDConstraintError) as raised:
            self.raw_classify(dict(complete=True, events=[]))
        self.assertEqual(raised.exception.details['missing_ids'], ['A001', 'A002'])

    def test_unknown_and_undefined_issue_references_are_explicit(self):
        with self.assertRaises(IDConstraintError) as raised:
            self.raw_classify(dict(complete=True, events=[], assignments={'A001':'P99','A002':None}))
        self.assertEqual(raised.exception.details['foreign_ids'], ['P99'])
        self.assertEqual(raised.exception.details['invalid_references'], [{'article_id':'A001','issue_id':'P99'}])
        with self.assertRaises(IDConstraintError) as raised:
            self.raw_classify(dict(complete=True, events=[], assignments={'A001':'P1','A002':None}))
        self.assertEqual(raised.exception.details['missing_ids'], ['P1'])

    def test_duplicate_issue_definitions_still_fail_with_an_unused_draft(self):
        item = dict(issue_id='P1', title='Issue', summary='Issue summary.', category='industry')
        unused = {**item, 'issue_id':'P2', 'title':'Unused issue'}
        with self.assertRaises(IDConstraintError) as raised:
            self.raw_classify(dict(complete=True, events=[item, item, unused], assignments={'A001':'P1','A002':None}))
        self.assertEqual(raised.exception.details['duplicate_ids'], ['P1'])

    def test_unused_issue_drafts_are_removed_with_warning_and_membership_preserved(self):
        definitions = [dict(issue_id=f'P{i}', title=f'Issue {i}', summary=f'Issue {i} summary.', category='industry') for i in (1,2,3)]
        values = [article(i) for i in (1,2,3)]
        assignments = {value.article_id:'P1' for value in values}
        payload = dict(complete=True, events=definitions, assignments=assignments)
        proposals, details, failed = self.raw_classify(payload, values)
        self.assertFalse(failed)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].event.title, 'Issue 1')
        self.assertEqual(proposals[0].event.article_ids, ['A001','A002','A003'])
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0].stage, 'classify')
        self.assertEqual(details[0].code, 'unused_issue_drafts_removed')
        self.assertEqual(details[0].item_id, 'G1')
        self.assertEqual(json.loads(details[0].message), {
            'removed_issue_ids':['P2','P3'], 'retained_issue_ids':['P1'],
            'article_ids':['A001','A002','A003']})
        self.assertEqual(payload['assignments'], assignments)
        self.assertEqual([item['issue_id'] for item in payload['events']], ['P1','P2','P3'])

    def test_all_irrelevant_articles_drop_unused_definitions_without_failure(self):
        item = dict(issue_id='P1', title='Issue', summary='Issue summary.', category='industry')
        proposals, details, failed = self.raw_classify(dict(complete=True, events=[item], assignments={'A001':None,'A002':None}))
        self.assertEqual(proposals, [])
        self.assertFalse(failed)
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0].code, 'unused_issue_drafts_removed')
        self.assertEqual(json.loads(details[0].message), {
            'removed_issue_ids':['P1'], 'retained_issue_ids':[], 'article_ids':['A001','A002']})

    def test_unused_drafts_do_not_hide_undefined_references(self):
        definitions = [dict(issue_id=f'P{i}', title=f'Issue {i}', summary=f'Issue {i} summary.', category='industry') for i in (1,2)]
        with self.assertRaises(IDConstraintError) as raised:
            self.raw_classify(dict(complete=True, events=definitions, assignments={'A001':'P1','A002':'P3','A003':None}), [article(i) for i in (1,2,3)])
        self.assertEqual(raised.exception.details['missing_ids'], ['P3'])
        self.assertEqual(raised.exception.details['invalid_references'], [{'article_id':'A002','issue_id':'P3'}])

    def test_grouping_is_time_bounded_deterministic_and_exact_once(self):
        values = [article(i, day=i) for i in range(10)]
        left = group_articles(values, [])
        right = group_articles(list(reversed(values)), [])
        self.assertEqual(left, right)
        covered = [key for group in left.groups for key in group.article_ids]
        self.assertEqual(len(covered), len(set(covered)))
        self.assertEqual(set(covered), {a.article_id for a in values})
        self.assertTrue(all(len(group.article_ids) <= 4 for group in left.groups))
        isolated = group_articles([article(20), article(21, day=8)], [])
        self.assertEqual(len(isolated.groups), 2)

    def test_date_proximity_alone_cannot_group_unrelated_text(self):
        values = [article(1, text='aluminium copper nickel'), article(2, text='banknote mortgage deficit')]
        values[0].title = 'Metal'
        values[1].title = 'Currency'
        self.assertEqual(len(group_articles(values, []).groups), 2)

    def test_missing_publication_uses_discovery_only_for_grouping(self):
        values = [article(1, published=False), article(2, published=False, day=8)]
        self.assertEqual(len(group_articles(values, []).groups), 2)
        self.assertIsNone(values[0].published_at)

    def test_cross_window_cross_group_consolidation(self):
        values = [article(1), article(2, day=8)]
        groups = group_articles(values, []).groups
        self.assertEqual(len(groups), 2)
        receipts = []
        for value, group in zip(values, groups):
            accepted, _, _ = classify_articles(group, {value.article_id: value}, {}, Reply([proposal([value.article_id])]))
            receipts.extend(accepted)
        result, errors = consolidate_drafts(receipts, SameIssue())
        self.assertFalse(errors)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0][1].article_ids), {'A001', 'A002'})
        self.assertEqual(len(result[0][0]), 2)

    def test_binary_matching_validates_ids_and_no_candidates_needs_no_llm(self):
        item = EventProposal(**proposal(['A001']))
        self.assertEqual(associate_event(item, [], None).decision, 'new')
        class Invalid:
            calls = 0
            def complete(self, *args, **kwargs):
                self.calls += 1
                return dict(decision='same', event_id='Eforeign', reason='Bad supplied ID')
        llm = Invalid()
        candidate = SimpleNamespace(event_id='E001', model_dump=lambda **kwargs: dict(event_id='E001', **proposal(['A002'])))
        with self.assertRaises(ValueError):
            associate_event(item, [candidate], llm)
        self.assertEqual(llm.calls, 2)


if __name__ == '__main__':
    unittest.main()
