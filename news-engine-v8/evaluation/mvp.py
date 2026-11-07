"""Direct chronological MVP replay on unchanged authentic news; no labels in model inputs."""
import argparse
from datetime import datetime
from collections import defaultdict
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import time
from uuid import uuid4

from .real_corpus import DEFAULT_CORPUS, load_gold_events, load_scenario, validate_corpus


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def grouping_metrics(memberships, source_ids):
    """Missing predictions count against same-occurrence recall, never leave the denominator."""
    gold = load_gold_events()
    tp = fp = fn = tn = missing = 0
    for left, right in combinations(source_ids, 2):
        same = gold[left]['event'] == gold[right]['event']
        predicted = bool(memberships.get(left, set()) & memberships.get(right, set()))
        if not memberships.get(left) or not memberships.get(right):
            missing += 1
        tp += bool(same and predicted)
        fp += bool(not same and predicted)
        fn += bool(same and not predicted)
        tn += bool(not same and not predicted)
    return {'true_positive': tp, 'false_merge': fp, 'false_split_or_missing': fn,
            'true_negative': tn, 'pairs_with_missing_source': missing,
            'pair_precision': tp / (tp + fp) if tp + fp else None,
            'pair_recall': tp / (tp + fn) if tp + fn else None,
            'missing_source_ids': [s for s in source_ids if not memberships.get(s)]}


def main():
    from datetime import datetime
    parser=argparse.ArgumentParser(description='Replay unchanged authentic sources through Article-to-Event Classification')
    parser.add_argument('--selection',choices=('smoke','full'),default='smoke')
    parser.add_argument('--output',type=Path,default=Path('outputs/article-classification-v8-smoke'))
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--model');parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args();validation=validate_corpus();rows=load_scenario(args.selection)
    if args.validate_only:print(json.dumps(validation,indent=2));return 0
    if args.output.exists() and any(args.output.iterdir()):parser.error('Use fresh output directory')
    args.output.mkdir(parents=True,exist_ok=True)
    from news_engine.config import load_credentials
    from news_engine.clients import LLMClient
    from news_engine.models import ArticleInput, validate_assignments
    from news_engine.pipeline import run_pipeline
    from news_engine.storage import Store
    credentials=load_credentials(args.env_file,require_exa=False)
    llm=LLMClient(credentials.llm_api_key,args.model or credentials.llm_model,credentials.llm_base_url)
    report=dict(schema_version=8,source_count=len(rows),source_ids=[r['source_id'] for r in rows],model=llm.model,cohorts=[],status='running',
        limitations=['Selected authentic passages, not exhaustive full-article news.','Source-pair grouping is not semantic completeness.','Search skipped.','No independent scoring/summary gold.'])
    traces=args.output/'primary_replies';traces.mkdir();complete=llm.complete
    def trace(model,prompt,context,*,stage):
        value=dict(stage=stage,schema=model.__name__,context=context)
        try:
            result=complete(model,prompt,context,stage=stage);value['response']=result.model_dump(mode='json');return result
        except Exception as exc:value['error']=str(exc);raise
        finally:save(traces/(stage+'-'+uuid4().hex+'.json'),value)
    llm.complete=trace
    cohorts=defaultdict(list)
    for row in rows:cohorts[row['cohort']].append(row)
    memberships=defaultdict(set);source_by_url={r['source']['url']:r['source_id'] for r in rows}
    checked=invalid=0;started=time.monotonic()
    try:
        with Store(args.output/'news-mvp.sqlite') as store:
            for cohort,selected in cohorts.items():
                record=dict(cohort=cohort,source_ids=[r['source_id'] for r in selected]);report['cohorts'].append(record)
                try:
                    inputs=[ArticleInput(url=r['source']['url'],title=r['source']['title'],text=r['source']['body'],publisher=r['source']['source'],published_at=r['source']['published_at']) for r in selected]
                    result=run_pipeline(store,llm,articles=inputs,workers=args.workers,reference_time=datetime.fromisoformat(selected[0]['reference_time']))
                    payload=result.model_dump(mode='json');save(args.output/(cohort+'.json'),payload)
                    record.update(status=result.status,errors=payload['errors'],metrics=result.metrics)
                    articles={a.article_id:a for a in result.sources}
                    selected_urls={r['source']['url'] for r in selected}
                    relevant_ids={key for event in result.events for key in event.article_ids
                                  if key in articles and articles[key].url in selected_urls}
                    record['cardinality']=validate_assignments(result.events,relevant_ids)
                    for event in result.events:
                        for article_id in event.article_ids:
                            article=articles.get(article_id)
                            checked+=1
                            if article is None:invalid+=1
                            elif article.url in source_by_url:memberships[source_by_url[article.url]].add(event.event_id)
                except Exception as exc:record.update(status='failed',error=str(exc))
                save(args.output/'report.json',report)
    finally:
        receipts=llm.receipts;llm.close();save(args.output/'receipts.json',receipts)
    costs=[r.get('cost_usd') for r in receipts]
    report.update(status='completed' if all(c.get('status')=='completed' for c in report['cohorts']) else 'partial' if any(c.get('status') in ('completed','partial') for c in report['cohorts']) else 'failed',
        wall_seconds=time.monotonic()-started,llm_calls=len(receipts),input_tokens=sum(r.get('prompt_tokens',0) for r in receipts),output_tokens=sum(r.get('completion_tokens',0) for r in receipts),
        cost_usd=sum(costs) if costs and all(c is not None for c in costs) else None,known_cost_usd=sum(c or 0 for c in costs),
        grounding=dict(article_associations_checked=checked,invalid_article_associations=invalid,
            note='Original Article linkage only; semantic and numeric relationship support is not independently judged.'),
        cardinality=dict(unique_relevant_articles=sum(bool(memberships.get(key)) for key in report['source_ids']),
                         unique_events=len({event_id for ids in memberships.values() for event_id in ids})),
        grouping=grouping_metrics(memberships,report['source_ids']))
    report['cardinality']['valid']=report['cardinality']['unique_events']<=report['cardinality']['unique_relevant_articles']
    if not report['cardinality']['valid']:report['status']='failed'
    save(args.output/'report.json',report);print(json.dumps(report,indent=2))
    return 0 if report['status']=='completed' and not invalid else 1

if __name__=='__main__':raise SystemExit(main())
