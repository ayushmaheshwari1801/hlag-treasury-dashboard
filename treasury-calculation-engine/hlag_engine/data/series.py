"""Market data series (spec 3.6) and their check settings.

One FX convention: every FX series is USD per one unit of foreign currency (3.6).
Staleness limits and plausibility bands come from thresholds.yaml (market_data, ASSUMPTION).
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from hlag_engine.company_profile.scenario_validation import DEFAULT_THRESHOLDS_PATH


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class SourceKind(StrEnum):
    API = "api"  # loaded by a connector (ECB, NY Fed, FRED)
    AGENT = "agent"  # hand-loaded CSV history plus agent retrieval (submit_market_value)


USD_PER_UNIT = "USD per unit"
PERCENT = "%"
USD_PER_FEU = "USD/FEU"
USD_PER_T = "USD/t"
USD_PER_BBL = "USD/bbl"

FX_CURRENCIES = ("EUR", "CAD", "GBP", "CNY", "INR", "BRL")


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    frequency: Frequency
    unit: str
    source_kind: SourceKind
    positive_only: bool  # P29: rates may be negative; spreads, prices and FX may not
    band_group: str | None = None  # "fuel" or "wci": plausibility band for agent values


def _specs() -> dict[str, SeriesSpec]:
    specs = [
        SeriesSpec(f"FX_USD_PER_{ccy}", Frequency.DAILY, USD_PER_UNIT, SourceKind.API, True)
        for ccy in FX_CURRENCIES
    ]
    specs += [
        SeriesSpec("ESTR", Frequency.DAILY, PERCENT, SourceKind.API, False),
        SeriesSpec("SOFR", Frequency.DAILY, PERCENT, SourceKind.API, False),
        SeriesSpec("UST_2Y", Frequency.DAILY, PERCENT, SourceKind.API, False),
        SeriesSpec("EA_2Y", Frequency.DAILY, PERCENT, SourceKind.API, False),
        SeriesSpec("BB_OAS", Frequency.DAILY, PERCENT, SourceKind.API, True),
        SeriesSpec("BRENT", Frequency.DAILY, USD_PER_BBL, SourceKind.API, True),
    ]
    specs += [
        SeriesSpec(sid, Frequency.WEEKLY, USD_PER_FEU, SourceKind.AGENT, True, "wci")
        for sid in ("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE")
    ]
    specs += [
        SeriesSpec(sid, Frequency.WEEKLY, USD_PER_T, SourceKind.AGENT, True, "fuel")
        for sid in ("VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM")
    ]
    return {s.series_id: s for s in specs}


SERIES: dict[str, SeriesSpec] = _specs()
API_SERIES = tuple(sid for sid, s in SERIES.items() if s.source_kind is SourceKind.API)
AGENT_SERIES = tuple(sid for sid, s in SERIES.items() if s.source_kind is SourceKind.AGENT)


@dataclass(frozen=True)
class MarketDataSettings:
    staleness_days: dict[Frequency, int]
    band_pct: dict[str, float]

    def stale_after(self, spec: SeriesSpec) -> int:
        return self.staleness_days[spec.frequency]

    def band_for(self, spec: SeriesSpec) -> float | None:
        return self.band_pct.get(spec.band_group) if spec.band_group else None


def load_market_settings(path: Path | str = DEFAULT_THRESHOLDS_PATH) -> MarketDataSettings:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)["market_data"]
    return MarketDataSettings(
        staleness_days={Frequency(k): int(v) for k, v in raw["staleness_days"].items()},
        band_pct={k: float(v) for k, v in raw["plausibility_band_pct"].items()},
    )


def spec_for(series_id: str) -> SeriesSpec:
    try:
        return SERIES[series_id]
    except KeyError:
        raise KeyError(f"unknown series {series_id}") from None
