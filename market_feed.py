"""Treasury market data ingestion; Python 3.11+, standard library only."""
import argparse
import csv
import io
import json
import math
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


FX = ('EUR', 'CAD', 'GBP', 'CNY', 'INR', 'BRL')
WEEKLY = ('WCI_SHA_RTM', 'WCI_SHA_LAX', 'WCI_RTM_NYC', 'WCI_COMPOSITE',
          'VLSFO_SIN', 'VLSFO_RTM', 'HSFO_SIN', 'HSFO_RTM')
FRED = {'UST_2Y': 'DGS2', 'BB_OAS': 'BAMLH0A1HYBB', 'BRENT': 'DCOILBRENTEU'}
ECB = {'ESTR': ('EST', 'B.EU000A2X2A25.WT'),
       'EA_2Y': ('YC', 'B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y')}
SERIES = {**{f'FX_USD_PER_{c}': ('USD/unit', 'ECB', 5) for c in FX},
          **{s: ('%', 'ECB', 5) for s in ECB},
          'SOFR': ('%', 'NYFED', 5),
          **{s: ('USD/bbl' if s == 'BRENT' else '%', 'FRED', 5) for s in FRED},
          **{s: ('USD/FEU' if s.startswith('WCI') else 'USD/t', 'CSV', 10)
             for s in WEEKLY}}


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS market_data (
          series_id TEXT NOT NULL, date TEXT NOT NULL, value REAL NOT NULL,
          unit TEXT NOT NULL, source TEXT NOT NULL, label TEXT NOT NULL,
          retrieved_at TEXT NOT NULL, filled INTEGER NOT NULL DEFAULT 0,
          days_since_print INTEGER NOT NULL DEFAULT 0, confirmed INTEGER NOT NULL,
          source_ref TEXT NOT NULL, PRIMARY KEY(series_id,date));
        CREATE TABLE IF NOT EXISTS revisions (
          id INTEGER PRIMARY KEY, series_id TEXT, date TEXT,
          old_record TEXT, revised_at TEXT);
        CREATE TABLE IF NOT EXISTS confirm_queue (
          id INTEGER PRIMARY KEY, series_id TEXT, date TEXT, value REAL,
          unit TEXT, source TEXT, source_ref TEXT, reason TEXT,
          retrieved_at TEXT, status TEXT DEFAULT 'pending', decided_by TEXT,
          UNIQUE(series_id,date,value,status));
    ''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(confirm_queue)')}
    if 'label' not in columns:
        db.execute("ALTER TABLE confirm_queue ADD COLUMN label TEXT DEFAULT 'MARKET'")
    return db


def validate(series, day, value, unit):
    if series not in SERIES:
        raise ValueError(f'Unknown series: {series}')
    date.fromisoformat(day)
    if unit != SERIES[series][0]:
        raise ValueError(f'{series}: expected {SERIES[series][0]}, received {unit}')
    if not math.isfinite(value):
        raise ValueError(f'{series}: non-finite value')
    # Interest rates may legitimately be zero or negative.
    if (unit != '%' and value <= 0) or (series == 'BB_OAS' and value < 0):
        raise ValueError(f'{series}: invalid value {value}')


def save(db, series, day, value, unit, source, ref, confirmed=True, label=None):
    validate(series, day, value, unit)
    now = datetime.now(timezone.utc).isoformat()
    old = db.execute('SELECT * FROM market_data WHERE series_id=? AND date=?',
                     (series, day)).fetchone()
    label = label or ('PROXY' if series == 'WCI_COMPOSITE' else 'MARKET')
    if label not in {'MARKET', 'PROXY', 'ASSUMPTION'}:
        raise ValueError('Invalid CSV provenance label')
    if old and (old['value'], old['unit'], old['source'], old['source_ref'],
                old['confirmed'], old['label']) == (value, unit, source, ref, int(confirmed), label):
        return
    if old:
        db.execute('INSERT INTO revisions(series_id,date,old_record,revised_at) VALUES(?,?,?,?)',
                   (series, day, json.dumps(dict(old)), now))
    db.execute('''INSERT OR REPLACE INTO market_data
        VALUES(?,?,?,?,?,?,?,0,0,?,?)''',
        (series, day, value, unit, source, label, now, int(confirmed), ref))


