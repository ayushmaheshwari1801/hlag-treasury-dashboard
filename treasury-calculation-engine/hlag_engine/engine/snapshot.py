"""Market data snapshot at T0 (spec 4.2), recent history, and the default T0 (4.1).

The snapshot holds, per series, the latest value dated on or before T0 with the date of the last real
print, days_since_print, unit, label, confirmed, filled, and the stale flag from the staleness limits in
thresholds.yaml (daily 5 days, weekly 10; P45). Modules never query the store; they read the snapshot.
"""

from datetime import date, timedelta

from hlag_engine.data.checks import days_since_print, is_stale
from hlag_engine.data.series import API_SERIES, SERIES, MarketDataSettings, load_market_settings
from hlag_engine.data.store import Store
from hlag_engine.modules.interface import SnapshotEntry

HISTORY_DAYS = 400  # covers the 26-week beta window, pre-T0 hedge start dates and a 1-year volatility window


class SnapshotError(ValueError):
    pass


def build_snapshot(store: Store, as_of: date, settings: MarketDataSettings | None = None) -> dict[str, SnapshotEntry]:
    settings = settings or load_market_settings()
    out = {}
    for sid, spec in SERIES.items():
        row = store.latest_on_or_before(sid, as_of)
        if row is None:
            continue
        last_print = store.latest_print_before(sid, as_of + timedelta(days=1))
        print_date = last_print.date if last_print else row.date
        dsp = days_since_print(print_date, as_of)
        out[sid] = SnapshotEntry(
            value=row.value, value_date=print_date, unit=row.unit, days_since_print=dsp, label=row.label,
            confirmed=row.confirmed, filled=row.filled, stale=is_stale(spec, settings, dsp),
        )
    return out


def build_history(store: Store, as_of: date, days: int = HISTORY_DAYS) -> dict[str, list[tuple[date, float]]]:
    """Real prints (filled = false) in the last `days` days, per series."""
    start = as_of - timedelta(days=days)
    return {sid: [(r.date, r.value) for r in store.get_series(sid, start, as_of, include_filled=False)]
            for sid in SERIES}


def default_as_of(store: Store) -> date:
    """The latest date on which every daily API series has a row, real or carried forward (4.1, P72).

    Carry-forward rows count as present, so a series that publishes late (FRED's Brent) does not pull T0
    back; its lag shows as its stale flag instead.
    """
    latest = []
    for sid in API_SERIES:
        row = store.latest_on_or_before(sid, date.max)
        if row is None:
            raise SnapshotError(f"no data loaded for {sid}; load API history first")
        latest.append(row.date)
    return min(latest)
