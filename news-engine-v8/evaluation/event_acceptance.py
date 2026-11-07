"""Strict E quality gate, separate from successful execution of the evaluator."""
import argparse
import json
from pathlib import Path


def assess(report):
    checks = {'complete_dataset': report.get('complete') is True}
    for name, summary in {'all': report.get('summary', {}), **report.get('by_sequence', {})}.items():
        steps = summary.get('steps', 0)
        failures = summary.get('service_or_stage_failures', steps)
        checks[name + '.execution_success'] = steps > 0 and (steps - failures) / steps > .85
        checks[name + '.zero_call_replay'] = summary.get('replay_extra_calls') == 0
        for metric in ('association', 'state', 'development'):
            value = summary.get(metric, {})
            denominator = value.get('denominator', 0)
            numerator = value.get('numerator', 0)
            checks[name + '.' + metric] = (steps > 0 and denominator == steps and
                                           0 <= numerator <= denominator and numerator / denominator > .85)
    return {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
            'scope': 'E only; frozen W2 official-source corpus. Not full-workflow acceptance.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    result = assess(json.loads(args.report.read_text()))
    args.report.with_name('quality-gate.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return int(result['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