def request_text(url):
    req = Request(url, headers={'User-Agent': 'TreasuryMarketFeed/1.0',
                               'Accept': 'text/csv, application/json'})
    try:
        with urlopen(req, timeout=45) as response:
            return response.read().decode('utf-8-sig')
    except HTTPError as exc:
        # Do not disclose request URLs: FRED URLs contain the API key.
        raise RuntimeError(f'HTTP {exc.code}; request stopped, no automatic retry') from None
    except (URLError, TimeoutError):
        raise RuntimeError('Network request failed; no automatic retry') from None


def ecb_rows(flow, key, start, end):
    url = f'https://data-api.ecb.europa.eu/service/data/{flow}/{key}?'
    url += urlencode({'startPeriod': start, 'endPeriod': end, 'format': 'csvdata'})
    rows = list(csv.DictReader(io.StringIO(request_text(url))))
    if not rows or not {'TIME_PERIOD', 'OBS_VALUE'}.issubset(rows[0]):
        raise ValueError(f'ECB {flow}: empty or unexpected CSV response')
    return rows, url


def fx_observations(rows):
    by_day = {}
    for row in rows:
        if row['OBS_VALUE'].strip():
            by_day.setdefault(row['TIME_PERIOD'], {})[row['CURRENCY']] = float(row['OBS_VALUE'])
    result = []
    for day, rates in sorted(by_day.items()):
        if 'USD' not in rates:
            continue
        for currency in FX:
            if currency == 'EUR' or currency in rates:
                result.append((f'FX_USD_PER_{currency}', day,
                               rates['USD'] / (1 if currency == 'EUR' else rates[currency])))
    return result


def fetch(db, provider, start, end):
    if provider == 'ecb':
        key = 'D.USD+CAD+GBP+CNY+INR+BRL.EUR.SP00.A'
        rows, ref = ecb_rows('EXR', key, start, end)
        observations = fx_observations(rows)
        for s, day, value in observations:
            save(db, s, day, value, 'USD/unit', 'ECB', ref)
        for s, (flow, key) in ECB.items():
            rows, ref = ecb_rows(flow, key, start, end)
            for row in rows:
                if row['OBS_VALUE'].strip():
                    save(db, s, row['TIME_PERIOD'], float(row['OBS_VALUE']), '%', 'ECB', ref)
    elif provider == 'nyfed':
        ref = 'https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?'
        ref += urlencode({'startDate': start, 'endDate': end})
        rows = json.loads(request_text(ref))['refRates']
        if not rows:
            raise ValueError('NYFED: empty response')
        for row in rows:
            save(db, 'SOFR', row['effectiveDate'], float(row['percentRate']), '%', 'NYFED', ref)
    else:
        key = os.environ.get('FRED_API_KEY')
        if not key:
            raise ValueError('Set FRED_API_KEY before fetching FRED data')
        for s, remote in FRED.items():
            ref = f'https://fred.stlouisfed.org/series/{remote}'
            url = 'https://api.stlouisfed.org/fred/series/observations?'
            url += urlencode({'series_id': remote, 'api_key': key, 'file_type': 'json',
                              'observation_start': start, 'observation_end': end})
            rows = json.loads(request_text(url))['observations']
            if not rows:
                raise ValueError(f'FRED {s}: empty response')
            for row in rows:
                if row['value'] != '.':
                    save(db, s, row['date'], float(row['value']), SERIES[s][0], 'FRED', ref)


