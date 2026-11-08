"""One five-stage execution path for CLI and HTTP."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
from time import perf_counter
from uuid import uuid4
from datetime import datetime, timezone

from .models import ArticleInput, Event, Error, RunResult, event_rank_key, validate_assignments
from .events import retrieve_candidates,associate_event,AssociationDraft,RetrievalDraft,consolidate_drafts,merge_event_content
from .analyze import analyze_batch,analysis_batches, ANALYSIS_PROMPT, AnalysisDraft,BatchAnalysisDraft
from .search import search
from .storage import BusyError
from .group_first import group_articles, classify_articles, ArticleClassificationDraft

_LOCK = Lock()


def run_pipeline(store, llm, articles=None, exa=None, workers=4, max_queries=7, chunk_size=None, geographic_scopes=None, reference_time=None, analysis_batch_size=3, analysis_workers=2, max_articles=None):
    # Retired chunk_size is ignored: input context has no application length cap.
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('Invalid workers')
    if type(analysis_batch_size) is not int or not 1<=analysis_batch_size<=4 or type(analysis_workers) is not int or not 1<=analysis_workers<=8:
        raise ValueError('Analysis batch size must be1..4 and workers1..8')
    if max_articles is not None and (type(max_articles) is not int or max_articles < 1):
        raise ValueError('max_articles must be a positive integer or None')
    if not _LOCK.acquire(blocking=False):
        raise BusyError('News pipeline is busy')
    try:
        with store.writer_lock():
            try:
                return _run(store,llm,articles,exa,workers,max_queries,geographic_scopes,reference_time,analysis_batch_size,analysis_workers,max_articles)
            except Exception as exc:
                row = store.db.execute("SELECT run_id FROM runs WHERE status='running' ORDER BY rowid DESC LIMIT 1").fetchone()
                if row is None:
                    raise
                stage = "run"
                try:
                    progress = json.loads(Path(str(store.path) + ".progress.json").read_text(encoding="utf-8"))
                    if progress.get("run_id") == row[0]:
                        stage = progress.get("stage", stage)
                except (OSError, ValueError):
                    pass
                # Do not leak provider response bodies/credentials or discard resumable work.
                result = RunResult(run_id=row[0], status="failed", errors=[Error(
                    stage=stage, code=type(exc).__name__,
                    message=f"News collection failed during {stage} ({type(exc).__name__}). Saved Articles and pending work are preserved. Check provider availability and configuration before an explicit refresh; no automatic retry.")])
                store.save_run(result)
                return result
    finally:
        _LOCK.release()


def _run(store,llm,articles,exa,workers,max_queries,geographic_scopes,reference_time,analysis_batch_size,analysis_workers,max_articles):
    started = perf_counter()
    receipts_start = len(getattr(llm,'receipts',[]))
    validation_start = len(getattr(llm,'validation_errors',[]))
    search_receipts_start = len(getattr(exa,'receipts',[]))
    run_id = 'R' + uuid4().hex
    observed_at = reference_time or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError('reference_time must be timezone-aware')
    policy_code=''.join(Path(__file__).with_name(name).read_text(encoding='utf-8-sig') for name in ('models.py','events.py','analyze.py','group_first.py','clients.py','id_constraints.py'))
    fingerprint = sha256((policy_code+ANALYSIS_PROMPT+getattr(llm,'model','recorded')+json.dumps([m.model_json_schema() for m in (ArticleInput,ArticleClassificationDraft,AssociationDraft,RetrievalDraft,AnalysisDraft,BatchAnalysisDraft)],sort_keys=True)).encode()).hexdigest()
    store.ensure_policy(fingerprint)
    store.start_run(run_id)
    store.progress(run_id, "search")
    errors = []
    warnings = []
    def record_classification_details(details):
        for detail in details:
            if detail.stage == 'classify' and detail.code == 'unused_issue_drafts_removed':
                warnings.append(detail)
            else:
                errors.append(detail)
    coverage=[]
    metrics = dict(search_coverage=coverage,articles_attempted=0,articles_cached=0,articles_successful=0,articles_failed=0,classification_workers=workers,consolidation_workers=workers,association_workers=workers)
    if articles is None:
        if exa is None:
            articles=[]
            errors.append(Error(stage='search',code='provider_missing',message='Exa is required for search'))
        else:
            articles, errors = search(llm,exa,store,max_queries=max_queries,geographic_scopes=geographic_scopes,coverage=coverage,now=observed_at)
    event_map={event.event_id:event for event in store.events()}
    supplied_ids = set()
    metrics['input_records_received'] = len(articles)
    metrics['input_records_invalid'] = 0
    for index, raw in enumerate(articles):
        try:
            article=store.add_article(ArticleInput.model_validate(raw))
            supplied_ids.add(article.article_id)
        except Exception as exc:
            metrics['input_records_invalid'] += 1
            errors.append(Error(stage='input',item_id=str(index),code=type(exc).__name__,message='Invalid article input'))
    article_map={article.article_id:article for article in store.articles()}
    receipts = store.pending_proposals()
    # Resume indivisible saved work first. Deferred members may not sneak back in
    # through the new-input/cache path and exceed the caller's Article budget.
    units = [('proposal', receipt.proposal_key, set(receipt.event.article_ids)) for receipt in receipts]
    units.extend(('event', event.event_id, set(event.article_ids))
                 for event in event_map.values() if event.score is None)
    protected = set().union(*(ids for _, _, ids in units)) if units else set()
    unfinished = {key for key in article_map
                  if store.classification_status(key) not in {'accepted','irrelevant'}}
    work_ids = protected | unfinished
    units.extend(('article', key, {key}) for key in article_map if key in unfinished-protected)
    units.extend(('cached', key, {key}) for key in article_map if key in supplied_ids-work_ids)
    selected = {}
    blocked = []
    for kind, key, ids in units:
        additional = ids - selected.keys()
        if max_articles is not None and len(selected) + len(additional) > max_articles:
            if kind in {'proposal', 'event'} and len(ids) > max_articles:
                blocked.append({'kind':kind,'id':key,'article_count':len(ids),'article_ids':sorted(ids)})
            continue
        selected.update((article_id, article_map[article_id]) for article_id in sorted(ids))
    metrics.update(max_articles=max_articles, articles_selected=len(selected),
                   articles_attempted=len(selected), articles_deferred=len(work_ids-selected.keys()),
                   budget_blocked_units=blocked)
    pending=[]
    for article in selected.values():
        if store.classification_status(article.article_id) in {'accepted','irrelevant'}:
            record_classification_details(store.classification_errors(article.article_id))
            metrics['articles_cached'] += 1
            metrics['articles_successful'] += 1
        else:
            pending.append(article)
    grouping = group_articles(pending, list(event_map.values()))
    metrics['article_groups'] = len(grouping.groups)
    pending_map = {a.article_id:a for a in pending}
    def classify(group):
        try:
            return group, classify_articles(group,pending_map,event_map,llm), None
        except Exception as exc:
            return group, None, exc
    store.progress(run_id, "classification", 0, len(grouping.groups))
    classified_groups = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(classify, group): group for group in grouping.groups}
        for future in as_completed(futures):
            group, result, exc = future.result()
            classified_groups += 1
            store.progress(run_id, "classification", classified_groups, len(grouping.groups))
            if exc is not None:
                for source_id in group.article_ids:store.fail_article(source_id)
                metrics['articles_failed'] += len(group.article_ids)
                errors.append(Error(stage='classify',item_id=group.group_key,code=type(exc).__name__,
                    message='Group Article classification failed; sources retained: '+str(exc) if isinstance(exc,ValueError)
                            else 'Group Article classification failed; sources retained for retry'))
                continue
            accepted,details,failed=result
            record_classification_details(details)
            try:
                store.save_group_proposals(group.article_ids,accepted,details,failed,irrelevant=set(group.article_ids)-failed-{key for p in accepted for key in p.event.article_ids})
            except Exception as exc:
                for source_id in group.article_ids:store.fail_article(source_id)
                metrics['articles_failed']+=len(group.article_ids)
                errors.append(Error(stage='classify',item_id=group.group_key,code=type(exc).__name__,
                                    message='Group draft storage failed; sources retained: '+str(exc) if isinstance(exc,ValueError)
                                    else 'Group draft storage failed; sources retained for retry'))
                continue
            metrics['articles_failed']+=len(failed)
            metrics['articles_successful']+=len(group.article_ids)-len(failed)
    store.progress(run_id, "consolidation")
    eligible_proposals = [receipt for receipt in store.pending_proposals()
                          if set(receipt.event.article_ids) <= selected.keys()]
    article_map={article.article_id:article for article in store.articles()}
    consolidated,details=consolidate_drafts(eligible_proposals,llm,workers=workers,
        articles=article_map,
        progress=lambda done,total:store.progress(run_id,"consolidation",done,total))
    errors.extend(details)
    metrics['consolidated_drafts']=len(consolidated)
    touched=set()
    article_map={article.article_id:article for article in store.articles()}
    for event in event_map.values():
        if set(event.article_ids) & selected.keys():
            touched.add(event.event_id)
    store.progress(run_id, "association", 0, len(consolidated))
    history_snapshot = tuple(event.model_copy(deep=True) for event in event_map.values())
    def match_history(item):
        proposal_keys, proposal = item
        try:
            return associate_event(proposal, retrieve_candidates(proposal, history_snapshot), llm, articles=article_map), None
        except Exception as exc:
            return None, exc
    # Workers only make decisions; all persistent merges use the latest caller-owned state.
    matches = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(match_history,item): index for index,item in enumerate(consolidated)}
        for future in as_completed(futures):
            matches.append((futures[future], future.result()))
            store.progress(run_id, "association", len(matches), len(consolidated))
    for index, (decision, failure) in sorted(matches):
        proposal_keys, proposal = consolidated[index]
        try:
            if failure is not None:
                raise failure
            existing=event_map.get(decision.event_id)
            if decision.decision == 'same' and existing is None:
                from .id_constraints import IDConstraintError
                raise IDConstraintError('Historical target is absent at commit', foreign_ids=[decision.event_id])
            if existing is None:
                event_id='E'+sha256(proposal_keys[0].encode()).hexdigest()[:20]
                event=Event(event_id=event_id,title=proposal.title,summary=proposal.summary,
                    category=proposal.category,article_ids=proposal.article_ids)
            else:
                event,added=merge_event_content(existing,proposal)
                if added:
                    event.score=None;event.why_it_matters=[];event.what_to_do=[]
            store.save_association(proposal_keys,event)
            event_map[event.event_id]=event;touched.add(event.event_id)
        except Exception as exc:
            errors.append(Error(stage='group',item_id=proposal_keys[0],code=type(exc).__name__,
                message='Event association failed; draft retained: '+str(exc) if isinstance(exc,ValueError) else 'Event association failed; draft retained'))
    unscored=[event for event in event_map.values() if event.score is None and event.event_id in touched]
    touched.update(event.event_id for event in unscored)
    metrics['analysis_context_articles'] = len({key for event in unscored for key in event.article_ids})
    batches,details=analysis_batches(unscored,article_map,observed_at.isoformat(),analysis_batch_size)
    errors.extend(details)
    metrics.update(analysis_batches=len(batches),analysis_batch_size=analysis_batch_size,analysis_workers=analysis_workers)
    store.progress(run_id, "analysis", 0, len(batches))
    completed_batches = 0
    with ThreadPoolExecutor(max_workers=analysis_workers) as pool:
        futures={pool.submit(analyze_batch,members,llm,context):members for members,context in batches}
        for future in as_completed(futures):
            completed_batches += 1
            store.progress(run_id, "analysis", completed_batches, len(batches))
            try:
                accepted,details=future.result()
                errors.extend(details)
            except Exception as exc:
                errors.extend(Error(stage='analyze',item_id=e.event_id,code=type(exc).__name__,
                    message='Analysis batch failed; Event retained unscored') for e in futures[future])
                continue
            for event in accepted.values():
                try:
                    store.save_event(event)
                    event_map[event.event_id]=event
                except Exception as exc:
                    errors.append(Error(stage='analyze',item_id=event.event_id,code=type(exc).__name__,
                                        message='Analysis storage failed; Event retained unscored: '+str(exc) if isinstance(exc,ValueError)
                                        else 'Analysis storage failed; Event retained unscored'))
    remaining_ids = {key for key in article_map
                     if store.classification_status(key) not in {'accepted','irrelevant'}}
    remaining_proposals = store.pending_proposals()
    remaining_ids.update(key for receipt in remaining_proposals for key in receipt.event.article_ids)
    remaining_ids.update(key for event in event_map.values() if event.score is None for key in event.article_ids)
    metrics['articles_deferred'] = len(remaining_ids-selected.keys())
    remaining_keys = {receipt.proposal_key for receipt in remaining_proposals}
    metrics['budget_blocked_units'] = [unit for unit in blocked
        if (unit['kind']=='proposal' and unit['id'] in remaining_keys)
        or (unit['kind']=='event' and event_map[unit['id']].score is None)]
    output=[]
    cited=set()
    for event_id in sorted(touched):
        event=event_map[event_id].model_copy(deep=True)
        cited.update(event.article_ids)
        output.append(event)
    output.sort(key=event_rank_key)
    errors=list({error.model_dump_json():error for error in errors}.values())
    relevant={key for key in selected if store.classification_status(key)=='accepted'}
    counts=validate_assignments(output,relevant,require_complete=not errors)
    metrics.update(counts)
    metrics['evaluated_article_ids']=sorted(relevant)
    metrics['classified_relevant_articles']=len(relevant)
    metrics['articles_awaiting_identity']=len(relevant-store.assignments().keys())
    metrics['evaluated_event_count']=counts['unique_events']
    metrics['irrelevant_articles']=sum(store.classification_status(key)=='irrelevant' for key in selected)
    receipts=getattr(llm,'receipts',[])[receipts_start:]
    search_receipts=getattr(exa,'receipts',[])[search_receipts_start:]
    metrics['id_validation_errors'] = ([r['validation_error'] for r in receipts if r.get('validation_error')]
        + getattr(llm,'validation_errors',[])[validation_start:])
    metrics['search_known_cost_usd'] = sum(r.get('cost_usd') or 0 for r in search_receipts)
    metrics['search_cost_usd'] = sum(r['cost_usd'] for r in search_receipts) if all(r.get('cost_usd') is not None for r in search_receipts) else None
    costs=[r.get('cost_usd') for r in receipts+search_receipts]
    stages={}
    w3={}
    for receipt in receipts:
        stage=receipt.get('stage','unknown')
        stages[stage]=stages.get(stage,0)+1
        if stage.startswith('group_') or stage=='classify':
            substep=w3.setdefault(stage,dict(calls=0,input_tokens=0,output_tokens=0,wall_seconds=0.0,known_cost_usd=0.0,cost_usd=0.0))
            substep['calls']+=1
            substep['input_tokens']+=receipt.get('prompt_tokens',0) or 0
            substep['output_tokens']+=receipt.get('completion_tokens',0) or 0
            substep['wall_seconds']+=receipt.get('latency_seconds',0) or 0
            cost=receipt.get('cost_usd')
            substep['known_cost_usd']+=cost or 0
            substep['cost_usd']=None if cost is None or substep['cost_usd'] is None else substep['cost_usd']+cost
    wall=perf_counter()-started
    metrics.update(w3=w3,wall_seconds=wall,llm_calls=len(receipts),llm_calls_by_stage=stages,input_tokens=sum(r.get('prompt_tokens',0) or 0 for r in receipts),output_tokens=sum(r.get('completion_tokens',0) or 0 for r in receipts),known_cost_usd=sum(c for c in costs if c is not None),cost_usd=sum(costs) if all(c is not None for c in costs) else None,cost_coverage=sum(c is not None for c in costs)/len(costs) if costs else 1,search_calls=len(search_receipts),articles_per_second=metrics['articles_successful']/wall)
    metrics['warnings'] = list({warning.model_dump_json():warning.model_dump(mode='json') for warning in warnings}.values())
    status='completed' if not errors else 'partial' if metrics['articles_successful'] or output else 'failed'
    themes=', '.join(sorted({e.category for e in output}))
    priorities='; '.join(f'{e.title} (R5 {e.score.total if e.score else "unscored"})' for e in output[:3])
    summary=(f'Monitoring themes: {themes}. Highest priorities: {priorities}.\n' if output else 'No classified Treasury news Events in this run.')
    summary+='\n'.join(f'{e.title}: {e.summary} '+' '.join(e.why_it_matters+e.what_to_do) for e in output)
    result=RunResult(overall_summary=summary,run_id=run_id,status=status,events=output,sources=[article_map[k] for k in sorted(cited)],errors=errors,metrics=metrics)
    store.save_run(result)
    store.progress(run_id, status, 1, 1)
    return result


def main():
    from .storage import Store
    from .clients import LLMClient, ExaClient
    from .config import load_credentials
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--database',type=Path,default=Path('data/news-mvp-v8-issue-identity-v2.sqlite'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--max-queries',type=int,default=7)
    parser.add_argument('--max-articles',type=int,default=30,help='Maximum new/resumed Articles per run (default: 30)')
    parser.add_argument('--geography',action='append')
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--analysis-batch-size',type=int,choices=range(1,5),default=3)
    parser.add_argument('--analysis-workers',type=int,choices=range(1,9),default=2)
    args=parser.parse_args()
    credentials=load_credentials(require_exa=args.input is None)
    llm=LLMClient(credentials.llm_api_key,credentials.llm_model,credentials.llm_base_url)
    exa=ExaClient(credentials.exa_api_key) if args.input is None else None
    try:
        articles=json.loads(args.input.read_text(encoding='utf-8-sig')) if args.input else None
        with Store(args.database) as store:
            result=run_pipeline(store,llm,articles,exa,workers=args.workers,max_queries=args.max_queries,geographic_scopes=args.geography,
                                analysis_batch_size=args.analysis_batch_size,analysis_workers=args.analysis_workers,max_articles=args.max_articles)
        text=result.model_dump_json(indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(text,encoding='utf-8')
        else:
            print(text)
        return {'completed':0,'partial':2,'failed':1}[result.status]
    finally:
        llm.close()
        if exa: exa.close()

if __name__=='__main__':
    raise SystemExit(main())
