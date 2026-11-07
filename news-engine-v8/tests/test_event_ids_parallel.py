"""Offline ID allowlists and deterministic parallel consolidation checks."""
from threading import Barrier, Lock, get_ident, main_thread
import unittest
from news_engine.events import associate_event, consolidate_drafts
from news_engine.group_first import EventProposal, StoredProposal, DraftCandidate
from news_engine.id_constraints import IDConstraintError

def proposal(article, issue='Facility rule'):
    return EventProposal(title=issue, summary='Reports concerning ' + issue, category='regulatory', article_ids=[article])

def candidate(index):
    return DraftCandidate(event_id='E' + f'{index:024d}', **proposal('A' + str(index)).model_dump())

class Reply:

    def __init__(self, value):
        self.value, self.contexts, self.schemas = (value, [], [])

    def complete(self, schema, prompt, context, *, stage):
        self.contexts.append(context)
        self.schemas.append(schema.model_json_schema())
        return self.value

class IssueLabels:

    def __init__(self, barrier=False):
        self.barrier = Barrier(4) if barrier else None
        self.lock, self.active, self.peak = (Lock(), 0, 0)

    def complete(self, schema, prompt, context, *, stage):
        parallel = self.barrier is not None and get_ident() != main_thread().ident and (int(context['event_proposal']['article_ids'][0][1:]) >= 4)
        if parallel:
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            self.barrier.wait(timeout=5)
        target = next((c for c in context['candidates'] if c['title'] == context['event_proposal']['title']), None)
        if parallel:
            with self.lock:
                self.active -= 1
        return schema.model_validate({'decision': 'same' if target else 'new', 'event_id': target['event_id'] if target else None, 'reason': 'Fixed offline labels'})

def receipts():
    return [StoredProposal(proposal_key=f'{i:024d}', event=proposal(f'A{i}', 'First' if i < 4 else 'Second')) for i in range(8)]

