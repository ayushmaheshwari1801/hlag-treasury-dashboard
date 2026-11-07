"""Frankfurter connector: fallback for ECB FX reference rates (spec 3.6). No API key.

Frankfurter republishes the ECB reference rates, quoted per EUR; converted to USD per unit
with the same rule as the ECB connector.
"""

import json
from datetime import date

import httpx

from hlag_engine.data.connectors.base import FetchResult, Observation, SourceError, get_text
from hlag_engine.data.connectors.ecb import FX_SERIES, fx_to_usd_per_unit
from hlag_engine.data.series import USD_PER_UNIT

BASE_URL = "https://api.frankfurter.dev/v1"


class FrankfurterSource:
    name = "Frankfurter (ECB reference rates)"
    series_ids = tuple(FX_SERIES)

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, series_ids: list[str], start: date, end: date) -> FetchResult:
        unknown = set(series_ids) - set(self.series_ids)
        if unknown:
            raise SourceError(f"Frankfurter does not supply {sorted(unknown)}")
        symbols = sorted({"USD", *(FX_SERIES[s] for s in series_ids if s != "FX_USD_PER_EUR")})
        text, url = get_text(
            self.client, f"{BASE_URL}/{start.isoformat()}..{end.isoformat()}", {"base": "EUR", "symbols": ",".join(symbols)}
        )
        try:
            rates = json.loads(text)["rates"]
        except (ValueError, KeyError) as exc:
            raise SourceError(f"unexpected Frankfurter response: {exc}") from exc
        per_eur: dict[str, dict[date, float]] = {}
        for day, quotes in rates.items():
            for ccy, value in quotes.items():
                per_eur.setdefault(ccy, {})[date.fromisoformat(day)] = float(value)
        converted = fx_to_usd_per_unit(per_eur)
        observations = [
            Observation(sid, d, v, USD_PER_UNIT, url)
            for sid in series_ids
            for d, v in sorted(converted.get(sid, {}).items())
            if start <= d <= end
        ]
        return FetchResult(observations, self.name, "frankfurter")
