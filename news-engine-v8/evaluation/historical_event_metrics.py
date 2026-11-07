"""Pure archived V1 report metrics; no retired workflow execution or model calls."""
from collections import Counter, defaultdict
from itertools import combinations
from datetime import datetime, timedelta, timezone
import html
import json
import os
import tempfile

def summarize(rows):
    successful=[r for r in rows if not r['errors']]
    retrieval=[x for r in successful for x in r['retrieval'] if x['expected_existing']]
    receipts=[c for r in rows for c in r['service_calls']]
    def metric(values): return {'numerator':sum(values),'denominator':len(values),'rate':sum(values)/len(values) if values else None}
    return {'steps':len(rows),'service_or_stage_failures':len(rows)-len(successful),
        'unique_source_urls':len({r['source']['source']['url'] for r in rows}),
        'unique_gold_events':len({(r.get('sequence'),r['source']['gold']['event']) for r in rows}),
        'association':metric([r['association_correct'] for r in rows]),
        'nonduplicate_association':metric([r['association_correct'] for r in rows if r['source']['gold']['decision']!='duplicate']),
        'new_event_association':metric([r['association_correct'] for r in rows if r['source']['gold']['decision']=='create']),
        'existing_event_association':metric([r['association_correct'] for r in rows if r['source']['gold']['decision'] not in {'create','duplicate'}]),
        'duplicate_inheritance':metric([r['association_correct'] for r in rows if r['source']['gold']['decision']=='duplicate']),
        'false_merges':sum(r.get('false_merge',False) for r in rows),
        'false_splits':sum(r.get('false_split',False) for r in rows),
        'state':metric([r['state_correct'] for r in rows]),
        'high_confidence_state':metric([r['state_correct'] for r in rows if r['source'].get('annotation',{}).get('state_confidence')=='high']),
        'decision':metric([r['decision_correct'] for r in rows if 'decision_correct' in r]),
        'development':metric([r['development_correct'] for r in rows]),
        'baseline_development':metric([r['baseline_development_correct'] for r in rows if r.get('baseline_development_correct') is not None]),
        'deferred_steps':sum(any(d.get('decision')=='defer' for d in r.get('predicted_decisions',[])) for r in rows),
        'retrieval_recall_at_5':metric([x['hit'] for x in retrieval]),
        'replay_extra_calls':sum(r['replay_calls'] for r in rows),
        'model_calls_recorded':len(receipts),'provider_calls':sum(c.get('status')!='mock' for c in receipts),
        'cost_usd_reported':sum(c.get('cost_usd',0) or 0 for c in receipts) if any(c.get('cost_usd') is not None for c in receipts) else None,
        'finish_reasons':dict(Counter(c.get('finish_reason','unreported') for c in receipts)),
        'completion_limit_hits':sum(c.get('finish_reason')=='length' for c in receipts),
        'explicit_output_cap_calls':sum(c.get('output_token_limit') is not None for c in receipts),
        'max_prompt_tokens_reported':max((c.get('prompt_tokens',0) for c in receipts),default=0),
        'max_completion_tokens_reported':max((c.get('completion_tokens',0) for c in receipts),default=0),
        'cost_receipt_count':sum(c.get('cost_usd') is not None for c in receipts)}

def pairwise_membership(rows):
    """Compare article pairs only inside the same sequence/database replicate.

    Missing extractions remain false negatives for same-gold-event pairs. No
    credit is awarded merely for the many unrelated pairs with no shared ID.
    """
    groups=defaultdict(list)
    for row in rows: groups[(row['sequence'],row['replicate'])].append(row)
    tp=fp=fn=0
    for group in groups.values():
        for a,b in combinations(group,2):
            expected=a['source']['gold']['event']==b['source']['gold']['event']
            linked=bool(set(a['predicted_ids']) & set(b['predicted_ids']))
            tp+=expected and linked;fp+=not expected and linked;fn+=expected and not linked
    return {'true_positive':tp,'false_positive':fp,'false_negative':fn,
        'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
        'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}

def finalize_report(report):
    report['summary']=summarize(report['rows'])
    report['pairwise_membership']=pairwise_membership(report['rows'])
    report['by_sequence']={seq:summarize([r for r in report['rows'] if r['sequence']==seq])
        for seq in sorted({r['sequence'] for r in report['rows']})}
    expected=report.get('expected_steps')
    report['complete']=expected is not None and len(report['rows'])==expected

def render(report,out):
    summary=report['summary']
    body='<h1>Workflow E evaluation</h1><p>'+html.escape(report['mode'])+'</p><pre>'+html.escape(json.dumps(summary,indent=2))+'</pre>'
    body+='<p>'+html.escape(' '.join(report['limitations']))+'</p>'
    body+='<h2>Coverage and annotation</h2><pre>'+html.escape(json.dumps({
        'complete':report.get('complete'),'expected_steps':report.get('expected_steps'),
        'dataset':report.get('dataset'),'pairwise_membership':report.get('pairwise_membership'),
        'by_sequence':report.get('by_sequence')},ensure_ascii=False,indent=2))+'</pre>'
    # Keep full failed evidence inspectable without any script or remote resources.
    for row in report['rows']:
        body+='<details><summary>'+html.escape(f"{row['sequence']} / {row['index']} / replicate {row['replicate']}")+'</summary><pre>'+html.escape(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></details>'
    try:
        os.environ.setdefault('MPLCONFIGDIR', os.path.join(tempfile.gettempdir(), 'treasury-matplotlib'))
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        keys=['association','state','development','retrieval_recall_at_5'];fig,axes=plt.subplots(1,2,figsize=(13,4))
        ax=axes[0];bars=ax.bar(keys,[(summary[k]['rate'] or 0)*100 for k in keys]);ax.set_ylim(0,115);ax.set_ylabel('Percent');ax.tick_params(axis='x',labelrotation=18)
        for bar,key in zip(bars,keys):
            m=summary[key];ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+2,f"{m['numerator']}/{m['denominator']}",ha='center')
        for offset,key in enumerate(keys[:3]):
            groups=report.get('by_sequence',{})
            axes[1].bar([i+offset*.25 for i in range(len(groups))],[(s[key]['rate'] or 0)*100 for s in groups.values()],width=.25,label=key)
        axes[1].set_xticks([i+.25 for i in range(len(report.get('by_sequence',{})))],list(report.get('by_sequence',{})));axes[1].set_ylim(0,115);axes[1].legend()
        fig.suptitle(f"{summary['unique_source_urls']} unique sources; {summary['steps']} evaluated steps; {summary['service_or_stage_failures']} stage failures")
        fig.tight_layout()
        for suffix in ('png','svg'):fig.savefig(out/('event-quality.'+suffix))
        plt.close(fig)
        body+=(out/'event-quality.svg').read_text()
    except ImportError:
        body+='<p>Install matplotlib to render PNG/SVG charts.</p>'
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>Workflow E evaluation</title>'+body)
