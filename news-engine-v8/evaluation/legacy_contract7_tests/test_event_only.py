"""Current Event-only runtime contracts. All providers are offline fakes."""
import json,sqlite3,tempfile,unittest,threading
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from news_engine.models import ArticleInput,Event,EventEntry,RunResult,Score,validate_r5,IMPACT_CAPS,RELEVANCE_CAPS,URGENCY_CAPS
from news_engine.analyze import AnalysisDraft,ANALYSIS_PROMPT
from news_engine.group_first import EntryDraft,PROMPT
from news_engine.storage import Store,BusyError
from news_engine.pipeline import run_pipeline
from news_engine.group_first import EventConstructionDraft,group_articles
from news_engine.clients import LLMClient
from news_engine.api import create_app
from news_engine.search import QueryPlan,search

NOW=datetime(2026,11,1,tzinfo=timezone.utc)
IDENTITY=dict(subject='Authority',action='decision',occurrence='Rule issue',scope='Eligibility')
def raw(text='The authority adopted a rule.',url='https://example.org/a',title='Rule'):
 return dict(url=url,title=title,text=text,published_at='2026-10-30T00:00:00Z')
def source(ctx):return ctx['sources'][0]
def construction(ctx):
 return dict(complete=True,events=[dict(title=a['title'],identity=IDENTITY,category='regulatory',source_ids=[a['article_id']],
    what_changed=[dict(text=a['text'],claim_type='actual',source_id=a['article_id'],evidence_quote=a['text'],numbers=[])]) for a in ctx['sources']])
def analysis(ctx):
 ref=ctx['current_event']['evidence'][0]
 entries=ctx['current_event']['what_changed']
 index=next(i for i,entry in enumerate(entries) if any(
  candidate['source_id']==ref['source_id']
  for candidate in [dict(source_id=entry['source_id'],evidence=entry['evidence']),*entry.get('supporting_evidence',[])]))
 return dict(why_it_matters=['Company applicability is not established.'],what_to_do=['If relevant, verify applicability.'],impact=1,relevance=1,urgency=1,
 reasoning=dict(impact='Restricted issue.',relevance='Speculative applicability.',urgency='Background monitoring.'),primary_source_id=ref['source_id'],
 primary_evidence_quote=ref['evidence']['quote'],primary_entry_index=index,impact_basis='commentary_or_forecast',relevance_basis='speculative',urgency_basis='long_horizon',has_treasury_action_or_constraint=False)
class Fake:
 model='recorded-event-only'
 def __init__(self,handler=None):self.handler=handler;self.calls=[];self.receipts=[]
 def complete(self,schema,prompt,ctx,*,stage):
  self.calls.append((stage,ctx));self.receipts.append(dict(stage=stage,prompt_tokens=10,completion_tokens=5,cost_usd=0.001))
  value=self.handler(stage,ctx) if self.handler else None
  if value is None:
   if stage=='construct':value=construction(ctx)
   elif stage=='analyze_batch':
    items=[]
    for context in ctx['events']:
     single={**context,'reference_time':ctx['reference_time'],'treasury_context':ctx['treasury_context']}
     item=self.handler('analyze',single) if self.handler else None
     items.append(dict(event_id=context['current_event']['event_id'],analysis=item if item is not None else analysis(single)))
    value=dict(results=items)
   elif stage=='analyze':value=analysis(ctx)
   elif stage=='group_retrieve':value=dict(event_ids=[])
   else:value=dict(decision='new',event_id=None,relation='material',reason='Distinct issue.')
  return schema.model_validate(value)

