"""MarketDataSource interface (spec 1.5, 3.6).

A source fetches raw observations for the series it supports. Conversions to the store's
conventions (USD per unit for FX) happen inside the connector, once. Checks, carry-forward
and storage happen in data.ingest, the same way for every source.
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import httpx

DEFAULT_TIMEOUT = 30.0


class SourceError(Exception):
    """A source could not deliver (HTTP error, unexpected format)."""


@dataclass(frozen=True)
class Observation:
    series_id: str
    date: date
    value: float | None  # None when the source publishes an empty value
    unit: str
    source_ref: str  # URL or API call, never containing a secret


@dataclass(frozen=True)
class FetchResult:
    observations: list[Observation]
    source: str  # name shown in market_data.source, e.g. "ECB Data Portal"
    path: str  # which access path was used, e.g. "fred_api" or "fred_csv" (load report)


@runtime_checkable
class MarketDataSource(Protocol):
    name: str
    series_ids: tuple[str, ...]

    def fetch(self, series_ids: list[str], start: date, end: date) -> FetchResult: ...


def get_text(client: httpx.Client, url: str, params: dict[str, str]) -> tuple[str, str]:
    """GET a URL; returns (body, full request URL). Raises SourceError on any HTTP problem."""
    try:
        response = client.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"{url}: {exc}") from exc
    return response.text, str(response.request.url)


def parse_float(text: str | None) -> float | None:
    """A published value, or None for empty and '.' (FRED's missing marker)."""
    if text is None:
        return None
    text = text.strip()
    if text in ("", "."):
        return None
    try:
        return float(text)
    except ValueError:
        raise SourceError(f"not a number: {text!r}") from None
