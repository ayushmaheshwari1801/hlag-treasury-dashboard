import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import market_feed as m


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.db = m.connect(':memory:')

    def tearDown(self):
        self.db.close()

    def test_fx_cross_uses_same_day_only(self):
        rows = [{'TIME_PERIOD': '2026-06-01', 'CURRENCY': 'USD', 'OBS_VALUE': '1.2'},
                {'TIME_PERIOD': '2026-06-01', 'CURRENCY': 'CAD', 'OBS_VALUE': '1.5'},
                {'TIME_PERIOD': '2026-06-02', 'CURRENCY': 'CAD', 'OBS_VALUE': '1.6'}]
        values = {(s, d): v for s, d, v in m.fx_observations(rows)}
        self.assertAlmostEqual(values['FX_USD_PER_CAD', '2026-06-01'], .8)
        self.assertEqual(len(values), 2)

    def test_validation_and_revision(self):
        m.save(self.db, 'ESTR', '2026-01-01', -.1, '%', 'ECB', 'url')
        m.save(self.db, 'ESTR', '2026-01-01', -.2, '%', 'ECB', 'url')
        m.save(self.db, 'ESTR', '2026-01-01', -.2, '%', 'ECB', 'url')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 1)
        for value, unit in [(0, 'USD/t'), (float('nan'), 'USD/t'), (500, 'USD/bbl')]:
            with self.assertRaises(ValueError):
                m.validate('VLSFO_SIN', '2026-01-01', value, unit)

    def test_snapshot_no_lookahead_and_staleness(self):
        m.save(self.db, 'SOFR', '2026-01-01', 4, '%', 'NYFED', 'url')
        m.save(self.db, 'SOFR', '2026-02-01', 3, '%', 'NYFED', 'url')
        item = next(r for r in m.snapshot(self.db, date(2026, 1, 8)) if r['series_id'] == 'SOFR')
        self.assertEqual(item['value'], 4)
        self.assertEqual(item['status'], 'STALE')
        self.assertEqual(item['days_since_print'], 7)

    def test_csv_queue_and_atomic_validation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path = Path(tmp) / 'prices.csv'
            def write(rows):
                with path.open('w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['series_id', 'date', 'value', 'unit', 'source', 'source_ref'])
                    writer.writerows(rows)
            write([['VLSFO_SIN', '2026-01-01', 500, 'USD/t', 'supplier', 'url'],
                   ['VLSFO_SIN', '2026-01-08', 800, 'USD/t', 'supplier', 'url']])
            self.assertEqual(m.import_csv(self.db, path), {'accepted': 1, 'queued': 1})
            self.assertEqual(self.db.execute('SELECT confirmed FROM market_data').fetchone()[0], 0)
            write([['HSFO_RTM', '2026-01-01', 400, 'USD/t', 'supplier', 'url'],
                   ['HSFO_RTM', '2026-01-08', 450, 'USD/bbl', 'supplier', 'url']])
            with self.assertRaises(ValueError):
                m.import_csv(self.db, path)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM market_data').fetchone()[0], 1)

    def test_provider_parsers(self):
        with patch.object(m, 'request_text', return_value='{"refRates":[{"effectiveDate":"2026-01-02","percentRate":3.5}]}'):
            m.fetch(self.db, 'nyfed', '2026-01-01', '2026-01-03')
        with patch.dict('os.environ', {'FRED_API_KEY': 'secret'}), patch.object(
                m, 'request_text', return_value='{"observations":[{"date":"2026-01-02","value":"."},{"date":"2026-01-03","value":"2"}]}'):
            m.fetch(self.db, 'fred', '2026-01-01', '2026-01-03')
        rows = self.db.execute('SELECT * FROM market_data').fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all('secret' not in r['source_ref'] for r in rows))

    def test_manual_daily_csv_preserves_provenance_and_zero_rate(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path = Path(tmp) / 'manual.csv'
            path.write_text('series_id,date,value,unit,source,source_ref,label,confirmed\n'
                'ESTR,2026-01-01,0,%,Manual,manual.md,ASSUMPTION,true\n', encoding='utf-8')
            self.assertEqual(m.import_csv(self.db, path), {'accepted': 1, 'queued': 0})
            row = self.db.execute('SELECT * FROM market_data').fetchone()
            self.assertEqual((row['label'], row['confirmed'], row['value']), ('ASSUMPTION', 1, 0))
            m.save(self.db, 'ESTR', '2026-01-01', 0, '%', 'Manual', 'manual.md', True, 'MARKET')
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
