"""Audit the frozen real-source corpus without a network or model call."""
import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import html
import json
from pathlib import Path
from urllib.parse import urlsplit


def validate_dataset(path):
    path=Path(path);dataset=json.loads(path.read_text())
    archive=json.loads((path.parent/dataset['source_archive']).read_text())['sources']
    if dataset['schema_version']!=4: raise ValueError('Expected real corpus schema 4')
    seen_urls=set();used=set();sequence_ids=set();events=set();rows=[]
    def require(ok,message):
        if not ok: raise ValueError(message)
    for seq in dataset['sequences']:
        require(seq['id'] not in sequence_ids,'Duplicate sequence ID');sequence_ids.add(seq['id'])
        prior=set();last=None
        for row in seq['articles']:
            source=row['source'];p=row['provenance'];gold=row['gold'];a=row['annotation']
            require(source['url'] not in seen_urls,'Duplicate source URL inflates sample count');seen_urls.add(source['url'])
            require(urlsplit(source['url']).scheme=='https','Missing HTTPS source URL')
            saved=archive[p['source_id']];used.add(p['source_id'])
            require(saved['url']==source['url'] and saved['title']==source['title'],'Source identity differs from archive')
            indices=p['archive_paragraphs']
            require(bool(indices) and indices==sorted(set(indices)) and all(0<=i<len(saved['paragraphs']) for i in indices),'Invalid archive paragraph range')
            body='\n\n'.join(saved['paragraphs'][i] for i in indices)
            require(body==source['body'],'Source excerpt differs from archived evidence')
            require(sha256(body.encode()).hexdigest()==p['body_sha256'],'Source body hash mismatch')
            require(sha256('\n\n'.join(saved['paragraphs']).encode()).hexdigest()==saved['text_sha256'],'Archive text hash mismatch')
            published=datetime.fromisoformat(source['published_at']);at=datetime.fromisoformat(row['discovered_at'])
            require(published.tzinfo is not None and at.tzinfo is not None,'Timezone is required')
            require(published.date().isoformat()==saved['published_date'],'Publication date differs from source')
            require(published<=at and (last is None or last<at),'Replay must be strictly chronological and after publication');last=at
            require(p['published_precision']=='date','Unexpected publication precision')
            require(gold['event'] in dataset['event_catalog'],'Unknown annotated occurrence')
            require(a['scope']==dataset['event_catalog'][gold['event']],'Occurrence scope mismatch')
            require((gold['decision']=='create')==(gold['event'] not in prior),'Create/attach label contradicts chronological membership')
            require(gold['lifecycle'] in {'announced','ongoing','resolved','unknown'},'Invalid lifecycle')
            require(gold['kind'] in {'new_development','pending_claim','corroboration','conflict','correction','resolution','no_change'},'Invalid development kind')
            require(gold['decision'] in {'create','attach','corroborate','conflict','defer'},'Invalid decision')
            require(gold['kind']!='resolution' or gold['lifecycle']=='resolved','Resolution requires resolved lifecycle')
            require(row['w2']['facts']==[body] and row['w2']['review_status']=='confirmed' and row['w2']['duplicate_index'] is None,'Frozen W2 must contain only the unaltered source passage and documented review status')
            require(a['annotator']=='OpenAI Codex assistant' and a['review_status']=='single_annotator_not_independently_adjudicated','Do not misrepresent annotation provenance')
            require(len(a['rationale'])>30 and a['state_confidence'] in {'high','medium'} and a['identity_confidence'] in {'high','medium'},'Missing annotation rationale/confidence')
            prior.add(gold['event']);events.add(gold['event']);rows.append(row)
    require(used==set(archive),'Archive and scored sources must match exactly')
    require(events==set(dataset['event_catalog']),'Unused event catalog entries')
    return {'dataset':dataset['name'],'sources':len(rows),'unique_urls':len(seen_urls),'events':len(events),
        'streams':{s['id']:len(s['articles']) for s in dataset['sequences']},
        'publishers':dict(Counter(r['source']['source'] for r in rows)),
        'lifecycles':dict(Counter(r['gold']['lifecycle'] for r in rows)),
        'decisions':dict(Counter(r['gold']['decision'] for r in rows)),
        'development_kinds':dict(Counter(r['gold']['kind'] for r in rows)),
        'state_confidence':dict(Counter(r['annotation']['state_confidence'] for r in rows)),
        'identity_confidence':dict(Counter(r['annotation']['identity_confidence'] for r in rows)),
        'dataset_sha256':sha256(json.dumps(dataset,sort_keys=True).encode()).hexdigest(),
        'source_archive_sha256':sha256((path.parent/dataset['source_archive']).read_bytes()).hexdigest()}


def render_annotations(path,output):
    data=json.loads(Path(path).read_text());coverage=validate_dataset(path)
    body='<h1>Workflow E: real-source annotation audit</h1><p>72 authentic official releases; selected passages; single AI annotator, no independent human adjudication.</p>'
    body+='<pre>'+html.escape(json.dumps(coverage,ensure_ascii=False,indent=2))+'</pre>'
    for seq in data['sequences']:
        body+='<h2>'+html.escape(seq['id'])+'</h2>'
        for row in seq['articles']:
            source=row['source']
            body+='<details><summary>'+html.escape(source['published_at'][:10]+' | '+source['title'])+'</summary>'
            body+='<p><a href="'+html.escape(source['url'],quote=True)+'">Original source</a></p><pre>'+html.escape(json.dumps({'gold':row['gold'],'annotation':row['annotation'],'provenance':row['provenance']},ensure_ascii=False,indent=2))+'</pre><blockquote>'+html.escape(source['body']).replace('\n','<br>')+'</blockquote></details>'
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+'\n')
    (output/'annotations.html').write_text('<!doctype html><meta charset="utf-8"><title>Real event annotation audit</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto}pre,blockquote{white-space:pre-wrap}details{margin:14px 0}</style>'+body)
    return coverage


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',type=Path,default=Path(__file__).resolve().parents[1]/'tests/fixtures/event_sequences_real_v1.json');parser.add_argument('--output',type=Path)
    args=parser.parse_args();print(json.dumps(render_annotations(args.dataset,args.output) if args.output else validate_dataset(args.dataset)))


if __name__ == "__main__":
    raise SystemExit(main())
