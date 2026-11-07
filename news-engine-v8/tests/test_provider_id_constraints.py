"""Actual provider schemas and diagnostic values are checked without paid calls."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
import httpx

from news_engine.clients import LLMClient
from news_engine.group_first import classification_schema, EventProposal, DraftCandidate
from news_engine.events import _id_schema, RetrievalDraft, AssociationDraft, associate_event
from news_engine.id_constraints import IDConstraintError


def envelope(content):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 7, "cost": .002}}


def response(assignments):
    return {"complete": True, "events": [{"issue_id": "P1", "title": "Funding facility",
            "summary": "A facility was announced.", "category": "regulatory"}],
            "assignments": assignments}


class ProviderIDConstraintTests(unittest.TestCase):
    def run_response(self, schema, content, callback):
        requests = []
        def reply(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=envelope(content))
        with httpx.Client(transport=httpx.MockTransport(reply)) as http:
            client = LLMClient("offline-key", "offline-model", "https://example.com/v1", http_client=http)
            callback(client, requests)
        self.assertEqual(len(requests), 1)

    def test_classification_payload_requires_one_field_per_article_and_local_issue_enum(self):
        schema = classification_schema(["A001", "A002"])
        def check(client, requests):
            parsed = client.complete(schema, "Classify", {}, stage="classify")
            self.assertEqual(parsed.assignments.model_dump(), {"A001": "P1", "A002": None})
            sent = requests[0]["response_format"]["json_schema"]["schema"]
            assignments = sent["$defs"]["ArticleAssignments"]
            self.assertEqual(assignments["required"], ["A001", "A002"])
            self.assertFalse(assignments["additionalProperties"])
            self.assertEqual(assignments["properties"]["A001"]["anyOf"][0]["enum"], ["P1", "P2"])
            self.assertEqual(client.receipts[0]["status"], "succeeded")
        self.run_response(schema, json.dumps(response({"A001": "P1", "A002": None})), check)

    def test_missing_foreign_articles_and_invalid_issue_ids_survive_provider_validation(self):
        schema = classification_schema(["A001", "A002"])
        def check(client, requests):
            with self.assertRaises(IDConstraintError) as caught:
                client.complete(schema, "Classify", {}, stage="classify")
            details = caught.exception.details
            self.assertEqual(details["missing_ids"], ["A002"])
            self.assertEqual(details["foreign_ids"], ["A999", "P99"])
            self.assertEqual(details["allowed_article_ids"], ["A001", "A002"])
            self.assertEqual(details["allowed_issue_ids"], ["P1", "P2"])
            self.assertEqual(client.receipts[0]["validation_error"], details)
            self.assertEqual(client.receipts[0]["status"], "failed")
            self.assertEqual(client.receipts[0]["cost_usd"], .002)
        self.run_response(schema, json.dumps(response({"A001": "P99", "A999": "P1"})), check)

    def test_duplicate_json_article_keys_cannot_silently_use_the_last_value(self):
        schema = classification_schema(["A001", "A002"])
        raw = json.dumps(response({"A001": "P1", "A002": None})).replace(
            '"A001": "P1"', '"A001": "P1", "A001": "P2"')
        def check(client, requests):
            with self.assertRaises(IDConstraintError) as caught:
                client.complete(schema, "Classify", {}, stage="classify")
            self.assertEqual(caught.exception.details["duplicate_ids"], ["A001"])
            self.assertEqual(caught.exception.details["code"], "duplicate_json_keys")
            self.assertEqual(client.receipts[0]["validation_error"], caught.exception.details)
        self.run_response(schema, raw, check)

    def test_missing_assignment_table_identifies_every_unassigned_article(self):
        schema = classification_schema(["A001", "A002"])
        body = response({})
        del body["assignments"]
        def check(client, requests):
            with self.assertRaises(IDConstraintError) as caught:
                client.complete(schema, "Classify", {}, stage="classify")
            self.assertEqual(caught.exception.details["missing_ids"], ["A001", "A002"])
            self.assertEqual(client.receipts[0]["validation_error"], caught.exception.details)
        self.run_response(schema, json.dumps(body), check)

    def test_candidate_event_enums_reach_the_provider_and_foreign_id_is_recorded(self):
        for base, field, value in [(RetrievalDraft, "event_ids", ["E999"]),
                                    (AssociationDraft, "event_id", "E999")]:
            schema = _id_schema(base, ["E001", "E002"])
            body = {field: value}
            if base is AssociationDraft:
                body.update(decision="same", reason="Same issue.")
            def check(client, requests):
                with self.assertRaises(IDConstraintError) as caught:
                    client.complete(schema, "Match", {}, stage="group_identity")
                self.assertEqual(caught.exception.details["foreign_ids"], ["E999"])
                self.assertEqual(caught.exception.details["allowed_event_ids"], ["E001", "E002"])
                sent = requests[0]["response_format"]["json_schema"]["schema"]
                target = sent["properties"][field]
                allowed = target["items"] if base is RetrievalDraft else target["anyOf"][0]
                self.assertEqual(allowed["enum"], ["E001", "E002"])
            self.run_response(schema, json.dumps(body), check)

    def test_wrong_id_field_containing_source_text_is_not_logged_as_an_id(self):
        schema = classification_schema(["A001"])
        source = "Private original source paragraph containing names and confidential context."
        def check(client, requests):
            with self.assertRaises(IDConstraintError) as caught:
                client.complete(schema, "Classify", {"source": source}, stage="classify")
            self.assertNotIn(source, str(caught.exception))
            self.assertNotIn(source, json.dumps(client.receipts))
        self.run_response(schema, json.dumps(response({"A001": source})), check)

    def test_semantic_diagnostic_hook_is_thread_safe_copied_and_not_a_provider_call(self):
        requests = []
        def reply(request):
            requests.append(request)
            raise AssertionError("The diagnostic hook must not call the provider")
        with httpx.Client(transport=httpx.MockTransport(reply)) as http:
            client = LLMClient("offline", "model", "https://example.com/v1", http_client=http)
            details = {"code": "invalid_candidate_ids", "duplicate_ids": ["E001"]}
            client.record_validation_error(details, "group_retrieve")
            details["duplicate_ids"].append("E002")
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda n: client.record_validation_error(
                    {"code": "invalid_candidate_ids", "duplicate_ids": [f"E{n + 3:03}"]},
                    "group_retrieve"), range(20)))
            snapshot = client.validation_errors
            self.assertEqual(len(snapshot), 21)
            self.assertEqual(snapshot[0]["duplicate_ids"], ["E001"])
            self.assertEqual(snapshot[0]["stage"], "group_retrieve")
            snapshot[0]["duplicate_ids"].append("E999")
            self.assertEqual(client.validation_errors[0]["duplicate_ids"], ["E001"])
            self.assertEqual(client.receipts, [])
            self.assertEqual(requests, [])

    def test_real_client_keeps_a_repaired_duplicate_candidate_error_and_actual_billing(self):
        replies = [{"event_ids": ["E001", "E001"]}, {"event_ids": ["E001"]},
                   {"decision": "same", "event_id": "E001", "reason": "Same facility."}]
        requests = []
        def reply(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=envelope(json.dumps(replies[len(requests) - 1])))
        proposal = EventProposal(title="Facility", summary="Funding facility.",
                                  category="regulatory", article_ids=["A100"])
        candidates = [DraftCandidate(event_id=f"E{n:03}", title="Facility", summary="Funding facility.",
                         category="regulatory", article_ids=[f"A{n:03}"]) for n in range(1, 7)]
        with httpx.Client(transport=httpx.MockTransport(reply)) as http:
            client = LLMClient("offline", "model", "https://example.com/v1", http_client=http)
            decision = associate_event(proposal, candidates, client)
            self.assertEqual(decision.event_id, "E001")
            self.assertEqual(len(client.validation_errors), 1)
            self.assertEqual(client.validation_errors[0]["duplicate_ids"], ["E001"])
            self.assertEqual(len(client.receipts), 3)
            self.assertAlmostEqual(sum(r["cost_usd"] for r in client.receipts), .006)
            self.assertFalse(any(r.get("validation_error") for r in client.receipts))
            self.assertEqual(len(requests), 3)


if __name__ == "__main__":
    unittest.main()
