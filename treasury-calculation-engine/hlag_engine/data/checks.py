"""Shared market data checks (spec 3.6). Each failed check returns a named reason code."""

import math
from datetime import date

from hlag_engine.data.series import MarketDataSettings, SeriesSpec

MISSING_VALUE = "missing_value"
NON_POSITIVE_VALUE = "non_positive_value"
WRONG_UNIT = "wrong_unit"
OUTSIDE_PLAUSIBILITY_BAND = "outside_plausibility_band"
CONFLICTS_WITH_STORED = "conflicts_with_stored"


def normalise_unit(unit: str | None) -> str:
    return " ".join((unit or "").split()).lower()


def value_checks(spec: SeriesSpec, value: float | None, unit: str | None) -> list[str]:
    """Missing, non-positive (where it applies, P29) and wrong unit."""
    failed: list[str] = []
    if value is None or (isinstance(value, float) and math.isnan(value)):
        failed.append(MISSING_VALUE)
    elif spec.positive_only and value <= 0:
        failed.append(NON_POSITIVE_VALUE)
    if normalise_unit(unit) != normalise_unit(spec.unit):
        failed.append(WRONG_UNIT)
    return failed


def move_pct(value: float, previous: float) -> float:
    """Move from the previous print, in whole percent (P1)."""
    return (value / previous - 1.0) * 100.0


def outside_band(spec: SeriesSpec, settings: MarketDataSettings, value: float, previous: float | None) -> bool:
    """True when the week-on-week move exceeds the series' band (P25: no previous print, no check)."""
    band = settings.band_for(spec)
    if band is None or previous is None or previous <= 0:
        return False
    return abs(move_pct(value, previous)) > band


def days_since_print(print_date: date, on: date) -> int:
    """Calendar days from the last real print to a date (P24)."""
    return (on - print_date).days


def is_stale(spec: SeriesSpec, settings: MarketDataSettings, days: int) -> bool:
    return days > settings.stale_after(spec)