class EventOnlyTests(unittest.TestCase):
 def setUp(self):
  self.folder=tempfile.TemporaryDirectory();self.path=Path(self.folder.name)/'fresh.sqlite';self.store=Store(self.path)
 def tearDown(self):self.store.close();self.folder.cleanup()
 def run_news(self,llm=None,articles=None,**kw):return run_pipeline(self.store,llm or Fake(),articles if articles is not None else [raw()],reference_time=NOW,**kw)
 def test_no_standalone_change_layer(self):
  import news_engine.models as models
  self.assertFalse(hasattr(models,'MajorChange'));self.assertFalse(hasattr(models,'ChangeDraft'));self.assertFalse(hasattr(models,'Numeric'))
  result=self.run_news();self.assertEqual(result.schema_version,7)
  fields=result.events[0].model_dump()
  self.assertIn('summary',fields);self.assertIn('evidence',fields)
  for key in ['changes','latest_change_id','change_id','status']:self.assertNotIn(key,fields)
  tables={row[0] for row in self.store.db.execute("select name from sqlite_master where type='table'")}
  self.assertEqual(tables,{'articles','events','metadata','runs','search_history'})
 def test_source_evidence_and_natural_numbers(self):
  result=self.run_news(articles=[raw('The rule sets a zero percent risk weight and a 30-day comment window.')])
  event=result.events[0];ref=event.evidence[0];source=result.sources[0]
  self.assertEqual(source.text[ref.evidence.start:ref.evidence.end],ref.evidence.quote)
  self.assertIn('30-day',event.summary);self.assertNotIn('numeric',event.model_dump_json())
 def test_cached_duplicate_restart_no_rescore(self):
  llm=Fake();one=self.run_news(llm);calls=len(llm.calls);key=one.events[0].event_id
  self.store.close();self.store=Store(self.path)
  two=self.run_news(llm)
  self.assertEqual(two.events[0].event_id,key);self.assertEqual(len(llm.calls),calls)
 def test_multi_event_single_article(self):
  def handle(stage,ctx):
   if stage=='construct':
    events=construction(ctx)['events'];events.append({**events[0],'title':'Freight','what_changed':[dict(text='Freight operating conditions changed.',claim_type='actual',source_id=source(ctx)['article_id'],evidence_quote=source(ctx)['text'],numbers=[])],'category':'industry','identity':{**IDENTITY,'occurrence':'Freight'}})
    return dict(complete=True,events=events)
  result=self.run_news(Fake(handle),[raw('A rule was adopted. Freight operating conditions changed.')])
  self.assertEqual(len(result.events),2);self.assertEqual({e.category for e in result.events},{'regulatory','industry'})
 def test_existing_event_material_update(self):
  first=self.run_news();key=first.events[0].event_id
  def handle(stage,ctx):
   if stage=='group_identity':return dict(decision='same',event_id=key,relation='material',reason='Same rule, new restriction.')
  two=self.run_news(Fake(handle),[raw('A new restriction applies.',url='https://example.org/b')])
  self.assertEqual(two.events[0].event_id,key);self.assertEqual(len(two.events[0].evidence),2)
  self.assertEqual(two.events[0].category,'regulatory')
 def test_corroboration_adds_citation_without_rescore(self):
  key=self.run_news().events[0].event_id
  def handle(stage,ctx):
   if stage=='construct':
    value=construction(ctx);value['events'][0]['what_changed'][0]['text']='The authority adopted a rule.';return value
   if stage=='group_identity':return dict(decision='same',event_id=key,relation='corroboration',reason='Same information, independent citation.')
  llm=Fake(handle);result=self.run_news(llm,[raw('The adopted rule was reported again.',url='https://example.org/b')])
  self.assertEqual(len(result.events[0].evidence),2);self.assertFalse(any(s in ('analyze','analyze_batch') for s,c in llm.calls))
 def test_technical_failure_pending_retry_after_restart(self):
  self.run_news()
  def fail(stage,ctx):
   if stage=='group_identity':raise RuntimeError('Injected provider failure')
  two=self.run_news(Fake(fail),[raw('Another issue.',url='https://example.org/b')])
  self.assertEqual(two.status,'partial');self.assertEqual(len(self.store.pending_proposals()),1)
  self.store.close();self.store=Store(self.path)
  llm=Fake();three=self.run_news(llm,[])
  self.assertEqual(three.status,'completed');self.assertEqual(len(self.store.pending_proposals()),0)
  self.assertFalse(any(s=='construct' for s,c in llm.calls))
 def test_association_atomic_on_write_failure(self):
  with patch.object(self.store,'_save_event',side_effect=ValueError('Injected write failure')):
   result=self.run_news()
  self.assertEqual(result.status,'partial');self.assertEqual(self.store.events(),[]);self.assertEqual(len(self.store.pending_proposals()),1)
 def test_invalid_entry_isolated_without_content_retry(self):
  def handle(stage,ctx):
   if stage=='construct' and source(ctx)['title']=='Bad':
    v=construction(ctx);v['events'][0]['what_changed'][0]['evidence_quote']='   ';return v
  llm=Fake(handle);result=self.run_news(llm,[raw(title='Bad'),raw('Different valid news.',url='https://example.org/good',title='Good')])
  self.assertEqual(result.status,'partial');self.assertEqual(result.metrics['articles_failed'],1)
  self.assertEqual(sum(s=='construct' and any(a['title']=='Bad' for a in c['sources']) for s,c in llm.calls),1)
  self.assertEqual(result.errors[0].code,'invalid_entry')
 def test_foreign_source_rejected(self):
  def handle(stage,ctx):
   if stage=='construct':
    v=construction(ctx);v['events'][0]['source_ids']=['A-fake'];return v
  self.assertEqual(self.run_news(Fake(handle)).status,'failed')
 def test_incomplete_not_successful_empty(self):
  self.assertEqual(self.run_news(Fake(lambda s,c:dict(complete=False,events=[]) if s=='construct' else None)).status,'failed')
 def test_valid_empty_cached(self):
  llm=Fake(lambda s,c:dict(complete=True,events=[]) if s=='construct' else None)
  self.assertEqual(self.run_news(llm).status,'completed');calls=len(llm.calls)
  self.run_news(llm);self.assertEqual(len(llm.calls),calls)
 def test_unknown_candidate_id_rejected(self):
  self.run_news()
  def handler(stage,ctx):
   if stage in ('group_identity','group_reconsider'):return dict(decision='same',event_id='E-fake',relation='duplicate',reason='bad')
  result=self.run_news(Fake(handler),[raw('Different news.',url='https://example.org/b')])
  self.assertEqual(result.status,'partial');self.assertEqual(len(self.store.events()),1)
 def test_empty_shortlist_new_event_after_six(self):
  self.run_news(articles=[raw(f'Rule {i} adopted.',url=f'https://example.org/{i}',title=f'Rule{i}') for i in range(6)])
  self.assertEqual(len(self.store.events()),6)
  llm=Fake();result=self.run_news(llm,[raw('Suez route reopened.',url='https://example.org/suez',title='Suez')])
  self.assertEqual(result.status,'completed');self.assertEqual(len(self.store.events()),7)
  self.assertEqual([s for s,c in llm.calls if s.startswith('group_')],['group_retrieve'])
 def test_analysis_wrong_source_rejected(self):
  def handler(stage,ctx):
   if stage=='analyze':return {**analysis(ctx),'primary_source_id':'A-fake'}
  result=self.run_news(Fake(handler));self.assertEqual(result.status,'partial');self.assertIsNone(result.events[0].score)
 def test_analysis_invalid_score_repaired_once(self):
  def handler(stage,ctx):
   if stage=='analyze':return {**analysis(ctx),'impact':6}
  llm=Fake(handler);result=self.run_news(llm);self.assertEqual(result.status,'partial')
  self.assertEqual(sum(s in ('analyze','analyze_batch') for s,c in llm.calls),2)
 def test_article_content_dedup_and_url_normalization(self):
  a=self.store.add_article(ArticleInput(**raw()));b=self.store.add_article(ArticleInput(**raw(url='https://example.org/a?utm_source=test')))
  self.assertEqual(a.article_id,b.article_id)
 def test_old_database_refused_unchanged(self):
  p=Path(self.folder.name)/'old.sqlite'
  with sqlite3.connect(p) as db:
   db.execute('CREATE TABLE metadata(key text,value text)');db.execute("INSERT INTO metadata VALUES('schema','news-engine-mvp-4')")
  db.close()
  before=p.read_bytes()
  with self.assertRaises(ValueError):Store(p)
  self.assertEqual(p.read_bytes(),before)
 def test_normalized_evidence_and_null_offsets_survive_persistence_restart(self):
  result=self.run_news();event=result.events[0].model_copy(deep=True)
  quote='The authority has adopted new requirements.'
  event.what_changed[0].evidence.quote=quote
  event.what_changed[0].evidence.start=None;event.what_changed[0].evidence.end=None
  event.evidence[0].evidence=event.what_changed[0].evidence.model_copy(deep=True)
  self.store.save_event(event)
  self.store.close();self.store=Store(self.path)
  saved=self.store.events()[0]
  self.assertEqual(saved.evidence[0].evidence.quote,quote)
  self.assertIsNone(saved.evidence[0].evidence.start);self.assertIsNone(saved.evidence[0].evidence.end)
  self.assertEqual(self.store.articles()[0].text,'The authority adopted a rule.')

 def test_storage_rejects_unknown_source_without_quote_matching(self):
  original=self.run_news().events[0];event=original.model_copy(deep=True)
  event.source_ids=['A-unknown']
  for ref in event.evidence:ref.source_id='A-unknown'
  for entry in event.what_changed:entry.source_id='A-unknown'
  with self.assertRaisesRegex(ValueError,'unknown Article'):self.store.save_event(event)
  self.assertEqual(self.store.events()[0].source_ids,original.source_ids)

 def test_writer_lock_and_interrupted_recovery(self):
  self.store.start_run('interrupted')
  with self.store.writer_lock():
   self.assertEqual(self.store.get_run('interrupted').status,'failed')
   with Store(self.path) as other:
    with self.assertRaises(BusyError):
     with other.writer_lock():pass
 def test_frozen_four_source_evidence_offline(self):
  from evaluation.real_corpus import load_sources,SMOKE_IDS,validate_corpus
  self.assertEqual(validate_corpus()['status'],'passed')
  sources=load_sources()
  result=self.run_news(articles=[raw(sources[k]['source']['body'],sources[k]['source']['url'],sources[k]['source']['title']) for k in SMOKE_IDS])
  self.assertEqual(result.status,'completed');self.assertEqual(len(result.sources),4)
  for e in result.events:
   for ref in e.evidence:
    a=next(a for a in result.sources if a.article_id==ref.source_id)
    self.assertEqual(a.text[ref.evidence.start:ref.evidence.end],ref.evidence.quote)
 def test_group_coverage_and_bounds(self):
  articles=[self.store.add_article(ArticleInput(**raw(f'Port disruption {i}.',f'https://example.org/{i}'))) for i in range(9)]
  groups=group_articles(articles,[])
  self.assertEqual({a for g in groups.groups for a in g.article_ids},{a.article_id for a in articles})
  self.assertTrue(all(len(g.article_ids)<=4 for g in groups.groups))
 def test_r5_strict_dimensions_arithmetic_and_enum_validation_remain_enabled(self):
  self.assertEqual(validate_r5(3,4,3),67)
  for values in [(True,1,1),(1.2,2,1),(6,2,1),(-1,2,1),(1,6,1),(1,2,6)]:
   with self.subTest(values=values),self.assertRaises(ValueError):validate_r5(*values)
  for total in (66,True,'67'):
   with self.subTest(total=total),self.assertRaises(ValueError):validate_r5(3,4,3,total=total)
  for name in ('impact_basis','relevance_basis','urgency_basis'):
   with self.subTest(basis=name),self.assertRaises(ValueError):validate_r5(3,4,3,**{name:'not_an_anchor'})

 def test_r5_temporarily_disabled_caps_accept_formerly_restricted_scores(self):
  import news_engine.models as models
  self.assertFalse(models.R5_CAPS_ENABLED)
  for values in [(1,0,0),(2,1,1),(2,2,3),(5,0,5)]:
   with self.subTest(values=values):self.assertEqual(validate_r5(*values),8*values[0]+7*values[1]+5*values[2])
  for claim_type in ('proposed','forecast'):
   self.assertEqual(validate_r5(5,0,5,impact_basis='none',relevance_basis='none',urgency_basis='none',
    claim_type=claim_type,has_treasury_action_or_constraint=False),65)
  score=Score(impact=5,relevance=0,urgency=5,total=65,reasoning=dict(impact='Independent impact.',relevance='No direct channel.',urgency='Independent urgency.'))
  self.assertEqual(score.total,65)

 def test_r5_existing_caps_can_be_restored_with_one_flag(self):
  with patch('news_engine.models.R5_CAPS_ENABLED',True):
   for values in [(1,0,0),(2,1,1),(2,2,3)]:
    with self.subTest(values=values),self.assertRaises(ValueError):validate_r5(*values)
   for caps,name in [(IMPACT_CAPS,'impact_basis'),(RELEVANCE_CAPS,'relevance_basis'),(URGENCY_CAPS,'urgency_basis')]:
    for basis,cap in caps.items():
     if cap<5:
      args=[0,5,0];args[{'impact_basis':0,'relevance_basis':1,'urgency_basis':2}[name]]=cap+1
      with self.subTest(basis=basis),self.assertRaises(ValueError):validate_r5(*args,**{name:basis})
   with self.assertRaises(ValueError):validate_r5(4,4,2,claim_type='proposed')
   with self.assertRaises(ValueError):validate_r5(2,4,2,claim_type='forecast')
   with self.assertRaises(ValueError):validate_r5(3,4,3,has_treasury_action_or_constraint=False)
   with self.assertRaises(ValueError):Score(impact=5,relevance=0,urgency=5,total=65,reasoning=dict(impact='Impact.',relevance='Relevance.',urgency='Urgency.'))

 def test_four_claim_types_roundtrip_construction_scoring_storage_and_json(self):
  reports={
   'actual':'The authority adopted the rule today.',
   'announced':'The authority announced that implementation will begin next month.',
   'proposed':'The authority proposed a rule that has not been adopted.',
   'forecast':'Analysts expect funding costs to increase next year.'}
  def handler(stage,ctx):
   if stage=='construct':
    value=construction(ctx)
    for draft in value['events']:draft['what_changed'][0]['claim_type']=draft['title']
    return value
   if stage=='analyze':return analysis(ctx)
  result=self.run_news(Fake(handler),[raw(text,url=f'https://example.org/{kind}',title=kind) for kind,text in reports.items()])
  self.assertEqual(result.status,'completed',result.errors)
  self.assertEqual(len(result.events),4)
  for event in RunResult.model_validate_json(result.model_dump_json()).events:
   entry=event.what_changed[0]
   self.assertEqual(entry.claim_type,event.title)
   self.assertEqual(entry.text,reports[entry.claim_type]);self.assertIsNotNone(event.score)
   self.assertEqual(entry.evidence.quote,reports[entry.claim_type])
  self.store.close();self.store=Store(self.path)
  self.assertEqual({e.what_changed[0].claim_type for e in self.store.events()},set(reports))
 def test_removed_claim_type_and_obsolete_scoring_type_field_are_rejected(self):
  result=self.run_news();entry=result.events[0].what_changed[0].model_dump()
  entry['claim_type']='unverified'
  draft=dict(text='A report.',claim_type='unverified',source_id='A1',evidence_quote='A report.',numbers=[])
  scoring=analysis(dict(current_event=result.events[0].model_dump(mode='json')))
  scoring['primary_claim_type']='unverified'
  for schema,value in [(EventEntry,entry),(EntryDraft,draft),(AnalysisDraft,scoring)]:
   with self.subTest(schema=schema.__name__),self.assertRaises(ValidationError):schema.model_validate(value)
  with self.assertRaisesRegex(ValueError,'Unsupported scoring Claim Type'):validate_r5(1,1,1,claim_type='unverified')
 def test_removed_claim_type_is_a_failure_without_reclassification(self):
  def handler(stage,ctx):
   if stage=='construct':
    value=construction(ctx);value['events'][0]['what_changed'][0]['claim_type']='unverified';return value
  llm=Fake(handler);result=self.run_news(llm)
  self.assertTrue(result.errors);self.assertEqual(result.events,[])
  self.assertFalse(any(stage in ('analyze','analyze_batch') for stage,ctx in llm.calls))
  self.assertEqual(sum(stage=='construct' for stage,ctx in llm.calls),1)
 def test_attributed_uncertain_forecast_is_preserved_without_actual_outcome(self):
  source='An unofficial report predicts that the port may reopen; the forecast is disputed by the operator.'
  def handler(stage,ctx):
   if stage=='construct':
    value=construction(ctx);value['events'][0]['what_changed'][0]['claim_type']='forecast';return value
   if stage=='analyze':return {**analysis(ctx),'why_it_matters':['The disputed prediction may inform conditional monitoring; reopening is not established.']}
  result=self.run_news(Fake(handler),[raw(source)])
  self.assertEqual(result.status,'completed',result.errors)
  self.assertEqual(result.events[0].what_changed[0].claim_type,'forecast')
  self.assertEqual(result.events[0].summary,source)
  self.assertEqual(result.events[0].evidence[0].evidence.quote,source)
 def test_unrepresentable_allegation_may_be_omitted_without_fallback(self):
  def handler(stage,ctx):
   if stage=='construct':return dict(complete=True,events=[])
  result=self.run_news(Fake(handler),[raw('An anonymous rumor alleges a port has closed, without confirmation.')])
  self.assertEqual(result.status,'completed');self.assertEqual(result.events,[])
  articles=self.store.articles()
  self.assertEqual(len(articles),1)
  self.assertIn('anonymous rumor',articles[0].text)
 def test_input_claim_types_and_output_scoring_selector_contract(self):
  for prompt in (PROMPT,ANALYSIS_PROMPT):
   for kind in ('actual:','announced:','proposed:','forecast:'):self.assertIn(kind,prompt)
   self.assertIn('independently verified',prompt);self.assertIn('Omit developments',prompt)
   self.assertIn('allegation',prompt);self.assertNotIn('unverified',prompt)
  construction=EventConstructionDraft.model_json_schema()['$defs']['EntryDraft']['properties']['claim_type']['enum']
  public=RunResult.model_json_schema()['$defs']['EventEntry']['properties']['claim_type']['enum']
  scoring=AnalysisDraft.model_json_schema()['properties']
  self.assertNotIn('primary_claim_type',scoring);self.assertNotIn('claim_type',scoring)
  self.assertEqual(scoring['primary_entry_index']['type'],'integer')
  self.assertEqual(construction,['actual','announced','proposed','forecast'])
  self.assertEqual(public,construction)
 def test_contract5_and6_databases_are_preserved_unchanged(self):
  for version in (5,6):
   path=Path(self.folder.name)/f'contract{version}.sqlite'
   with sqlite3.connect(path) as db:
    db.execute('CREATE TABLE metadata(key TEXT,value TEXT)')
    db.execute('INSERT INTO metadata VALUES(?,?)',('schema',f'news-engine-mvp-{version}'))
    db.execute('CREATE TABLE events(data TEXT)')
    db.execute('INSERT INTO events VALUES(?)',(json.dumps({'what_changed':[{'claim_type':'unverified'}]}),))
   db.close();before=path.read_bytes()
   with self.subTest(version=version),self.assertRaisesRegex(ValueError,'fresh database'):Store(path)
   self.assertEqual(path.read_bytes(),before)
 def test_api_event_only_output_and_saved_get(self):
  with TestClient(create_app(database=self.path,token='test',llm=Fake())) as client:
   self.assertEqual(client.post('/api/news/runs',json={'articles':[raw()]}).status_code,401)
   headers={'Authorization':'Bearer test'}
   response=client.post('/api/news/runs',json={'articles':[raw()]},headers=headers)
   self.assertEqual(response.status_code,200);body=response.json();self.assertEqual(body['schema_version'],7)
   self.assertIn('what_changed',body['events'][0]);self.assertNotIn('change_id',body['events'][0]);self.assertIn('evidence',body['events'][0])
   self.assertEqual(client.get('/api/news/runs/'+body['run_id'],headers=headers).json(),body)
 def test_schema_exposes_event_fields_and_rejects_numeric(self):
  ctx=dict(sources=[dict(article_id='A1',title='Rule',text='Rule adopted.')]);value=construction(ctx)
  with self.assertRaises(ValidationError):EventConstructionDraft.model_validate({**value,'changes':[]})
  value['events'][0]['numeric']=[]
  with self.assertRaises(ValidationError):EventConstructionDraft.model_validate(value)
 def test_provider_prompt_has_same_schema(self):
  requests=[]
  def respond(request):
   requests.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"complete":true,"events":[]}'}}]})
  with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
   llm=LLMClient('test','model','https://example.org/v1',http_client=transport)
   llm.complete(EventConstructionDraft,'Extract Events',{},stage='construct')
  schema=requests[0]['response_format']['json_schema']['schema']
  self.assertIn(json.dumps(schema,ensure_ascii=False,separators=(',',':')),requests[0]['messages'][0]['content'])
  self.assertNotIn('Numeric',schema.get('$defs',{}))
 def test_all_llm_stages_omit_output_token_limits_and_keep_usage(self):
  stages=['search','construct','group_retrieve','group_identity','group_reconsider','analyze_batch','analyze']
  for base_url in ['https://example.org/v1','https://openrouter.ai/api/v1']:
   requests=[]
   def respond(request):
    requests.append(json.loads(request.content))
    return httpx.Response(200,json={'usage':{'prompt_tokens':50,'completion_tokens':12000,'cost':0.02},
     'choices':[{'finish_reason':'stop','message':{'content':'{"complete":true,"events":[]}'}}]})
   with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
    llm=LLMClient('offline','model',base_url,http_client=transport)
    for stage in stages:
     with self.subTest(base_url=base_url,stage=stage):
      result=llm.complete(EventConstructionDraft,'Output contract',{'events':[]},stage=stage)
      self.assertTrue(result.complete)
      for key in ('max_tokens','max_completion_tokens','max_output_tokens'):self.assertNotIn(key,requests[-1])
      self.assertEqual(requests[-1]['response_format']['type'],'json_schema')
      self.assertEqual(llm.receipts[-1]['completion_tokens'],12000)
      self.assertEqual(llm.receipts[-1]['cost_usd'],0.02)
   self.assertEqual(len(requests),len(stages))

 def test_geographic_search_history_and_budget(self):
  queries=[dict(query='global rules',topic='regulatory',geography='Global'),dict(query='Suez routing',topic='industry',geography='Suez Canal')]
  class Exa:
   def search(self,q,**kw):return {'results':[]}
  llm=Fake(lambda s,c:dict(queries=queries))
  articles,errors=search(llm,Exa(),self.store,max_queries=2,now=NOW)
  self.assertFalse(errors);self.assertEqual(len(self.store.history()),2)
  self.assertEqual(self.store.history()[1]['geography'],'Suez Canal')
  _,errors=search(llm,Exa(),self.store,max_queries=1,now=NOW)
  self.assertTrue(errors)

 def test_paraphrase_with_exact_evidence_is_accepted(self):
  def handler(stage,ctx):
   if stage=='construct':
    v=construction(ctx);v['events'][0]['what_changed'][0]['text']='The authority has adopted new requirements.';return v
  result=self.run_news(Fake(handler),[raw('The authority adopted a rule.')])
  self.assertEqual(result.status,'completed')
  self.assertEqual(result.events[0].what_changed[0].text,'The authority has adopted new requirements.')
 def test_partial_entry_and_observation_preserve_usable_event_and_cache_errors(self):
  def handler(stage,ctx):
   if stage=='construct':
    v=construction(ctx);entries=v['events'][0]['what_changed']
    entries[0]['text']='The rule applies.'
    entries[0]['numbers']=[dict(value='0',unit='percent',source_quote='zero percent'),dict(value='99',unit='percent',source_quote='zero percent'),dict(value='14',unit='percent',source_quote='Unlocated numeric supporting passage'),dict(value=None,unit=None,source_quote=None)]
    entries.extend([dict(text='Invented detail.',claim_type='actual',source_id=source(ctx)['article_id'],evidence_quote='',numbers=[]),
                    dict(text='The limit is 99 percent.',claim_type='actual',source_id=source(ctx)['article_id'],evidence_quote=source(ctx)['text'],numbers=[])])
    return v
  llm=Fake(handler)
  result=self.run_news(llm,[raw('The rule applies a zero percent risk weight.')])
  self.assertEqual(result.status,'partial',result.errors);self.assertEqual(len(result.events),1)
  self.assertEqual(len(result.events[0].what_changed),2)
  self.assertEqual([n.value for n in result.events[0].what_changed[0].numbers],['0','99','14'])
  self.assertEqual(result.metrics['discarded_entries'],1);self.assertEqual(result.metrics['discarded_observations'],1)
  calls=len(llm.calls)
  cached=self.run_news(llm,[raw('The rule applies a zero percent risk weight.')])
  self.assertEqual(cached.status,'partial');self.assertEqual(len(llm.calls),calls)
  self.assertEqual(cached.metrics['discarded_entries'],1)
 def test_bad_optional_metadata_does_not_discard_event(self):
  def handler(stage,ctx):
   if stage=='construct':
    v=construction(ctx);v['events'][0]['identity']={**IDENTITY,'location':'Fabricated port','occurrence_at':'2026-10-22T00:00:00Z'};return v
  result=self.run_news(Fake(handler))
  self.assertEqual(result.status,'partial');self.assertEqual(len(result.events),1)
  self.assertIsNone(result.events[0].identity.location)
  self.assertEqual(result.events[0].identity.occurrence_at.isoformat(),'2026-10-22T00:00:00+00:00')
 def test_bad_draft_does_not_discard_sibling_event(self):
  def handler(stage,ctx):
   if stage=='construct':
    v=construction(ctx);v['events'].append({**v['events'][0],'source_ids':['A-other']});return v
  result=self.run_news(Fake(handler))
  self.assertEqual(result.status,'partial');self.assertEqual(len(result.events),1)
 def test_analysis_numeric_source_check_is_disabled(self):
  def handler(stage,ctx):
   if stage=='analyze':return {**analysis(ctx),'why_it_matters':['The company owes 99999 USD.']}
  llm=Fake(handler);result=self.run_news(llm)
  self.assertEqual(result.status,'completed',result.errors);self.assertIsNotNone(result.events[0].score)
  self.assertEqual(result.events[0].why_it_matters,['The company owes 99999 USD.'])
  self.assertFalse(any(stage=='analyze' for stage,ctx in llm.calls))

 def test_null_entry_and_optional_number_are_isolated(self):
  def handler(stage,ctx):
   if stage=='construct':
    v=construction(ctx);entries=v['events'][0]['what_changed']
    entries[0]['numbers']=[dict(value=None,unit=None,source_quote=None)]
    entries.append(dict(text=None,claim_type='actual',source_id=source(ctx)['article_id'],evidence_quote=None,numbers=[]))
    return v
  result=self.run_news(Fake(handler))
  self.assertEqual(result.status,'partial');self.assertEqual(len(result.events),1)
  self.assertEqual(len(result.events[0].what_changed),1);self.assertEqual(result.events[0].what_changed[0].numbers,[])
 def test_numeric_values_and_units_are_retained_without_factual_validation(self):
  def handler(stage,ctx):
   if stage=='construct':
    v=construction(ctx);v['events'][0]['what_changed'][0]['numbers']=[dict(value='25',unit='USD',source_quote='25 basis points')];return v
  result=self.run_news(Fake(handler),[raw('The bank lowered its policy rate by 25 basis points.')])
  self.assertEqual(result.status,'completed',result.errors)
  observation=result.events[0].what_changed[0].numbers[0]
  self.assertEqual((observation.value,observation.unit,observation.source_quote),('25','USD','25 basis points'))
  self.assertEqual(result.metrics['discarded_observations'],0)

 def test_numeric_scalar_formats_normalize_without_value_unit_matching(self):
  def handler(stage,ctx):
   if stage=='construct':
    value=construction(ctx);value['events'][0]['what_changed'][0]['numbers']=[
      dict(value=99,unit='USD',source_quote='zero percent'),
      dict(value=1.25,unit='EUR billion',source_quote='zero percent')]
    return value
  result=self.run_news(Fake(handler),[raw('The rule applies a zero percent risk weight.')])
  self.assertEqual(result.status,'completed',result.errors)
  numbers=result.events[0].what_changed[0].numbers
  self.assertEqual([(n.value,n.unit) for n in numbers],[('99','USD'),('1.25','EUR billion')])
  self.assertTrue(all(type(n.value) is str for n in numbers))

 def test_cli_uses_same_contract7_pipeline(self):
  import sys
  from types import SimpleNamespace
  from news_engine.pipeline import main
  source=Path(self.folder.name)/'input.json';output=Path(self.folder.name)/'result.json';db=Path(self.folder.name)/'cli.sqlite'
  source.write_text(json.dumps([raw()]),encoding='utf-8')
  llm=Fake();llm.close=lambda:None
  credentials=SimpleNamespace(llm_api_key='test',llm_model='recorded-event-only',llm_base_url='https://example.org/v1')
  with patch.object(sys,'argv',['pipeline','--input',str(source),'--output',str(output),'--database',str(db),'--analysis-batch-size','2','--analysis-workers','1']),patch('news_engine.config.load_credentials',return_value=credentials),patch('news_engine.clients.LLMClient',return_value=llm):
   self.assertEqual(main(),0)
  body=json.loads(output.read_text(encoding='utf-8'))
  self.assertEqual(body['schema_version'],7);self.assertIn('what_changed',body['events'][0])
  self.assertEqual(body['metrics']['analysis_batch_size'],2);self.assertEqual(body['metrics']['analysis_workers'],1)

 def test_new_decision_does_not_require_historical_relation(self):
  self.run_news()
  for relation in ['material',None]:
   def handler(stage,ctx):
    if stage=='group_identity':return dict(decision='new',event_id=None,relation=relation,reason='Identifiable different issue.')
   result=self.run_news(Fake(handler),[raw(f'New issue {relation}.',url=f'https://example.org/{relation}')])
   self.assertEqual(result.status,'completed',result.errors)
  self.assertEqual(len(self.store.events()),3)
 def test_nonbinary_output_is_a_schema_failure(self):
  self.run_news()
  def handler(stage,ctx):
   if stage in ('group_identity','group_reconsider'):
    return dict(decision='uncertain',event_id=None,relation=None,reason='Invalid nonbinary output')
  llm=Fake(handler)
  result=self.run_news(llm,[raw('A possibly repeated development.',url='https://example.org/b')])
  self.assertEqual(result.status,'partial');self.assertEqual(len(self.store.pending_proposals()),1)
  self.assertEqual(sum(s=='group_reconsider' for s,c in llm.calls),1)
  self.assertNotIn('Uncertain Event association',result.errors[0].message)

 def test_binary_schema_and_best_effort_ambiguous_choices(self):
  from news_engine.events import AssociationDraft
  schema=AssociationDraft.model_json_schema()
  self.assertEqual(schema['properties']['decision']['enum'],['same','new'])
  with self.assertRaises(ValidationError):AssociationDraft(decision='uncertain',relation=None,reason='unsupported')
  seed=self.run_news().events[0]
  for decision in ['same','new']:
   def handler(stage,ctx):
    if stage=='group_identity':return dict(decision=decision,event_id=seed.event_id if decision=='same' else None,
      relation='material' if decision=='same' else None,reason='Best-supported issue boundary despite overlap.')
   result=self.run_news(Fake(handler),[raw(f'A related rule development {decision}.',url=f'https://example.org/{decision}')])
   self.assertEqual(result.status,'completed',result.errors)
   self.assertEqual(result.events[0].event_id==seed.event_id,decision=='same')

 def test_current_run_and_cross_run_canonical_development_dedup(self):
  def handler(stage,ctx):
   if stage=='construct':
    value=construction(ctx)
    for draft in value['events']:draft['what_changed'][0]['text']='Authority adopted a rule.'
    return value
   if stage=='group_identity':return dict(decision='same',event_id=ctx['candidates'][0]['event_id'],relation='material',reason='Same issue reported by another source.')
  llm=Fake(handler)
  result=self.run_news(llm,[raw('Authority adopted a rule.',url='https://example.org/one'),raw('Another source confirms the rule.',url='https://example.org/two')])
  self.assertEqual(result.status,'completed');self.assertEqual(len(self.store.events()),1)
  event=result.events[0];self.assertEqual(len(event.what_changed),1);self.assertEqual(len(event.evidence),2)
  self.assertEqual(len(event.what_changed[0].supporting_evidence),1)
  self.assertEqual(sum(s in ('analyze','analyze_batch') for s,c in llm.calls),1)
  key=event.event_id;before=len(llm.calls)
  self.store.close();self.store=Store(self.path)
  again=self.run_news(llm,[raw('Third source repeats the same rule.',url='https://example.org/three')])
  self.assertEqual(again.events[0].event_id,key);self.assertEqual(len(again.events[0].what_changed),1)
  self.assertEqual(len(again.events[0].what_changed[0].supporting_evidence),2)
  self.assertFalse(any(s in ('analyze','analyze_batch') for s,c in llm.calls[before:]))

 def test_three_categories_not_inherited_from_group_topic(self):
  def handler(stage,ctx):
   if stage=='construct':
    value=construction(ctx)
    for draft in value['events']:draft['category']=draft['title']
    return value
  result=self.run_news(Fake(handler),[raw(f'News {category}.',url=f'https://example.org/{category}',title=category)
     for category in ['regulatory','macroeconomics','industry']])
  self.assertEqual(result.status,'completed');self.assertEqual({e.category for e in result.events},{'regulatory','macroeconomics','industry'})

 def test_distinct_factual_qualifications_are_not_canonicalized_together(self):
  def handler(stage,ctx):
   if stage=='construct':
    value=construction(ctx)
    for draft in value['events']:
     entry=draft['what_changed'][0];entry['text']='Facility operation reported.';entry['claim_type']=draft['title']
    return value
   if stage=='group_identity':return dict(decision='same',event_id=ctx['candidates'][0]['event_id'],relation='material',reason='Same facility with a distinct factual qualification.')
   if stage=='analyze':return analysis(ctx)
  result=self.run_news(Fake(handler),[raw(f'Facility report: {kind}.',url=f'https://example.org/{kind}',title=kind)
      for kind in ['proposed','announced','actual']])
  self.assertEqual(result.status,'completed',result.errors)
  self.assertEqual(len(self.store.events()),1)
  self.assertEqual({entry.claim_type for entry in result.events[0].what_changed},{'proposed','announced','actual'})
