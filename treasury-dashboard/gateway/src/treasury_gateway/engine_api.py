"""Read-only hlag_engine endpoints for the frontend: market overview, run cash flows and adjustable assumptions."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

NOTE = ('Baseline and scenario amounts are the paired cash lines in USD m. BASE_AMOUNT lines sit on the baseline cash '
        'path in full; DELTA_ONLY lines are already inside the OCF run-rate, so only their delta moves the path. '
        'path_items holds the path-level baseline flow (OCF run-rate, lease, capex and BASE_AMOUNT lines) and the '
        'anchor adjustment, which are not cash lines (hlag_engine runs only).')


def cashflows(run: dict) -> dict:
    keys = ('baseline', 'scenario', 'delta')
    by_line = defaultdict(lambda: dict.fromkeys(keys, 0.))
    by_driver = defaultdict(lambda: dict.fromkeys(keys, 0.))
    by_driver_day = defaultdict(lambda: dict.fromkeys(keys, 0.))   # every cash line has its own date
    for line in run.get('lines', []):
        month = line['date'][:7]
        amounts = (line['amount_usd_base_m'], line['amount_usd_scenario_m'], line['delta_usd_m'])
        for bucket in (by_line[(line['owner'], month, line['line_code'], line.get('treatment'))], by_driver[(line['owner'], month)],
                       by_driver_day[(line['owner'], line['date'][:10])]):
            for k, v in zip(keys, amounts):
                bucket[k] += v

    def rows(table, names, field):
        return [{**dict(zip(names, key)), 'usd_m': v[field]} for key, v in sorted(table.items(), key=lambda kv: tuple(str(x) for x in kv[0]))]

    names = ('driver', 'month', 'line_code', 'treatment')
    path = defaultdict(lambda: {'baseline_flow_usd_m': 0., 'anchor_usd_m': 0., 'delta_usd_m': 0.})
    for item in run.get('path_components', []):
        for k in path[item['date'][:7]]:
            path[item['date'][:7]][k] += item[k]
    return {'run_id': run['run_id'], 'as_of': run.get('as_of'), 'unit': 'USD m', 'scenario': run.get('scenario', {}).get('name'),
            'baseline': rows(by_line, names, 'baseline'), 'scenario_flows': rows(by_line, names, 'scenario'),
            'deltas': rows(by_line, names, 'delta'),
            'by_driver_month': [{'driver': d, 'month': m, 'baseline_usd_m': v['baseline'], 'scenario_usd_m': v['scenario'],
                                 'delta_usd_m': v['delta']} for (d, m), v in sorted(by_driver.items())],
            # the same amounts by driver and day, so the page can show daily, weekly or monthly columns
            'by_driver_day': [{'driver': d, 'date': day, 'baseline_usd_m': v['baseline'], 'scenario_usd_m': v['scenario'],
                               'delta_usd_m': v['delta']} for (d, day), v in sorted(by_driver_day.items())],
            'path_items': [{'month': m, **v} for m, v in sorted(path.items())],
            'totals': {k: sum(v[k] for v in by_driver.values()) for k in keys}, 'note': NOTE,
            'liquidity': liquidity_paths(run)}


def liquidity_paths(run: dict) -> dict | None:
    """Both liquidity paths with the run's minimum reserve. Liquidity = headroom + reserve, i.e. cash plus undrawn
    credit lines [plus the special fund when counted]: the amount the reserve is tested against."""
    inputs = run.get('liquidity_inputs')
    if not inputs or not run.get('baseline_path'):
        return None
    reserve = inputs['reserve_usd_m']

    def path(points):
        return [{'date': p['date'], 'cash_usd_m': p['cash_usd_m'], 'headroom_usd_m': p['headroom_usd_m'],
                 'liquidity_usd_m': p['headroom_usd_m'] + reserve} for p in points]
    return {'reserve_usd_m': reserve, 'cash_t0_usd_m': inputs['cash_t0_usd_m'],
            'baseline_path': path(run['baseline_path']), 'scenario_path': path(run.get('scenario_path', [])),
            'note': 'Liquidity = cash + undrawn credit lines (+ special fund when counted); headroom = liquidity - reserve.'}


class RefreshRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=120)


def create_router(run_store, store_path=None, dependencies=(), writable=False, sources_factory=None, today=None):
    """writable: only with CASH_ENGINE=spec may the explicit market data refresh write to the engine store.
    sources_factory and today are for tests (no network, fixed date)."""
    router = APIRouter(prefix='/api/v1/engine', tags=['hlag engine'], dependencies=list(dependencies))

    def shared():
        from .engine_adapter import _Shared
        return _Shared.get(store_path)

    @router.get('/market')
    def market(as_of: date | None = None):
        from .engine_adapter import market_overview
        return market_overview(shared(), as_of)

    @router.get('/runs/{run_id}/cashflows')
    def run_cashflows(run_id: str):
        return cashflows(run_store.get(run_id))

    @router.get('/assumptions')
    def engine_assumptions():
        from .engine_adapter import assumptions
        return assumptions(shared())

    @router.post('/market/refresh')
    def refresh(body: RefreshRequest):
        """Explicit human action: load ECB, NY Fed and FRED history into the engine store (the gateway's only
        write to it), recorded in the engine's audit log with this person as the actor."""
        if not writable:
            raise HTTPException(409, 'Market data refresh is only available with CASH_ENGINE=spec')
        from .engine_adapter import refresh_market
        day = today() if today else date.today()
        try:
            return refresh_market(shared(), body.actor, day, sources_factory() if sources_factory else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    return router
