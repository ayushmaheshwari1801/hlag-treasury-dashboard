"""New York Fed Markets API connector: SOFR (spec 3.6). No API key."""

import json
from datetime import date

import httpx

from hlag_engine.data.connectors.base import FetchResult, Observation, SourceError, get_text
from hlag_engine.data.series import PERCENT

SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"


class NYFedSource:
    name = "New York Fed Markets API"
    series_ids = ("SOFR",)

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, series_ids: list[str], start: date, end: date) -> FetchResult:
        if set(series_ids) - {"SOFR"}:
            raise SourceError(f"NY Fed supplies only SOFR, not {sorted(set(series_ids) - {'SOFR'})}")
        if not series_ids:
            return FetchResult([], self.name, "nyfed_markets_api")
        text, url = get_text(self.client, SOFR_URL, {"startDate": start.isoformat(), "endDate": end.isoformat()})
        try:
            rows = json.loads(text)["refRates"]
            observations = [
                Observation("SOFR", date.fromisoformat(r["effectiveDate"]), r.get("percentRate"), PERCENT, url)
                for r in rows
                if r.get("type", "SOFR") == "SOFR"
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError(f"unexpected NY Fed response: {exc}") from exc
        observations.sort(key=lambda o: o.date)
        return FetchResult(observations, self.name, "nyfed_markets_api")
