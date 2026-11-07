"""Shared engine test fixtures: a store with every series loaded, and toy modules for the walkthrough."""

from datetime import UTC, date, datetime, timedelta

import pytest

from hlag_engine.contracts.enums import ProvenanceLabel
from hlag_engine.data.series import SERIES, Frequency
from hlag_engine.data.store import MarketRow, Store

T0 = date(2026, 10, 31)
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
LEVELS = {
    "FX_USD_PER_EUR": 1.15, "FX_USD_PER_CAD": 0.72, "FX_USD_PER_GBP": 1.32, "FX_USD_PER_CNY": 0.14,
    "FX_USD_PER_INR": 0.0113, "FX_USD_PER_BRL": 0.185, "ESTR": 2.0, "SOFR": 4.0, "UST_2Y": 3.8, "EA_2Y": 2.2,
    "BB_OAS": 2.0, "BRENT": 80.0, "WCI_SHA_RTM": 2800.0, "WCI_SHA_LAX": 3000.0, "WCI_RTM_NYC": 2200.0,
    "WCI_COMPOSITE": 2600.0, "VLSFO_SIN": 600.0, "VLSFO_RTM": 580.0, "HSFO_SIN": 450.0, "HSFO_RTM": 430.0,
}


def rows_for(series_id: str, value: float, end: date = T0, days: int = 420, skip: set[str] = frozenset()) -> list[MarketRow]:
    spec = SERIES[series_id]
    out = []
    step = 1 if spec.frequency is Frequency.DAILY else 7
    last_print = end - timedelta(days=2) if spec.frequency is Frequency.WEEKLY else end
    d = last_print - timedelta(days=days)
    while d <= last_print:
        out.append(MarketRow(series_id, d, value, spec.unit, "test", ProvenanceLabel.MARKET, NOW, False, 0, True, "test://"))
        d += timedelta(days=step)
    return out


def loaded_store(skip: set[str] = frozenset()) -> Store:
    store = Store(":memory:", clock=lambda: NOW)
    rows = []
    for sid, value in LEVELS.items():
        if sid not in skip:
            rows += rows_for(sid, value)
    store.upsert_market_rows(rows)
    return store


@pytest.fixture
def store():
    s = loaded_store()
    yield s
    s.close()
