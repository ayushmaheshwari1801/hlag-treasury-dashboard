"""Generate portable HTML + publication-ready PNG/SVG from a frozen JSON report."""
import argparse
from collections import Counter
import html
import json
import os
from pathlib import Path
import statistics
import tempfile

os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'treasury-matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render(path):
    report=json.loads(path.read_text());out=path.parent
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    images=[]
    def save(fig,name):
        fig.tight_layout()
        fig.savefig(out/(name+'.png'),dpi=180,bbox_inches='tight')
        fig.savefig(out/(name+'.svg'),bbox_inches='tight')
        plt.close(fig);images.append(name)
    groups=report['groups'];names=list(groups)
    safety=[100*groups[n]['safe']/groups[n]['completed'] if groups[n]['completed'] else 0 for n in names]
    fig,ax=plt.subplots(figsize=(11,8));bars=ax.barh(names[::-1],safety[::-1],color='#2563eb')
    ax.set(xlim=(0,112),xlabel='Safety checks passed (%)',title='Safety by template family — completed requests')
    for bar,name in zip(bars,names[::-1]):
        g=groups[name];ax.text(bar.get_width()+1,bar.get_y()+bar.get_height()/2,f"{g['safe']}/{g['completed']}",va='center',fontsize=8)
    save(fig,'safety-by-family')
    eligible=[n for n in names if groups[n]['retention_total']]
    fig,ax=plt.subplots(figsize=(11,6));values=[100*groups[n]['retained']/groups[n]['retention_total'] for n in eligible]
    bars=ax.barh(eligible[::-1],values[::-1],color='#0d9488');ax.set(xlim=(0,112),xlabel='Required observations / changes retained (%)',title='Information retention by template family')
    for bar,name in zip(bars,eligible[::-1]):
        g=groups[name];ax.text(bar.get_width()+1,bar.get_y()+bar.get_height()/2,f"{g['retained']}/{g['retention_total']}",va='center',fontsize=8)
    save(fig,'retention-by-family')
    counts=Counter()
    for row in report['cases']:
        if row['status']=='service_failure': counts['Service failure']+=1
        elif not row.get('safe'): counts['Safety violation']+=1
        elif row.get('retained') is False: counts['Missing required information']+=1
        elif not row.get('replay_ok',True): counts['Replay failure']+=1
        else: counts['Passed contract checks']+=1
    if report['planned']>report['completed']: counts['Not completed']=report['planned']-report['completed']
    fig,ax=plt.subplots(figsize=(10,4));bars=ax.barh(list(counts),list(counts.values()),color='#6366f1')
    ax.set(xlabel='Case-replicate count',title='Run outcomes (mutually exclusive)')
    for bar in bars: ax.text(bar.get_width()+.3,bar.get_y()+bar.get_height()/2,str(int(bar.get_width())),va='center')
    save(fig,'outcomes')
    latencies=[r['latency_seconds'] for r in report['cases'] if 'latency_seconds' in r]
    fig,ax=plt.subplots(figsize=(10,4))
    if latencies: ax.hist(latencies,bins=min(20,max(1,len(latencies))),color='#0891b2',edgecolor='white')
    ax.set(xlabel='Seconds per case (includes normalization / replay)',ylabel='Count',title='Observed latency distribution')
    save(fig,'latency')
    pair=report['repeat_consistency'];fig,ax=plt.subplots(figsize=(8,3))
    ax.barh(['Consistent fields'],[pair['consistent']],color='#0d9488')
    ax.set(xlim=(0,max(1,pair['evaluated'])),xlabel='Cases with all replicates completed successfully',title=f"Repeat field consistency: {pair['consistent']}/{pair['evaluated']}")
    save(fig,'repeat-consistency')
    def esc(x): return html.escape(str(x))
    def ratio(n,d): return f'{n}/{d} ({100*n/d:.1f}%)' if d else 'Not evaluated'
    facts=[('Status',report['status']),('Completed / planned',f"{report['completed']}/{report['planned']}"),
        ('Contract pass',ratio(report['passed'],report['completed'])),('Safety',ratio(report['safe'],report['safety_denominator'])),
        ('Information retention',ratio(report['retained'],report['retention_denominator'])),('Service failures',report['service_failures']),
        ('Safe/unsafe abstentions (all empty outputs)',report['abstentions']),('Calls / provider attempts',f"{report['calls']}/{report['attempts']}"),
        ('Provider-reported cost (USD)',report['reported_cost_usd']),('Model',report['manifest']['model']),
        ('Dataset',report['manifest']['dataset']),('Scoring version',report['manifest']['scoring_version']),
        ('Frozen at',report['manifest']['frozen_at']),('Commit',report['manifest']['git_commit'])]
    failures=[]
    for row in report['cases']:
        if row['status']=='passed':continue
        codes=[x['code'] for x in row.get('unsafe',[])+row.get('missing',[])]+([row['error']] if row.get('error') else [])
        failures.append(f"<tr><td>{esc(row['case_id'])}</td><td>{row['replicate']}</td><td>{esc(', '.join(codes) or row['status'])}</td></tr>")
    title='Real API robustness evaluation'
    document=f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>{title}</title>
<style>body{{max-width:1100px;margin:35px auto;padding:0 20px;font:16px/1.5 system-ui;color:#172033}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}figure{{margin:35px 0}}svg{{width:100%;height:auto}}.note{{background:#eef2ff;padding:18px}}code{{word-break:break-all}}</style>
<h1>{title}</h1><p class="note">Real model responses on synthetic development templates. Safety and retention are separate; repeated variants are correlated. These rates are not independent real-news accuracy estimates.</p>
<table>'''+''.join(f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>' for k,v in facts)+'</table>'
    document+='<p><a href="report.json">Full report JSON</a> · <a href="manifest.json">Frozen manifest</a></p>'
    for name in images:
        document+='<figure>'+ (lambda text: text[text.index('<svg'):])((out/(name+'.svg')).read_text()) + f'<figcaption><a href="{name}.png">PNG</a> · <a href="{name}.svg">SVG</a></figcaption></figure>'
    document+='<h2>Failures and missing information</h2><table><tr><th>Case</th><th>Replicate</th><th>Codes</th></tr>'+''.join(failures)+'</table>'
    document+='<h2>Interpretation limits</h2><ul>'+''.join('<li>'+esc(s)+'</li>' for s in report['limitations'])+'</ul></html>'
    (out/'report.html').write_text(document)
    print(json.dumps(dict(report=str(out/'report.html'),charts=images,completed=report['completed'])))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('report',type=Path)
    render(parser.parse_args().report)


if __name__ == "__main__":
    raise SystemExit(main())
