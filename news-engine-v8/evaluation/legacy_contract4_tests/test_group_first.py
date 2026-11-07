"""Group-first and geographic planning mechanical contracts; no paid providers."""
import tempfile
from pathlib import Path
import unittest
from pydantic import ValidationError
from test_mvp import article, RecordedLLM, draft
from news_engine.group_first import group_articles, extract_group, EventExtractionDraft
from news_engine.search import QueryPlan, search
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store


class GroupFirstTests(unittest.TestCase):
    def test_cross_region_coverage_and_group_bounds(self):
        sources=[article('Port congestion disrupts shipping capacity across Europe and Asia.',key=str(i)) for i in range(9)]
        result=group_articles(sources,[])
        self.assertEqual({a for g in result.groups for a in g.article_ids},{a.article_id for a in sources})
        self.assertTrue(all(len(g.article_ids)<=4 for g in result.groups))
        self.assertEqual(len(result.groups),3)

    def test_multi_event_source_and_categories_survive_restart(self):
        def handle(stage,ctx):
            if stage=='extract':
                return dict(complete=True,events=[dict(title=text,identity=dict(subject=subject,action='decision',occurrence=text,scope=subject),category=category,source_id=ctx['source_id'],changes=[draft(text)]) for text,subject,category in [('Rule adopted.','Regulator','regulatory'),('Freight increased.','Shipping','industry')]])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new.sqlite'
            llm=RecordedLLM(handle)
            with Store(path) as store:
                result=run_pipeline(store,llm,[dict(url='https://example.org/multi',title='Two developments',text='Rule adopted. Freight increased.')])
                self.assertEqual(result.status,'completed')
                self.assertEqual(len(result.events),2)
                self.assertEqual({e.category for e in result.events},{'regulatory','industry'})
                self.assertTrue(all(e.identity.location is None for e in result.events))
                ids={e.event_id for e in result.events}
                calls=len(llm.calls)
            with Store(path) as store:
                again=run_pipeline(store,llm,[dict(url='https://example.org/multi',title='Two developments',text='Rule adopted. Freight increased.')])
                self.assertEqual({e.event_id for e in again.events},ids)
                self.assertEqual(len(llm.calls),calls)

    def test_authentic_four_source_group_coverage(self):
        from evaluation.real_corpus import load_sources, SMOKE_IDS
        corpus=load_sources()
        sources=[article(corpus[key]['source']['body'],key=key,title=corpus[key]['source']['title']) for key in SMOKE_IDS]
        result=group_articles(sources,[])
        self.assertEqual({a for g in result.groups for a in g.article_ids},set(SMOKE_IDS))
        self.assertTrue(all(g.article_ids for g in result.groups))

    def test_authentic_four_source_scripted_pipeline(self):
        from evaluation.real_corpus import load_sources, SMOKE_IDS
        from test_mvp import grouping, policy_timing
        corpus=load_sources()
        identities={key:dict(subject='Federal Reserve',action='development',
            occurrence='PPP facility' if key in (SMOKE_IDS[0],SMOKE_IDS[2]) else key,
            scope='PPP facility' if key in (SMOKE_IDS[0],SMOKE_IDS[2]) else key) for key in SMOKE_IDS}
        def handle(stage,ctx):
            if stage=='extract':
                key=next(k for k in SMOKE_IDS if corpus[k]['source']['title']==ctx['title'])
                changes=[]
                for paragraph in ctx['text'].split('\n\n'):
                    change=draft(paragraph,identity=identities[key],
                        claim_type='actual' if 'fully operational' in paragraph else 'announced')
                    if '30 days' in paragraph and 'comment' in paragraph.lower():
                        change['numeric']=[dict(value='30',unit='days',kind='level',
                            comparison_basis='after publication in the Federal Register',source_quote='30 days')]
                    changes.append(change)
                return dict(complete=True,changes=changes)
            if stage=='group':
                identity=ctx['change']['identity']
                match=next((e for e in ctx['candidates'] if e['identity']['occurrence']==identity['occurrence']),None)
                return grouping(identity=identity,event_id=match['event_id'] if match else None,
                    confidence='high' if match else 'new',relation='material' if match else 'new')
        raw=[dict(url=corpus[k]['source']['url'],title=corpus[k]['source']['title'],text=corpus[k]['source']['body']) for k in SMOKE_IDS]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new.sqlite';llm=RecordedLLM(handle)
            with Store(path) as store:
                result=run_pipeline(store,llm,raw,workers=1)
                self.assertEqual(result.status,'completed',result.errors)
                self.assertEqual(len(result.sources),4)
                self.assertEqual(len(result.events),3)
                facility=next(e for e in result.events if e.identity.occurrence=='PPP facility')
                self.assertEqual(len({c.source_id for c in facility.what_changed}),2)
                for event in result.events:
                    for change in event.what_changed:
                        source=next(a for a in result.sources if a.article_id==change.source_id)
                        self.assertEqual(source.text[change.evidence.start:change.evidence.end],change.evidence.quote)
                ids={e.event_id for e in result.events};calls=len(llm.calls)
            with Store(path) as store:
                again=run_pipeline(store,llm,raw)
                self.assertEqual({e.event_id for e in again.events},ids)
                self.assertEqual(len(llm.calls),calls)

    def test_proposal_children_share_identity_and_retry_atomically(self):
        from test_mvp import grouping
        identity=dict(subject='Board',action='rule change',occurrence='Eligibility rule',scope='Program eligibility')
        fail_novelty=[True]
        def handle(stage,ctx):
            if stage=='extract':
                return dict(complete=True,events=[dict(title='Eligibility rule',identity=identity,
                    category='regulatory',source_id=ctx['source_id'],changes=[
                        draft(text,identity=identity,claim_type='announced')
                        for text in ctx['text'].split(' | ')])])
            if stage=='group':
                if 'event_proposal' in ctx:
                    self.assertEqual(len(ctx['event_proposal']['changes']),2)
                    return grouping(event_id=ctx['candidates'][0]['event_id'],confidence='high',relation='material',identity=identity)
                # Child calls are novelty only, never a second identity decision.
                self.assertIn('identity_decision',ctx)
                if fail_novelty[0] and 'Temporary' in ctx['change']['description']:
                    raise RuntimeError('Injected provider failure')
                return grouping(event_id=ctx['candidates'][0]['event_id'],confidence='high',relation='material',identity=identity)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'batch.sqlite'; llm=RecordedLLM(handle)
            with Store(path) as store:
                seed=run_pipeline(store,llm,[dict(url='https://example.org/seed',title='Seed',text='Board announced eligibility.')])
                key=seed.events[0].event_id
                raw=[dict(url='https://example.org/update',title='Update',text='Board will change eligibility. | Temporary scope applies.')]
                failed=run_pipeline(store,llm,raw)
                self.assertEqual(failed.status,'partial')
                self.assertEqual(len([c for c in store.changes() if c.event_id is None]),2)
                self.assertEqual(len(store.events()[0].what_changed),1)
            fail_novelty[0]=False
            before=len(llm.calls)
            with Store(path) as store:
                retry=run_pipeline(store,llm,raw)
                self.assertEqual(retry.status,'completed',retry.errors)
                self.assertEqual({c.event_id for c in store.changes()},{key})
                self.assertEqual(len(retry.events[0].what_changed),3)
                calls=llm.calls[before:]
                self.assertEqual(sum(stage=='group_identity' for stage,_ in calls),1)
                self.assertFalse(any(stage=='extract' for stage,_ in calls))
                calls_before=len(llm.calls)
                run_pipeline(store,llm,raw)
                self.assertEqual(len(llm.calls),calls_before)

    def test_new_proposal_preserves_all_children_without_sibling_identity(self):
        identity=dict(subject='Board',action='rule change',occurrence='Eligibility rule',scope='Program eligibility')
        def handle(stage,ctx):
            if stage=='extract':
                return dict(complete=True,events=[dict(title='Eligibility',identity=identity,
                    category='regulatory',source_id=ctx['source_id'],changes=[
                        draft(text,identity=identity,claim_type='announced') for text in ctx['text'].split(' | ')])])
            if stage=='group':
                self.fail('A new proposal needs no sibling identity/novelty call')
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder)/'batch.sqlite') as store:
            result=run_pipeline(store,RecordedLLM(handle),[dict(url='https://example.org/new',title='New',
                text='Board will temporarily modify eligibility. | Only program loans qualify.')])
            self.assertEqual(result.status,'completed',result.errors)
            self.assertEqual(len(result.events),1)
            self.assertEqual(len(result.events[0].what_changed),2)

    def test_geographic_plan_and_budget(self):
        queries=[dict(query='global regulation',topic='regulatory',geography='Global'),dict(query='Brazil interest rates',topic='macroeconomics',geography='Brazil'),dict(query='Asia Europe freight',topic='industry',geography='Asia-Europe trade lane')]
        self.assertEqual(len(QueryPlan(queries=queries).queries),3)
        with self.assertRaises(ValidationError):QueryPlan(queries=[dict(query=' ',topic='industry',geography='Global')])
        with self.assertRaises(ValidationError):QueryPlan(queries=[dict(query='x',topic='other',geography='Global')])
        class Exa:
            def __init__(self):self.calls=[]
            def search(self,q,**kw):self.calls.append((q,kw));return {'results':[]}
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder)/'new.sqlite') as store:
            exa=Exa(); coverage=[]
            _, errors=search(RecordedLLM(lambda *_:dict(queries=queries)),exa,store,max_queries=3,coverage=coverage)
            self.assertFalse(errors);self.assertEqual(len(exa.calls),3);self.assertEqual(len(coverage),3)
            _, errors=search(RecordedLLM(lambda *_:dict(queries=queries)),exa,store,max_queries=2)
            self.assertTrue(errors);self.assertEqual(len(exa.calls),3)
            _, errors=search(RecordedLLM(lambda *_:dict(queries=[queries[0],queries[0]])),exa,store,max_queries=3)
            self.assertTrue(errors)