class EventIdParallelTests(unittest.TestCase):

    def test_candidate_enum_rejects_foreign_id_and_preserves_diagnostics(self):
        llm = Reply({'event_ids': ['Aforeign']})
        candidates = [candidate(i) for i in range(6)]
        with self.assertRaises(IDConstraintError) as error:
            associate_event(proposal('Aone'), candidates, llm)
        self.assertEqual(error.exception.details['foreign_ids'], ['Aforeign'])
        self.assertEqual(error.exception.details['allowed_event_ids'], sorted((c.event_id for c in candidates)))
        self.assertEqual(len(llm.contexts), 2)
        self.assertEqual(llm.contexts[1]['repair_details']['foreign_ids'], ['Aforeign'])
        self.assertEqual(llm.schemas[0]['properties']['event_ids']['items']['enum'], [c.event_id for c in candidates])

    def test_duplicate_retrieval_ids_are_not_silently_deduplicated(self):
        candidates = [candidate(i) for i in range(6)]
        repeated = candidates[2].event_id
        llm = Reply({'event_ids': [repeated, repeated]})
        with self.assertRaises(IDConstraintError) as error:
            associate_event(proposal('Aone'), candidates, llm)
        self.assertEqual(error.exception.details['duplicate_ids'], [repeated])
        self.assertFalse(error.exception.details['foreign_ids'])
        self.assertEqual(len(llm.contexts), 2)

    def test_binary_match_uses_candidate_enum_including_only_allowed_null(self):
        llm = Reply({'decision': 'same', 'event_id': 'Aforeign', 'reason': 'Bad identity'})
        with self.assertRaises(IDConstraintError) as error:
            associate_event(proposal('Aone'), [candidate(1)], llm)
        self.assertEqual(error.exception.details['foreign_ids'], ['Aforeign'])
        shape = llm.schemas[0]['properties']['event_id']['anyOf']
        self.assertEqual(shape[0]['const'], candidate(1).event_id)
        self.assertEqual(shape[1]['type'], 'null')

    def test_same_requires_supplied_event_id(self):
        reply = {'decision': 'same', 'event_id': None, 'reason': 'Missing selection'}
        code = 'invalid_same_event_id'
        with self.assertRaises(IDConstraintError) as error:
            associate_event(proposal('Aone'), [candidate(1)], Reply(reply))
        self.assertEqual(error.exception.details['code'], code)

    def test_new_requires_null_event_id(self):
        reply = {'decision': 'new', 'event_id': 'E000000000000000000000001', 'reason': 'Contradiction'}
        code = 'new_event_requires_null'
        with self.assertRaises(IDConstraintError) as error:
            associate_event(proposal('Aone'), [candidate(1)], Reply(reply))
        self.assertEqual(error.exception.details['code'], code)

    def test_provider_failure_never_uses_validation_repair(self):

        class Unavailable(Reply):

            def complete(self, *args, **kwargs):
                self.contexts.append({})
                raise RuntimeError('provider unavailable')
        llm = Unavailable(None)
        with self.assertRaises(RuntimeError):
            associate_event(proposal('Aone'), [candidate(1)], llm)
        self.assertEqual(len(llm.contexts), 1)

    def test_parallel_new_siblings_are_reconciled_and_progress_is_main_thread_only(self):
        llm, progress = (IssueLabels(barrier=True), [])
        result, errors = consolidate_drafts(receipts(), llm, workers=4, progress=lambda n, total: progress.append((get_ident(), n, total)))
        self.assertTrue(not errors and llm.peak == 4)
        self.assertEqual(len(result), 2)
        self.assertEqual(sorted((len(p.article_ids) for keys, p in result)), [4, 4])
        self.assertEqual({a for keys, p in result for a in p.article_ids}, {f'A{i}' for i in range(8)})
        self.assertEqual([p[1] for p in progress], list(range(9)))
        self.assertTrue(all((p[0] == main_thread().ident and p[2] == 8 for p in progress)))
        sequential, errors = consolidate_drafts(list(reversed(receipts())), IssueLabels(), workers=1)
        self.assertTrue(not errors and result == sequential)

    def test_failed_proposal_retains_full_diagnostics_and_is_not_committed(self):

        class Fails(IssueLabels):

            def complete(self, schema, prompt, context, *, stage):
                if context['event_proposal']['article_ids'] == ['A4']:
                    raise IDConstraintError('invalid_candidate_ids', foreign_ids=['Ebad'], allowed_ids=['E' + 'x' * 1000])
                return super().complete(schema, prompt, context, stage=stage)
        result, errors = consolidate_drafts(receipts(), Fails(), workers=4)
        self.assertTrue(len(errors) == 1 and errors[0].item_id == receipts()[4].proposal_key)
        self.assertTrue('"foreign_ids": ["Ebad"]' in errors[0].message and len(errors[0].message) > 1000)
        self.assertNotIn('A4', {a for keys, p in result for a in p.article_ids})
        self.assertEqual(sum((len(keys) for keys, p in result)), 7)

    def test_identity_enum_is_narrowed_to_retrieval_selection(self):
        candidates = [candidate(i) for i in range(6)]

        class Narrowed:
            schemas = []

            def complete(self, schema, prompt, context, *, stage):
                self.schemas.append(schema.model_json_schema())
                if 'event_ids' in schema.model_fields:
                    return {'event_ids': [candidates[0].event_id]}
                return {'decision': 'same', 'event_id': candidates[1].event_id, 'reason': 'Not retrieved'}
        with self.assertRaises(IDConstraintError) as error:
            associate_event(proposal('Aone'), candidates, Narrowed())
        self.assertEqual(error.exception.details['foreign_ids'], [candidates[1].event_id])
        self.assertEqual(error.exception.details['allowed_event_ids'], [candidates[0].event_id])

    def test_duplicate_input_candidates_are_rejected_before_any_provider_call(self):
        llm = Reply(None)
        with self.assertRaises(IDConstraintError) as error:
            associate_event(proposal('Aone'), [candidate(1), candidate(1)], llm)
        self.assertEqual(error.exception.details['duplicate_ids'], [candidate(1).event_id])
        self.assertFalse(llm.contexts)

    def test_repaired_duplicate_ids_remain_in_semantic_validation_audit(self):
        candidates = [candidate(i) for i in range(6)]
        selected = candidates[0].event_id

        class Repaired:

            def __init__(self):
                self.calls = 0
                self.validation_errors = []

            def record_validation_error(self, details, stage):
                self.validation_errors.append({'stage': stage, **details})

            def complete(self, schema, prompt, context, *, stage):
                self.calls += 1
                if self.calls == 1:
                    return {'event_ids': [selected, selected]}
                if 'event_ids' in schema.model_fields:
                    return {'event_ids': [selected]}
                return {'decision': 'same', 'event_id': selected, 'reason': 'Correct selection after repair'}
        llm = Repaired()
        result = associate_event(proposal('Aone'), candidates, llm)
        self.assertTrue(result.decision == 'same' and result.event_id == selected)
        self.assertEqual(llm.calls, 3)
        self.assertEqual(len(llm.validation_errors), 1)
        self.assertEqual(llm.validation_errors[0]['stage'], 'group_retrieve')
        self.assertEqual(llm.validation_errors[0]['duplicate_ids'], [selected])
        self.assertFalse(llm.validation_errors[0]['foreign_ids'])

    def test_schema_id_error_is_not_double_counted_by_semantic_audit_hook(self):

        class Invalid(Reply):

            def record_validation_error(self, details, stage):
                raise AssertionError('Schema rejection already recorded by provider client')
        with self.assertRaises(IDConstraintError):
            associate_event(proposal('Aone'), [candidate(1)], Invalid({'decision': 'same', 'event_id': 'Aforeign', 'reason': 'Wrong domain'}))
if __name__ == '__main__':
    unittest.main()
