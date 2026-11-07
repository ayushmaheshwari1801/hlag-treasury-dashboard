"""Market data connectors behind one MarketDataSource interface (spec 1.5, 3.6)."""

import httpx

from hlag_engine.data.connectors.base import FetchResult, MarketDataSource, Observation, SourceError
from hlag_engine.data.connectors.ecb import ECBSource
from hlag_engine.data.connectors.frankfurter import FrankfurterSource
from hlag_engine.data.connectors.fred import FREDSource
from hlag_engine.data.connectors.nyfed import NYFedSource
from hlag_engine.data.env import read_env_value


def default_sources(client: httpx.Client, fred_api_key: str | None = None) -> list[MarketDataSource]:
    """The sources in order of preference. FX falls back from ECB to Frankfurter (3.6)."""
    key = fred_api_key if fred_api_key is not None else read_env_value("FRED_API_KEY")
    return [ECBSource(client), FrankfurterSource(client), NYFedSource(client), FREDSource(client, key)]


__all__ = [
    "ECBSource",
    "FREDSource",
    "FetchResult",
    "FrankfurterSource",
    "MarketDataSource",
    "NYFedSource",
    "Observation",
    "SourceError",
    "default_sources",
]
