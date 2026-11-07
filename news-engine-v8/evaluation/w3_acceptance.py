"""W3 model quality gate after independent report-integrity validation."""
import argparse
import json
from pathlib import Path


def assess(report):
    def above(n, d):
        return d > 0 and 0 <= n <= d and n / d > .85
    planned = report.get('planned', 0)
    checks = {
        'complete_dataset': planned > 0 and report.get('status') == 'completed' and report.get('completed') == planned,
        'accuracy_including_output_state': above(report.get('passed', 0), planned),
        'execution_success': above(report.get('successful_extractions', 0), planned),
        'supported_signal_retention': above(report.get('retained', 0), report.get('retention_denominator', 0)),
        'source_safety': above(report.get('safe', 0), planned),
    }
    return {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
            'scope': 'Synthetic W3 cases with frozen W2; not real-news full-workflow accuracy.'}


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
