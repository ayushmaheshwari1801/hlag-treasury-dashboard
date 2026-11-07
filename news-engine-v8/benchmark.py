"""Fast MVP acceptance gate; paid smoke is explicitly selected."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('offline', 'live', 'full'), default='offline')
    parser.add_argument('--output', type=Path, default=Path('outputs/mvp-benchmark'))
    parser.add_argument('--model')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    checks = []
    if args.profile in ('offline', 'full'):
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test*.py', '-v']
        completed = subprocess.run(command, capture_output=True, text=True)
        (args.output / 'offline.log').write_text(completed.stdout + completed.stderr, encoding='utf8')
        print(completed.stdout + completed.stderr, end='')
        checks.append({'name': 'offline', 'exit_code': completed.returncode})
    if args.profile in ('live', 'full'):
        command = [sys.executable, '-m', 'evaluation.mvp', '--selection', 'smoke',
                   '--output', str(args.output / 'smoke')]
        if args.model:
            command.extend(['--model', args.model])
        checks.append({'name': 'live', 'exit_code': subprocess.run(command).returncode})
    report = {'status': 'passed' if all(c['exit_code'] == 0 for c in checks) else 'failed',
              'checks': checks, 'wall_seconds': time.monotonic() - started,
              'scope': 'Offline replies establish contracts; paid smoke reports diagnostic quality.'}
    (args.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
