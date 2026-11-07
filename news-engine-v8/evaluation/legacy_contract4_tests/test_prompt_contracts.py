"""Prompts expose schema constraints and runtime ID allowlists without paid calls."""
import json
import unittest
import httpx
from news_engine.clients import LLMClient, _strict_schema
from news_engine.group_first import EventExtractionDraft, group_articles, extract_group
from news_engine.analyze import analyze_event
from news_engine.models import Event
from test_mvp import article, RecordedLLM, NOW


class PromptContractTests(unittest.TestCase):
    def test_prompt_schema_matches_transport_schema(self):
        requests=[]
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop',
                'message':{'content':'{"complete":true,"events":[]}'}}]})
        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            client=LLMClient('test-key','test-model','https://example.org/v1',http_client=transport)
            client.complete(EventExtractionDraft,'Extraction instructions',{'text':'Source'},stage='extract')
        payload=requests[0]
        prompt=payload['messages'][0]['content']
        schema=json.loads(prompt.split('Runtime reference IDs must come from the supplied allowed values/candidates.\n',1)[1])
        self.assertEqual(schema,payload['response_format']['json_schema']['schema'])
        self.assertEqual(schema,_strict_schema(EventExtractionDraft.model_json_schema()))
        self.assertEqual(schema['$defs']['ProposedEvent']['properties']['category']['enum'],
                         ['regulatory','macroeconomics','industry'])
        self.assertIn({'type':'null'},schema['$defs']['EventIdentity']['properties']['location']['anyOf'])
        self.assertFalse(schema['additionalProperties'])

    def test_runtime_source_and_canonical_change_allowlists(self):
        source=article(); peer=article(key='A2',text='The central bank cut its policy rate again.')
        group=group_articles([source,peer],[]).groups[0]
        llm=RecordedLLM()
        accepted,_=extract_group(group,{a.article_id:a for a in [source,peer]}, {}, llm,16000,[source.article_id])
        context=llm.calls[0][1]
        self.assertEqual(context['allowed_values'],{'source_id':[source.article_id],'existing_event_id':[]})
        change=accepted[source.article_id][0]
        duplicate=change.model_copy(update={'change_id':'C-duplicate','duplicate_of':change.change_id})
        event=Event(category='macroeconomics',event_id='E1',title='Rate decision',identity=change.identity,
            what_changed=[change,duplicate],latest_change_id=change.change_id,
            created_at=NOW,last_seen_at=NOW,last_material_at=NOW)
        analyze_event(event,llm,NOW.isoformat())
        context=next(ctx for stage,ctx in llm.calls if stage=='analyze')
        self.assertEqual(context['allowed_values']['primary_change_id'],[change.change_id])
        self.assertNotIn(event.event_id,context['allowed_values']['primary_change_id'])
