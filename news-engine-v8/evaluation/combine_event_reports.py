"""Combine disjoint complete stream reports, preserving failures and provenance."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from .historical_event_metrics import finalize_report,render


def combine(reports,dataset):
    if not reports: raise ValueError('No reports')
    first=reports[0]
    common={k:v for k,v in first['manifest'].items() if k!='sequence'}
    if common['dataset_sha256']!=sha256(json.dumps(dataset,sort_keys=True).encode()).hexdigest():
        raise ValueError('Dataset hash does not match reports')
    keys=set();rows=[]
    for report in reports:
        if {k:v for k,v in report['manifest'].items() if k!='sequence'}!=common:
            raise ValueError('Cannot combine different model/config/source/dataset manifests')
        if not report.get('complete'): raise ValueError('Incomplete stream report')
        for row in report['rows']:
            key=(row['sequence'],row['replicate'],row['index'])
            if key in keys: raise ValueError('Overlapping report rows would inflate sample count')
            keys.add(key);rows.append(row)
    expected={(s['id'],repeat,i) for s in dataset['sequences'] for repeat in range(common['replicates']) for i in range(len(s['articles']))}
    if keys!=expected: raise ValueError('Reports do not cover exactly the selected dataset')
    # Ensure archived report source/gold rows have not been modified after scoring.
    source_rows={(s['id'],i):r for s in dataset['sequences'] for i,r in enumerate(s['articles'])}
    if any(row['source']!=source_rows[(row['sequence'],row['index'])] for row in rows):
        raise ValueError('Report rows differ from the frozen dataset')
    result={**first,'manifest':{**common,'sequence':None},'rows':sorted(rows,key=lambda r:(r['sequence'],r['replicate'],r['index'])),'expected_steps':len(expected),
        'component_sequences':[r['manifest']['sequence'] for r in reports]}
    finalize_report(result)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('reports',nargs='+',type=Path);parser.add_argument('--dataset',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists() and list(args.output.iterdir()): parser.error('Output must be empty; preserve older reports')
    result=combine([json.loads(p.read_text()) for p in args.reports],json.loads(args.dataset.read_text()))
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');render(result,args.output)
    print(json.dumps(result['summary']))


if __name__ == "__main__":
    raise SystemExit(main())
