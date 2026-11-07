"""Import the user-authorized manual CSV market and profile inputs."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'treasury-dashboard/gateway/src'), str(ROOT / 'treasury-dashboard/sensitivity-engine/src')]

import market_feed
from treasury_gateway.risk_api import RunStore
from treasury_gateway.risk_engine import RiskEngine, validate_profile


def main():
    db = market_feed.connect(str(ROOT / 'market_data.sqlite'))
    try:
        with db:
            counts = market_feed.import_csv(db, ROOT / 'inputs/manual_market.csv')
    finally:
        db.close()
    profile = RiskEngine(ROOT / 'market_data.sqlite').profile
    with (ROOT / 'inputs/manual_profile.csv').open(encoding='utf-8', newline='') as file:
        for row in csv.DictReader(file):
            if row['param_key'] not in profile['values']:
                raise ValueError('Unknown profile key: ' + row['param_key'])
            profile['values'][row['param_key']] = {'value': float(row['value']),
                                                  'label': row['label'], 'source': row['source']}
    validate_profile(profile)
    profile['profile_version'] = 'manual_' + hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:16]
    store = RunStore(ROOT / 'data/treasury.sqlite')
    with store.connect() as state:
        state.execute('INSERT OR IGNORE INTO risk_profiles VALUES(?,?,?,?)',
                      (profile['profile_version'], datetime.now(timezone.utc).isoformat(),
                       'user-authorized manual CSV import', json.dumps(profile)))
    print(json.dumps({'market': counts, 'profile_version': profile['profile_version']}))


if __name__ == '__main__':
    main()
