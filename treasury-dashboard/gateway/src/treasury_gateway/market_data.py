"""Read the shared feed store; never silently substitute demonstration observations."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

SERIES = {
    **{f'FX_USD_PER_{c}': ('USD/unit', 5) for c in ('EUR', 'CAD', 'GBP', 'CNY', 'INR', 'BRL')},
    **{s: ('%', 5) for s in ('SOFR', 'ESTR', 'UST_2Y', 'EA_2Y', 'BB_OAS')},
    'BRENT': ('USD/bbl', 5),
    **{s: ('USD/FEU', 10) for s in ('WCI_SHA_RTM', 'WCI_SHA_LAX', 'WCI_RTM_NYC', 'WCI_COMPOSITE')},
    **{s: ('USD/t', 10) for s in ('VLSFO_SIN', 'VLSFO_RTM', 'HSFO_SIN', 'HSFO_RTM')},
}


class CSVBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csv: str = Field(min_length=1, max_length=5_000_000)


def read_snapshot(path: Path, as_of: date) -> dict:
    rows = []
    if path.is_file():
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            for sid in SERIES:
                row = db.execute('SELECT * FROM market_data WHERE series_id=? AND date<=? ORDER BY date DESC LIMIT 1',
                                 (sid, as_of.isoformat())).fetchone()
                if row:
                    rows.append(dict(row))
    by_id = {r['series_id']: r for r in rows}
    result = []
    for sid, (unit, max_age) in SERIES.items():
        row = by_id.get(sid)
        if row is None:
            result.append({'series_id': sid, 'unit': unit, 'status': 'MISSING', 'value': None})
            continue
        age = (as_of - date.fromisoformat(row['date'])).days
        row.update(days_since_print=age, filled=age > 0, status='STALE' if age > max_age else 'OK')
        row['licence_note'] = 'Provider terms apply; publicly visible data is not a redistribution licence.'
        result.append(row)
    return {'as_of': as_of.isoformat(), 'series': result, 'available': len(rows), 'total': len(SERIES),
            'source': 'shared SQLite feed', 'demo': False}


def create_router(path: Path, dependencies=(), read_only: bool = False):
    router = APIRouter(prefix='/api/v1/market', tags=['market'], dependencies=list(dependencies))

    @router.get('/snapshot')
    def snapshot(as_of: date | None = None):
        return read_snapshot(path, as_of or date.today())

    @router.get('/history/{series_id}')
    def history(series_id: str, start: date | None = None, end: date | None = None):
        if series_id not in SERIES:
            raise HTTPException(404, 'Unknown market series')
        if not path.is_file():
            return {'series_id': series_id, 'points': [], 'status': 'MISSING'}
        if start and end and start > end:
            raise HTTPException(422, 'start must not exceed end')
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('''SELECT date,value,unit,source,label,confirmed FROM market_data
                WHERE series_id=? AND date>=? AND date<=? ORDER BY date''',
                (series_id, (start or date(1900, 1, 1)).isoformat(), (end or date.today()).isoformat())).fetchall()
        return {'series_id': series_id, 'points': [dict(r) for r in rows], 'interpolated': False}

    @router.post('/import-csv')
    def import_prices(body: CSVBody):
        if read_only:
            raise HTTPException(409, 'Market data is read-only with CASH_ENGINE=spec; load prices into the hlag_engine '
                                     'store with its own tools')
        # Import uses the same validated, transactional feed implementation as the CLI.
        import tempfile
        import market_feed
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            file = Path(tmp) / 'import.csv'
            file.write_text(body.csv, encoding='utf-8')
            db = market_feed.connect(str(path))
            try:
                with db:
                    return market_feed.import_csv(db, file)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            finally:
                db.close()

    return router
