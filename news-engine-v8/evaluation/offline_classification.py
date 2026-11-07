"""Fixed-label offline replay on unchanged authentic text; no provider calls.

Labels script model replies ONLY for contract regression, never semantic accuracy.
"""
import argparse
from collections import defaultdict
from pathlib import Path
import json
from tempfile import TemporaryDirectory
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from .real_corpus import load_sources, load_gold_events, validate_corpus
from .mvp import grouping_metrics


def analysis_reply(event):
    return dict(summary=event['summary'], why_it_matters=['If applicable, monitor financing and liquidity implications.'],
        what_to_do=['Verify exposure before changing Treasury plans.'], impact=2,relevance=2,urgency=2,
        reasoning=dict(impact='The reported issue may affect financing conditions.',
                       relevance='Company exposure remains unverified.', urgency='Monitor for applicable changes.'),
        impact_basis='benchmark_or_operating',relevance_basis='indirect_or_watch',
        urgency_basis='routine_or_unknown',has_treasury_action_or_constraint=False)


class ScriptedLabels:
    model='offline-fixed-labels-v8'
    def __init__(self, labels=None):
        self.labels=labels or {}; self.calls=[]; self.fail_stage=None; self.receipts=[]
    def complete(self, schema, prompt, context, *, stage):
        self.calls.append((stage,context))
        if self.fail_stage==stage: raise RuntimeError('Scripted technical failure')
        if stage=='classify':
            groups=defaultdict(list)
            for a in context['sources']:
                issue=self.labels.get(a['url'],a['title'].split(' | ')[0])
                groups[issue].append(a['article_id'])
            definitions=[]; assignments={}
            for index,(issue,ids) in enumerate(sorted(groups.items())):
                handle=f'P{index+1}'
                definitions.append(dict(issue_id=handle,title=issue,
                    summary='Reports concerning '+issue+'.',category='regulatory'))
                assignments.update({key:handle for key in ids})
            value=dict(complete=True,events=definitions,assignments=assignments)
        elif stage=='group_retrieve':
            value=dict(event_ids=[c['event_id'] for c in context['candidates']
                if c['title']==context['event_proposal']['title']][:5])
        elif stage in {'group_identity','group_reconsider'}:
            match=next((c for c in context['candidates'] if c['title']==context['event_proposal']['title']),None)
            value=dict(decision='same' if match else 'new',event_id=match['event_id'] if match else None,
                reason='Fixed issue labels for offline contract regression only.')
        elif stage=='analyze_batch':
            value=dict(results=[dict(event_id=c['current_event']['event_id'],analysis=analysis_reply(c['current_event']))
                for c in reversed(context['events'])])
        elif stage=='analyze': value=analysis_reply(context['current_event'])
        else: raise AssertionError(stage)
        return schema.model_validate(value)


def replay():
    assert validate_corpus()['status']=='passed'
    sources=load_sources();gold=load_gold_events()
    keys=[key for key in gold if key in sources]
    labels={sources[key]['source']['url']:gold[key]['event'] for key in keys}
    llm=ScriptedLabels(labels);memberships=defaultdict(set)
    with TemporaryDirectory() as folder, Store(Path(folder)/'v8.sqlite') as store:
        inputs=[dict(url=sources[key]['source']['url'],title=sources[key]['source']['title'],
            text=sources[key]['source']['body'],publisher=sources[key]['source']['source'],
            published_at=sources[key]['source']['published_at']) for key in keys]
        result=run_pipeline(store,llm,articles=inputs)
        by_url={a.url:a.article_id for a in result.sources}
        assignment=store.assignments()
        for key in keys:
            if sources[key]['source']['url'] in by_url:
                memberships[key].add(assignment[by_url[sources[key]['source']['url']]])
        before=assignment.copy();calls=len(llm.calls)
        restarted=run_pipeline(store,llm,articles=inputs)
        assert store.assignments()==before and len(llm.calls)==calls
        return dict(status=result.status,scope='Fixed gold-label replies; contract validation, NOT model semantic accuracy.',
            articles=len(result.sources),events=len(result.events),cardinality_passed=len(result.events)<=len(result.sources),
            historical_identity_stable=store.assignments()==before,unchanged_inputs_rescored=False,
            errors=[e.model_dump() for e in result.errors],grouping=grouping_metrics(memberships,keys),
            schema_version=result.schema_version)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,
        default=Path('outputs/contract8-offline/replay.json'));args=parser.parse_args()
    report=replay();args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,indent=2))
    return int(report['status']!='completed' or not report['cardinality_passed'])

if __name__=='__main__': raise SystemExit(main())