def import_csv(db, path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {'series_id', 'date', 'value', 'unit', 'source', 'source_ref'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('CSV requires columns: ' + ', '.join(sorted(required)))
        rows = list(reader)
    # Validate the entire file before writing anything.
    seen = set()
    for row in rows:
        s, day = row['series_id'], row['date']
        validate(s, day, float(row['value']), row['unit'])
        if row.get('label') and row['label'] not in {'MARKET', 'PROXY', 'ASSUMPTION'}:
            raise ValueError('Invalid CSV provenance label')
        if row.get('confirmed', '').lower() not in {'', 'true', 'false', '1', '0'}:
            raise ValueError('confirmed must be true or false')
        if not row['source'].strip() or not row['source_ref'].strip():
            raise ValueError('Every CSV observation needs source and source_ref')
        if (s, day) in seen:
            raise ValueError(f'Duplicate CSV observation: {s} {day}')
        seen.add((s, day))
    accepted = queued = 0
    for row in sorted(rows, key=lambda r: (r['series_id'], r['date'])):
        s, day, value = row['series_id'], row['date'], float(row['value'])
        previous = db.execute('''SELECT value FROM market_data WHERE series_id=?
            AND date<? AND date>=? ORDER BY date DESC LIMIT 1''',
            (s, day, (date.fromisoformat(day) - timedelta(days=10)).isoformat())).fetchone()
        limit = .8 if s.startswith('WCI') else .4
        if s in WEEKLY and previous and abs(value / previous['value'] - 1) > limit:
            db.execute('''INSERT OR IGNORE INTO confirm_queue
              (series_id,date,value,unit,source,source_ref,reason,retrieved_at,label)
              VALUES(?,?,?,?,?,?,?,?,?)''', (s, day, value, row['unit'], row['source'],
              row['source_ref'], f'Change exceeds {limit:.0%}', datetime.now(timezone.utc).isoformat(),
              row.get('label') or ('PROXY' if s == 'WCI_COMPOSITE' else 'MARKET')))
            queued += 1
        else:
            save(db, s, day, value, row['unit'], row['source'], row['source_ref'],
                 row.get('confirmed', '').lower() in {'true', '1'}, row.get('label') or None)
            accepted += 1
    return {'accepted': accepted, 'queued': queued}


def snapshot(db, as_of):
    result = []
    for s, (unit, provider, stale_days) in SERIES.items():
        row = db.execute('''SELECT * FROM market_data WHERE series_id=? AND date<=?
            ORDER BY date DESC LIMIT 1''', (s, as_of.isoformat())).fetchone()
        if row is None:
            result.append({'series_id': s, 'status': 'MISSING'})
            continue
        item = dict(row)
        age = (as_of - date.fromisoformat(item['date'])).days
        item.update(days_since_print=age, filled=age > 0,
                    status='STALE' if age > stale_days else 'OK', as_of=as_of.isoformat())
        item['licence_note'] = ('Publicly reported values; verify supplier usage rights'
                                if s in WEEKLY else 'Public reference data; provider terms apply')
        result.append(item)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='market_data.sqlite')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    p = commands.add_parser('fetch')
    p.add_argument('--provider', choices=['all', 'ecb', 'nyfed', 'fred'], default='all')
    p.add_argument('--start', type=date.fromisoformat)
    p.add_argument('--end', type=date.fromisoformat, default=date.today())
    p = commands.add_parser('import-csv')
    p.add_argument('path')
    p = commands.add_parser('snapshot')
    p.add_argument('--as-of', type=date.fromisoformat, default=date.today())
    commands.add_parser('queue')
    p = commands.add_parser('confirm')
    p.add_argument('id', type=int)
    p.add_argument('--user', required=True)
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        if args.command == 'fetch':
            providers = ['ecb', 'nyfed', 'fred'] if args.provider == 'all' else [args.provider]
            failures = []
            for provider in providers:
                owned = [s for s, spec in SERIES.items() if spec[1].lower() == provider]
                last = [db.execute('SELECT MAX(date) FROM market_data WHERE series_id=?',
                                   (s,)).fetchone()[0] for s in owned]
                start = args.start or (date.fromisoformat(min(last)) - timedelta(days=14)
                                       if all(last) else args.end - timedelta(days=731))
                try:
                    if start > args.end:
                        raise ValueError('start must not exceed end')
                    with db:
                        fetch(db, provider, start.isoformat(), args.end.isoformat())
                    print(f'{provider}: loaded {start} to {args.end}')
                except (ValueError, RuntimeError, KeyError, ZeroDivisionError) as exc:
                    failures.append(provider)
                    print(f'{provider}: {exc}', file=sys.stderr)
            return 1 if failures else 0
        with db:
            if args.command == 'import-csv':
                print(json.dumps(import_csv(db, args.path)))
            elif args.command == 'snapshot':
                print(json.dumps(snapshot(db, args.as_of), indent=2))
            elif args.command == 'queue':
                print(json.dumps([dict(r) for r in db.execute(
                    "SELECT * FROM confirm_queue WHERE status='pending'")], indent=2))
            elif args.command == 'confirm':
                row = db.execute("SELECT * FROM confirm_queue WHERE id=? AND status='pending'",
                                 (args.id,)).fetchone()
                if row is None:
                    raise ValueError('Pending queue item not found')
                save(db, row['series_id'], row['date'], row['value'], row['unit'],
                     row['source'], row['source_ref'], True, row['label'])
                db.execute("UPDATE confirm_queue SET status='confirmed',decided_by=? WHERE id=?",
                           (args.user, args.id))
                print('Confirmed')
            else:
                print(f'Initialized {args.db}; {len(SERIES)} series configured')
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
