"""Offline group construction/consolidation contracts; fakes are not model-quality labels."""
from hashlib import sha256
import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from news_engine.models import ArticleInput
from news_engine.storage import Store
from news_engine.pipeline import run_pipeline
from news_engine.group_first import construct_events,group_articles
from test_event_only import Fake,raw,construction,analysis,NOW,IDENTITY


def grouped_response(ctx,text=None):
    drafts=construction(ctx)['events']
    event=drafts[0]
    event['source_ids']=[a['article_id'] for a in ctx['sources']]
    event['what_changed']=[entry for draft in drafts for entry in draft['what_changed']]
    if text:
        for entry in event['what_changed']:entry['text']=text
    return dict(complete=True,events=[event])


class GroupConsolidationTests(TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/'groups.sqlite'
        self.store=Store(self.path)

    def tearDown(self):
        self.store.close();self.folder.cleanup()

    def run_news(self,llm,articles,**kwargs):
        return run_pipeline(self.store,llm,articles,reference_time=NOW,**kwargs)

    def reports(self):
        return [raw(f'Authority adopted a rule. Report {word}.',url=f'https://example.org/{word}')
                for word in ('alpha','beta','gamma','delta','epsilon')]

    def test_one_call_per_group_and_multi_article_evidence(self):
        def handler(stage,ctx):
            if stage=='construct':
                self.assertNotIn('source_id',ctx);self.assertNotIn('text',ctx)
                self.assertEqual(len(ctx['sources']),2)
                return grouped_response(ctx)
        result=self.run_news(Fake(handler),[
            raw('Authority adopted lending rules in Europe.',url='https://example.org/europe'),
            raw('Authority adopted lending rules in Asia-Pacific.',url='https://example.org/asia')])
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual(result.metrics['llm_calls_by_stage']['construct'],1)
        event=result.events[0]
        self.assertEqual(len(event.what_changed),2);self.assertEqual(len(event.source_ids),2)
        self.assertIsNone(event.identity.location)
        articles={a.article_id:a for a in result.sources}
        for entry in event.what_changed:
            self.assertEqual(articles[entry.source_id].text[entry.evidence.start:entry.evidence.end],entry.evidence.quote)

    def test_normalized_evidence_keeps_its_source_and_null_offsets_after_restart(self):
        normalized='The authority has adopted new regulatory requirements.'
        def handler(stage,ctx):
            if stage=='construct':
                value=grouped_response(ctx)
                value['events'][0]['what_changed'][1]['evidence_quote']=normalized
                return value
        result=self.run_news(Fake(handler),self.reports()[:2])
        self.assertEqual(result.status,'completed',result.errors);self.assertEqual(len(result.events),1)
        self.assertEqual(len(result.events[0].what_changed),2)
        entry=result.events[0].what_changed[1]
        self.assertEqual(entry.evidence.quote,normalized)
        self.assertIsNone(entry.evidence.start);self.assertIsNone(entry.evidence.end)
        self.assertEqual(result.metrics['articles_failed'],0)
        source=next(a for a in self.store.articles() if a.article_id==entry.source_id)
        self.assertNotIn(normalized,source.text)
        self.store.close();self.store=Store(self.path)
        stored=next(e for e in self.store.events()[0].what_changed if e.source_id==entry.source_id)
        self.assertEqual(stored.evidence.quote,normalized);self.assertIsNone(stored.evidence.start)

    def test_existing_article_outside_group_is_not_an_allowed_source(self):
        outsider=self.store.add_article(ArticleInput(**raw('Unrelated port news.',url='https://example.org/outsider')))
        self.store.save_group_proposals([outsider.article_id],[])
        def handler(stage,ctx):
            if stage=='construct':
                value=construction(ctx)
                value['events'].append({**value['events'][0],'source_ids':[outsider.article_id]})
                return value
        result=self.run_news(Fake(handler),[raw()])
        self.assertEqual(result.status,'partial');self.assertEqual(len(result.events),1)
        self.assertNotIn(outsider.article_id,result.events[0].source_ids)
        self.assertTrue(any(e.code=='invalid_source' for e in result.errors))

    def test_cross_group_dedup_before_persistent_ids_and_scoring(self):
        def handler(stage,ctx):
            if stage=='construct':return grouped_response(ctx,'Authority adopted a rule.')
            if stage=='group_identity':
                self.assertEqual(ctx['candidate_kind'],'local_draft')
                self.assertEqual(self.store.events(),[])
                self.assertTrue(all(c['event_id'].startswith('D') for c in ctx['candidates']))
                return dict(decision='same',event_id=ctx['candidates'][0]['event_id'],relation='duplicate',reason='Same rule reported across groups.')
            if stage=='analyze':
                self.assertEqual(len(ctx['current_event']['evidence']),5)
        llm=Fake(handler);result=self.run_news(llm,self.reports())
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual(result.metrics['article_groups'],2)
        self.assertEqual([s for s,c in llm.calls],['construct','construct','group_identity','analyze_batch'])
        self.assertEqual(len(result.events),1);event=result.events[0]
        self.assertTrue(event.event_id.startswith('E'));self.assertEqual(len(event.what_changed),1)
        self.assertEqual(len(event.what_changed[0].supporting_evidence),4)
        self.assertEqual(len(event.source_ids),5)

    def test_cross_group_consolidation_then_historical_identity_and_category(self):
        seed=self.run_news(Fake(),[raw()]).events[0]
        def handler(stage,ctx):
            if stage=='construct':
                value=grouped_response(ctx,'A new restriction was announced.')
                value['events'][0]['category']='industry'
                for entry in value['events'][0]['what_changed']:entry['claim_type']='announced'
                return value
            if stage=='group_identity':
                candidates=ctx['candidates']
                if ctx['candidate_kind']=='historical_event':
                    self.assertEqual(len(ctx['event_proposal']['source_ids']),5)
                    self.assertEqual(len(self.store.events()),1)
                    self.assertEqual(candidates[0]['event_id'],seed.event_id)
                return dict(decision='same',event_id=candidates[0]['event_id'],relation='material',reason='Same issue, new qualified restriction.')
        llm=Fake(handler);result=self.run_news(llm,self.reports())
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual([c['candidate_kind'] for s,c in llm.calls if s=='group_identity'],['local_draft','historical_event'])
        event=result.events[0];self.assertEqual(event.event_id,seed.event_id)
        self.assertEqual(event.category,'regulatory');self.assertEqual(len(event.source_ids),6)
        self.assertEqual({e.claim_type for e in event.what_changed},{'actual','announced'})
        self.assertEqual(sum(s in ('analyze','analyze_batch') for s,c in llm.calls),1)
        self.store.close();self.store=Store(self.path)
        again=self.run_news(llm,[])
        self.assertFalse(again.errors);self.assertEqual(self.store.events()[0].event_id,seed.event_id)

    def test_analysis_can_score_from_a_canonical_entry_supporting_source(self):
        def handler(stage,ctx):
            if stage=='construct':return grouped_response(ctx,'Authority adopted a rule.')
            if stage=='analyze':
                ref=ctx['current_event']['what_changed'][0]['supporting_evidence'][0]
                self.assertIn(ref['source_id'],ctx['allowed_values']['primary_source_id'])
                return {**analysis(ctx),'primary_source_id':ref['source_id'],
                        'primary_evidence_quote':ref['evidence']['quote']}
        llm=Fake(handler);result=self.run_news(llm,self.reports()[:2])
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual(sum(stage in ('analyze','analyze_batch') for stage,ctx in llm.calls),1)
        self.assertIsNotNone(result.events[0].score)

    def test_calendar_and_clock_values_are_not_source_validated(self):
        def handler(stage,ctx):
            if stage=='construct':
                value=construction(ctx)
                value['events'][0]['identity']['occurrence_at']='2030-04-17T09:47:00Z'
                value['events'][0]['what_changed'][0]['text']='The authority announced the rule on April 17, 2030 at 09:47.'
                return value
            if stage=='analyze':
                return {**analysis(ctx),'why_it_matters':['Monitor the announcement dated 2030-04-17 at 09:47 UTC; compare April 17, 2030 and 17 April 2030, in 2030 and Q2 2030.']}
        llm=Fake(handler);result=self.run_news(llm,[raw('The authority announced the rule on Thursday.')])
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual(result.events[0].identity.occurrence_at.isoformat(),'2030-04-17T09:47:00+00:00')
        self.assertEqual(sum(stage in ('analyze','analyze_batch') for stage,ctx in llm.calls),1)

    def test_publication_timestamp_does_not_reject_prose_calendar_date(self):
        def handler(stage,ctx):
            if stage=='analyze':
                return {**analysis(ctx),'reasoning':{**analysis(ctx)['reasoning'],
                    'urgency':'The source was published on 2020-04-17, before the reference time.'}}
        article={**raw(),'published_at':'2020-04-17T00:00:00Z'}
        result=self.run_news(Fake(handler),[article])
        self.assertEqual(result.status,'completed',result.errors)
        self.assertIsNotNone(result.events[0].score)

    def test_financial_narrative_numbers_are_not_deterministically_source_validated(self):
        def handler(stage,ctx):
            if stage=='analyze':return {**analysis(ctx),'why_it_matters':['Company cost is USD 17.']}
        result=self.run_news(Fake(handler),[raw('The rule was announced on 2020-04-17.')])
        self.assertEqual(result.status,'completed',result.errors);self.assertIsNotNone(result.events[0].score)

    def test_bare_years_and_numeric_prose_do_not_trigger_analysis_repair(self):
        def handler(stage,ctx):
            if stage=='analyze':
                return {**analysis(ctx),'why_it_matters':['The 2026 outlook could mean USD 99999, 17 percent and 25 basis points.']}
        llm=Fake(handler);result=self.run_news(llm,[raw('Management revised its outlook.')])
        self.assertEqual(result.status,'completed',result.errors)
        self.assertIsNotNone(result.events[0].score)
        self.assertEqual(sum(stage=='analyze_batch' for stage,ctx in llm.calls),1)
        self.assertFalse(any(stage=='analyze' for stage,ctx in llm.calls))

    def test_duplicate_label_cannot_drop_distinct_qualification(self):
        seed=self.run_news(Fake(),[raw()]).events[0]
        def handler(stage,ctx):
            if stage=='construct':
                value=construction(ctx);value['events'][0]['what_changed'][0]['claim_type']='announced';return value
            if stage=='group_identity':return dict(decision='same',event_id=seed.event_id,relation='duplicate',reason='Repeated broad issue.')
        llm=Fake(handler);result=self.run_news(llm,[raw('The authority announced a future restriction for PPP loans only.',url='https://example.org/restriction')])
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual({e.claim_type for e in result.events[0].what_changed},{'actual','announced'})
        self.assertEqual(sum(s in ('analyze','analyze_batch') for s,c in llm.calls),1)

    def test_all_merged_receipts_survive_failed_atomic_write_and_restart(self):
        def handler(stage,ctx):
            if stage=='construct':return grouped_response(ctx,'Authority adopted a rule.')
            if stage=='group_identity':return dict(decision='same',event_id=ctx['candidates'][0]['event_id'],relation='material',reason='Same issue.')
        save=self.store._save_event
        def fail_after_insert(event):
            save(event);raise ValueError('Injected failure after insert')
        with patch.object(self.store,'_save_event',side_effect=fail_after_insert):
            result=self.run_news(Fake(handler),self.reports())
        self.assertEqual(result.status,'partial');self.assertEqual(self.store.events(),[])
        pending=self.store.pending_proposals();self.assertEqual(len(pending),2)
        expected='E'+sha256(pending[0].proposal_key.encode()).hexdigest()[:20]
        self.store.close();self.store=Store(self.path)
        llm=Fake(handler);retried=self.run_news(llm,[])
        self.assertEqual(retried.status,'completed',retried.errors)
        self.assertEqual(retried.events[0].event_id,expected)
        self.assertFalse(any(s=='construct' for s,c in llm.calls))
        self.assertEqual(self.store.pending_proposals(),[])
        self.assertEqual(len(retried.events[0].source_ids),5)

    def test_group_receipts_and_all_source_states_rollback_together(self):
        articles=[self.store.add_article(ArticleInput(**a)) for a in self.reports()[:2]]
        mapping={a.article_id:a for a in articles}
        group=group_articles(articles,[]).groups[0]
        receipts,errors,failed=construct_events(group,mapping,{},Fake(),16000)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_group_proposals(group.article_ids,[receipts[0],receipts[0]],errors,failed)
        self.assertEqual(self.store.pending_proposals(),[])
        self.assertTrue(all(self.store.construction_status(a.article_id)=='pending' for a in articles))

    def test_failed_cross_group_decision_retains_draft_for_historical_retry(self):
        def handler(stage,ctx):
            if stage=='construct':return grouped_response(ctx,'Authority adopted a rule.')
            if stage=='group_identity':raise RuntimeError('Injected consolidation provider failure')
        first=self.run_news(Fake(handler),self.reports())
        self.assertEqual(first.status,'partial')
        self.assertTrue(any(e.stage=='consolidate' for e in first.errors))
        self.assertEqual(len(self.store.pending_proposals()),1)
        self.assertEqual(len(self.store.articles()),5)
        event=self.store.events()[0];self.assertEqual(len(event.source_ids),4)
        def retry(stage,ctx):
            if stage=='group_identity':return dict(decision='same',event_id=event.event_id,relation='duplicate',reason='Same source-grounded development.')
        self.store.close();self.store=Store(self.path)
        llm=Fake(retry);result=self.run_news(llm,[])
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual(result.events[0].event_id,event.event_id)
        self.assertEqual(len(result.events[0].source_ids),5)
        self.assertEqual(self.store.pending_proposals(),[])
        self.assertFalse(any(stage in ('construct','analyze','analyze_batch') for stage,ctx in llm.calls))

    def test_long_source_is_forwarded_whole_despite_retired_context_arguments(self):
        text='Authority adopted a rule. '+('Full source text. '*2200)
        self.assertGreater(len(text),16000)
        seen=[]
        def handler(stage,ctx):
            if stage=='construct':seen.append(ctx['sources'][0]['text'])
        llm=Fake(handler);result=self.run_news(llm,[raw(text)],chunk_size=40)
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual(seen,[text]);self.assertEqual(self.store.articles()[0].text,text)
        self.assertEqual(result.events[0].what_changed[0].evidence.quote,text)
        articles=self.store.articles()
        groups=group_articles(articles,[],max_chars=1)
        proposals,errors,failed=construct_events(groups.groups[0],{a.article_id:a for a in articles},{},Fake(),chunk_size=1)
        self.assertFalse(errors);self.assertFalse(failed)
        self.assertEqual(proposals[0].event.what_changed[0].evidence.quote,text)

    def test_authentic_facility_capital_and_eligibility_stay_distinct(self):
        from evaluation.real_corpus import load_sources,SMOKE_IDS
        originals=load_sources()
        issue_by_title={originals[k]['source']['title']:('facility' if k in (SMOKE_IDS[0],SMOKE_IDS[2]) else 'capital' if k==SMOKE_IDS[1] else 'eligibility') for k in SMOKE_IDS}
        def handler(stage,ctx):
            if stage=='construct':
                value=construction(ctx)
                for event in value['events']:
                    issue=issue_by_title[event['title']]
                    event['identity']={**IDENTITY,'occurrence':issue}
                    if event['title']==originals[SMOKE_IDS[0]]['source']['title']:
                        event['what_changed'][0]['claim_type']='announced'
                return value
            if stage=='group_identity':
                issue=ctx['event_proposal']['identity']['occurrence']
                match=next((e for e in ctx['candidates'] if e['identity']['occurrence']==issue),None)
                return dict(decision='same' if match else 'new',event_id=match['event_id'] if match else None,relation='material' if match else None,reason='Compare the real-world issue, not the shared PPP program.')
            if stage=='analyze':return analysis(ctx)
        articles=[raw(originals[k]['source']['body'],originals[k]['source']['url'],originals[k]['source']['title']) for k in SMOKE_IDS]
        result=self.run_news(Fake(handler),articles,chunk_size=max(len(a['text']) for a in articles))
        self.assertEqual(result.status,'completed',result.errors)
        self.assertEqual(len(result.events),3)
        by_issue={e.identity.occurrence:e for e in result.events}
        self.assertEqual(len(by_issue['facility'].source_ids),2)
        self.assertEqual({e.claim_type for e in by_issue['facility'].what_changed},{'actual','announced'})
        self.assertEqual(len(by_issue['capital'].source_ids),1)
        self.assertEqual(len(by_issue['eligibility'].source_ids),1)
        self.assertTrue(all(e.score is not None for e in result.events))

    def test_issue_level_prompt_contract_reaches_existing_calls(self):
        prompts={}
        class PromptFake(Fake):
            def complete(inner,schema,prompt,ctx,*,stage):
                prompts.setdefault(stage,[]).append(prompt)
                return super().complete(schema,prompt,ctx,stage=stage)
        result=self.run_news(PromptFake(),self.reports()[:2])
        self.assertEqual(result.status,'completed',result.errors)
        from news_engine.events import associate_event
        seed=result.events[0]
        candidates=[seed.model_copy(update={'event_id':f'E-candidate-{i}'}) for i in range(6)]
        def select(stage,ctx):
            if stage=='group_retrieve':return dict(event_ids=[ctx['candidates'][0]['event_id']])
        associate_event(seed,candidates,PromptFake(select))
        local=prompts['construct'][0];global_prompt=prompts['group_identity'][0]
        retrieval=prompts['group_retrieve'][0]
        self.assertIn('coherent, independently monitorable real-world issue',local)
        self.assertIn('mentally consolidate developments belonging to each issue',local)
        self.assertIn('Represent these as separate What Changed entries within one coherent Event.',local)
        self.assertIn('Prefer one Event with several source-grounded developments',local)
        self.assertIn('do not derive an Event-count\nlimit from the number of Articles',local)
        self.assertIn('Do not merge unrelated issues merely because they share a company',local)
        for example in ('PPPLF announcement + operational launch -> same',
                        'Capital neutrality + zero-percent risk weight under the same rule -> same',
                        'SBA clarification + related Board eligibility modification -> prefer same',
                        'PPPLF facility + separate capital-treatment regulation -> new'):
            self.assertIn(example,global_prompt)
        self.assertIn('Choose new when drafts clearly represent independently monitorable issues',global_prompt)
        self.assertIn('prefer merging related developments',local)
        self.assertIn('prefer same for the reasonably related issue',global_prompt)
        for prompt in (local,global_prompt,retrieval):
            normalized=' '.join(prompt.casefold().split())
            self.assertIn('actors',normalized);self.assertIn('claim types',normalized)
            self.assertIn('reporting periods',normalized)
        self.assertIn('prefer retaining',retrieval)
        for prompt in (local,retrieval):self.assertIn('not a requirement',prompt)
        for prompt in (local,global_prompt):
            self.assertIn('Claim Types, applicability conditions, dates, temporal qualifications',prompt)
            self.assertIn('supporting',prompt)

    def test_authentic_issue_replay_preserves_provisions_updates_and_distinct_rules(self):
        # Fixed replies test preservation/association contracts, not LLM prompt quality.
        from itertools import combinations
        from evaluation.real_corpus import load_sources,SMOKE_IDS
        originals=load_sources()
        sources={key:originals[key]['source'] for key in SMOKE_IDS}
        capital=sources[SMOKE_IDS[1]]['body'].split('\n\n')
        capital_parts=capital[1].split('. ',1)
        eligibility=sources[SMOKE_IDS[3]]['body'].split('\n\n')[2].split('. ',1)
        cases=[
            (SMOKE_IDS[0],'facility','announced',sources[SMOKE_IDS[0]]['body'],[]),
            (SMOKE_IDS[1],'capital','actual',capital_parts[0]+'.',[]),
            (SMOKE_IDS[1],'capital','actual',capital_parts[1],
             [dict(value='0',unit='percent',source_quote='zero percent')]),
            (SMOKE_IDS[1],'capital','actual',capital[2],
             [dict(value='30',unit='days',source_quote='30 days')]),
            (SMOKE_IDS[2],'facility','actual',sources[SMOKE_IDS[2]]['body'].split('\n\n')[0],[]),
            (SMOKE_IDS[3],'eligibility','actual',eligibility[0]+'.',[]),
            (SMOKE_IDS[3],'eligibility','announced',eligibility[1],[]),
        ]
        for key,issue,claim,text,numbers in cases:
            self.assertIn(text,sources[key]['body'])
            for number in numbers:self.assertIn(number['source_quote'],text)
        articles=[dict(url=a['url'],title=a['title'],text=a['body'],
                       publisher=a['source'],published_at=a['published_at']) for a in sources.values()]
        self.issue_metrics={}
        for fragmented in (False,True):
            local_counts=[]
            def handler(stage,ctx):
                if stage=='construct':
                    # Construction context exposes IDs/title/text, not source URLs.
                    by_title={a['title']:key for key,a in sources.items()}
                    supplied={by_title[a['title']]:a['article_id'] for a in ctx['sources']}
                    drafts={}
                    selected=[case for case in cases if case[0] in supplied]
                    if fragmented and SMOKE_IDS[0] in supplied:selected.append(cases[0])
                    for ordinal,(key,issue,claim,text,numbers) in enumerate(selected):
                        handle=str(ordinal) if fragmented else issue
                        if handle not in drafts:
                            drafts[handle]=dict(title=issue,category='regulatory',
                                identity=dict(subject=issue,action='development',occurrence=issue,scope='PPP'),
                                source_ids=[],what_changed=[])
                        draft=drafts[handle];source_id=supplied[key]
                        if source_id not in draft['source_ids']:draft['source_ids'].append(source_id)
                        draft['what_changed'].append(dict(text=text,claim_type=claim,
                            source_id=source_id,evidence_quote=text,numbers=numbers))
                    local_counts.append(len(drafts))
                    return dict(complete=True,events=list(drafts.values()))
                if stage=='group_identity':
                    issue=ctx['event_proposal']['identity']['occurrence']
                    match=next((e for e in ctx['candidates'] if e['identity']['occurrence']==issue),None)
                    return dict(decision='same' if match else 'new',event_id=match['event_id'] if match else None,
                                relation='material' if match else None,reason='Scripted independent issue reference.')
            with self.subTest(fragmented=fragmented),Store(Path(self.folder.name)/f'issues-{fragmented}.sqlite') as store:
                result=run_pipeline(store,Fake(handler),articles,reference_time=NOW)
                self.assertEqual(result.status,'completed',result.errors)
                self.assertEqual(len(result.events),3)
                self.assertEqual(result.metrics['consolidated_drafts'],3)
                self.assertEqual(len(result.sources),4)
                entries={entry.text:(event,entry) for event in result.events for entry in event.what_changed}
                self.assertEqual(sum(len(e.what_changed) for e in result.events),len(cases))
                self.assertEqual(set(entries),{case[3] for case in cases})
                article_by_id={a.article_id:a for a in result.sources}
                for key,issue,claim,text,numbers in cases:
                    event,entry=entries[text]
                    self.assertEqual(event.identity.occurrence,issue)
                    self.assertEqual(event.category,'regulatory')
                    self.assertEqual(entry.claim_type,claim)
                    self.assertEqual(article_by_id[entry.source_id].url,sources[key]['url'])
                    self.assertEqual(entry.evidence.quote,text)
                    self.assertEqual([n.model_dump() for n in entry.numbers],numbers)
                    self.assertIsNotNone(event.score)
                false_splits=false_merges=same_pairs=distinct_pairs=0
                for left,right in combinations(cases,2):
                    expected_same=left[1]==right[1]
                    observed_same=entries[left[3]][0].event_id==entries[right[3]][0].event_id
                    same_pairs+=expected_same;distinct_pairs+=not expected_same
                    false_splits+=expected_same and not observed_same
                    false_merges+=not expected_same and observed_same
                self.assertEqual((false_splits,false_merges),(0,0))
                self.issue_metrics['fragmented' if fragmented else 'issue_drafts']=dict(
                    local_drafts=sum(local_counts),events=len(result.events),false_splits=false_splits,
                    same_issue_pairs=same_pairs,false_merges=false_merges,distinct_issue_pairs=distinct_pairs,
                    retained_what_changed=len(entries),expected_what_changed=len(cases),
                    retained_numeric_observations=sum(len(entry.numbers) for event in result.events for entry in event.what_changed),
                    source_articles=len(result.sources),scripted_llm_calls=result.metrics['llm_calls'])

    def test_w2_authentic_sources_group_across_dates_without_a_time_cutoff(self):
        from evaluation.real_corpus import load_sources,SMOKE_IDS
        originals=load_sources()
        articles=[]
        for key in SMOKE_IDS:
            a=originals[key]['source']
            articles.append(self.store.add_article(ArticleInput(url=a['url'],title=a['title'],
                text=a['body'],publisher=a['source'],published_at=a['published_at'])))
        dates=[a.published_at for a in articles]
        self.assertGreater((max(dates)-min(dates)).days,2)
        grouped=group_articles(articles,[])
        undated=group_articles([a.model_copy(update={'published_at':None}) for a in articles],[])
        self.assertEqual(grouped.model_dump(),undated.model_dump())
        self.assertEqual(len(grouped.groups),1)
        self.assertEqual(set(grouped.groups[0].article_ids),{a.article_id for a in articles})
        self.w2_metrics=dict(article_groups=len(grouped.groups),source_articles=len(articles),
                             publication_span_days=(max(dates)-min(dates)).days)

    def test_authentic_facility_updates_preserve_identity_across_reporting_runs(self):
        # Scripted identity decisions verify cross-run mechanics, not semantic accuracy.
        from evaluation.real_corpus import load_sources,SMOKE_IDS
        originals=load_sources()
        keys=(SMOKE_IDS[0],SMOKE_IDS[2])
        inputs=[]
        for key in keys:
            a=originals[key]['source']
            inputs.append(dict(url=a['url'],title=a['title'],text=a['body'],
                               publisher=a['source'],published_at=a['published_at']))
        def handler(stage,ctx):
            if stage=='construct':
                value=construction(ctx)
                for event in value['events']:
                    announced=event['title']==inputs[0]['title']
                    event['identity']={**IDENTITY,'subject':'PPP liquidity facility',
                        'occurrence':'PPPLF issue','action':'establish' if announced else 'operate'}
                    event['what_changed'][0]['claim_type']='announced' if announced else 'actual'
                return value
            if stage=='group_identity':
                self.assertEqual(ctx['candidate_kind'],'historical_event')
                candidate=ctx['candidates'][0]
                self.assertNotEqual(ctx['event_proposal']['title'],candidate['title'])
                self.assertNotEqual(ctx['event_proposal']['identity']['action'],candidate['identity']['action'])
                return dict(decision='same',event_id=candidate['event_id'],relation='material',
                            reason='Scripted facility issue despite different actions, wording and publication dates.')
        first=self.run_news(Fake(handler),inputs[:1])
        self.assertEqual(first.status,'completed',first.errors)
        expected=first.events[0].event_id
        self.store.close();self.store=Store(self.path)
        second=self.run_news(Fake(handler),inputs[1:])
        self.assertEqual(second.status,'completed',second.errors)
        events=self.store.events();self.assertEqual(len(events),1)
        event=events[0];self.assertEqual(event.event_id,expected)
        self.assertEqual(len(event.what_changed),2)
        self.assertEqual({e.claim_type for e in event.what_changed},{'announced','actual'})
        self.assertEqual({e.evidence.quote for e in event.what_changed},{a['text'] for a in inputs})
        self.assertEqual(len(event.source_ids),2)
        self.assertEqual(len({a.published_at for a in self.store.articles()}),2)
        self.cross_run_metrics=dict(local_drafts=2,canonical_events_before=1,canonical_events_after=1,
            stable_event_ids=1,expected_stable_event_ids=1,retained_what_changed=2,expected_what_changed=2)
