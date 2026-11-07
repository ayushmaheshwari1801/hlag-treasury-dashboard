"""Provider and CLI boundaries are exercised entirely offline."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
from news_engine.clients import LLMClient, ExaClient
from news_engine.group_first import ArticleClassificationDraft
from news_engine.events import AssociationDraft, RetrievalDraft
from news_engine.analyze import AnalysisDraft, BatchAnalysisDraft
from news_engine.search import QueryPlan


def analysis():
    return dict(summary='Liquidity facility reported.', why_it_matters=['Monitor financing access if relevant.'],
        what_to_do=['Confirm eligibility before planning funding.'], impact=3, relevance=4, urgency=3,
        reasoning=dict(impact='Financing access may affect liquidity.', relevance='Potential funding channel.',
                       urgency='Monitor the operational facility.'), impact_basis='financial_action',
        relevance_basis='direct_channel', urgency_basis='operative_control_or_8_30_days',
        has_treasury_action_or_constraint=True)


def envelope(value, **kwargs):
    return dict(choices=[dict(finish_reason=kwargs.get('finish_reason', 'stop'),
        message=kwargs.get('message', dict(content=json.dumps(value))))],
        usage=dict(prompt_tokens=13, completion_tokens=7, cost=.002))


class ProviderTests(unittest.TestCase):
    def test_every_active_schema_omits_output_caps_and_records_usage(self):
        cases = [(QueryPlan, 'search_plan', dict(queries=[dict(query='funding', topic='regulatory', geography='Global')])),
            (ArticleClassificationDraft, 'classify', dict(complete=True, events=[], assignments={'A001':None})),
            (AssociationDraft, 'group_identity', dict(decision='new', event_id=None, reason='Distinct issue')),
            (RetrievalDraft, 'group_retrieve', dict(event_ids=[])),
            (AnalysisDraft, 'analyze', analysis()),
            (BatchAnalysisDraft, 'analyze_batch', dict(results=[dict(event_id='E001', analysis=analysis())]))]
        captured = []
        values = {schema.__name__: value for schema, _, value in cases}
        def respond(request):
            payload = json.loads(request.content)
            captured.append(payload)
            value = values[payload['response_format']['json_schema']['name']]
            return httpx.Response(200, json=envelope(value))
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = LLMClient('offline-key', 'offline-model', 'https://example.com/v1', http_client=http)
            for schema, stage, _ in cases:
                client.complete(schema, 'Task', {'full_text': 'original ' * 10000}, stage=stage)
            self.assertEqual(len(captured), len(cases))
            for payload in captured:
                self.assertFalse({'max_tokens', 'max_completion_tokens', 'max_output_tokens'} & payload.keys())
                self.assertEqual(json.loads(payload['messages'][1]['content'])['full_text'], 'original ' * 10000)
                self.assertTrue(payload['response_format']['json_schema']['strict'])
            self.assertEqual(sum(r['prompt_tokens'] for r in client.receipts), 13 * len(cases))
            self.assertTrue(all(r['status'] == 'succeeded' and r['cost_usd'] == .002 for r in client.receipts))

    def test_completion_refusal_tool_calls_and_malformed_json_fail_explicitly(self):
        malformed = [envelope({}, finish_reason='length'),
            envelope({}, message=dict(content='{}', refusal='refused')),
            envelope({}, message=dict(content='{}', tool_calls=[{}])),
            envelope({}, message=dict(content='not JSON')),
            envelope(dict(decision='uncertain', event_id=None, reason='invalid enum'))]
        for body in malformed:
            with self.subTest(body=body), httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as http:
                client = LLMClient('offline', 'model', 'https://example.com/v1', http_client=http)
                with self.assertRaises(ValueError):
                    client.complete(AssociationDraft, 'Task', {}, stage='group_identity')
                self.assertEqual(len(client.receipts), 1)
                self.assertEqual(client.receipts[0]['status'], 'failed')

    def test_authentication_failure_stops_further_requests_without_retry(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(402, json=dict(error=dict(code='insufficient_quota')))
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = LLMClient('offline', 'model', 'https://example.com/v1', http_client=http)
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    client.complete(AssociationDraft, 'Task', {}, stage='group_identity')
            self.assertEqual(len(seen), 1)
            self.assertEqual(client.receipts[0]['http_status'], 402)

    def test_transport_failure_is_explicit_and_not_retried(self):
        seen = []
        def respond(request):
            seen.append(request)
            raise httpx.ConnectError('offline transport failure', request=request)
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = LLMClient('offline', 'model', 'https://example.com/v1', http_client=http)
            with self.assertRaisesRegex(RuntimeError, 'transport'):
                client.complete(AssociationDraft, 'Task', {}, stage='group_identity')
            self.assertEqual(len(seen), 1)
            self.assertEqual(client.receipts[0]['status'], 'failed')

    def test_exa_preserves_full_text_date_filter_and_cost_receipt(self):
        from datetime import datetime, timezone, timedelta
        seen = []
        text = 'Original full source ' * 10000
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=dict(results=[dict(url='https://example.com/source', text=text)], costDollars=dict(total=.01)))
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = ExaClient('offline', http_client=http)
            end = datetime(2026, 1, 1, tzinfo=timezone.utc)
            result = client.search('funding', start=end-timedelta(days=2), end=end)
            self.assertEqual(result['results'][0]['text'], text)
            self.assertIn('startPublishedDate', seen[0])
            self.assertIn('endPublishedDate', seen[0])
            self.assertEqual(client.receipts[0]['cost_usd'], .01)

    def test_cli_input_runs_to_contract8_json_without_network(self):
        from news_engine.pipeline import main
        class Offline:
            model = 'offline'
            receipts = []
            closed = False
            def complete(self, schema, prompt, context, stage):
                if stage == 'classify':
                    return dict(complete=True, assignments={a['article_id']:'P1' for a in context['sources']},
                        events=[dict(issue_id='P1',title='Liquidity facility',
                        summary='Liquidity facility reported.', category='regulatory')])
                if stage == 'analyze_batch':
                    return dict(results=[dict(event_id=e['current_event']['event_id'], analysis=analysis()) for e in context['events']])
                raise AssertionError(stage)
            def close(self):
                self.closed = True
        client = Offline()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'input.json'; output = root/'result.json'
            source.write_text(json.dumps([dict(url='https://example.com/source', title='Funding facility',
                                               text='Liquidity facility reported.')]), encoding='utf-8')
            argv = ['news-engine', '--input', str(source), '--database', str(root/'v8.sqlite'), '--output', str(output)]
            credentials = SimpleNamespace(llm_api_key='offline', llm_model='offline', llm_base_url='https://example.com/v1')
            with patch('sys.argv', argv), patch('news_engine.config.load_credentials', return_value=credentials), patch('news_engine.clients.LLMClient', return_value=client):
                self.assertEqual(main(), 0)
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['schema_version'], 8)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(len(result['events']), 1)
            self.assertEqual(result['events'][0]['score']['total'], 67)
            self.assertTrue(client.closed)


if __name__ == '__main__':
    unittest.main()
