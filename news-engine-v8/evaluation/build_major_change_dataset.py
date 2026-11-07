"""Freeze primary-change labels without rewriting historical v2 evaluation labels."""
from copy import deepcopy
import json
from pathlib import Path
from .build_robustness_dataset import build


def build_major():
    data=deepcopy(build())
    data.update(dataset='major_changes_v3',schema_version=3,scoring_version='major-changes-v1',
        provenance='Spec v2.8: 20 synthetic development families x 6 variants, major-change labels frozen before the run. Secondary observations are allowed but not required. Not independent real-news accuracy.')
    for case in data['cases']:
        case['max_signals']=2
        if case['family'] in {'different_metrics','different_geography','different_reference_basis'}:
            case['required_observations']=case['required_observations'][:1]
        case['headline']=case['body'].split('. ')[0]+'.'
    return data


def main():
    path=Path(__file__).resolve().parents[1]/'tests/fixtures/major_changes_v3.json'
    path.write_text(json.dumps(build_major(),indent=2)+'\n')
    print(path)


if __name__ == "__main__":
    raise SystemExit(main())
