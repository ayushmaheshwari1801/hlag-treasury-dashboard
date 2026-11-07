"""FRED connector: UST_2Y (DGS2), BB_OAS (BAMLH0A1HYBB) and BRENT (DCOILBRENTEU) (spec 3.6).

Two access paths (P33): the official FRED API when FRED_API_KEY is set, otherwise FRED's
keyless CSV download. The path used is reported in the load report. The API key is never
written to source_ref, the store or the audit log.
"""

import csv
import io
import json
from datetime import date

import httpx

from hlag_engine.data.connectors.base import FetchResult, Observation, SourceError, get_text, parse_float
from hlag_engine.data.series import PERCENT, USD_PER_BBL

API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_IDS = {"UST_2Y": "DGS2", "BB_OAS": "BAMLH0A1HYBB", "BRENT": "DCOILBRENTEU"}
UNITS = {"UST_2Y": PERCENT, "BB_OAS": PERCENT, "BRENT": USD_PER_BBL}
PATH_API = "fred_api"
PATH_CSV = "fred_csv"


def redact(url: str, secret: str | None) -> str:
    return url.replace(secret, "REDACTED") if secret else url


class FREDSource:
    name = "FRED"
    series_ids = tuple(FRED_IDS)

    def __init__(self, client: httpx.Client, api_key: str | None = None):
        self.client = client
        self.api_key = api_key or None

    @property
    def path(self) -> str:
        return PATH_API if self.api_key else PATH_CSV

    def fetch(self, series_ids: list[str], start: date, end: date) -> FetchResult:
        unknown = set(series_ids) - set(FRED_IDS)
        if unknown:
            raise SourceError(f"FRED connector does not supply {sorted(unknown)}")
        observations: list[Observation] = []
        for sid in series_ids:
            rows = self._api(sid, start, end) if self.api_key else self._csv(sid, start, end)
            observations += rows
        return FetchResult(observations, self.name, self.path)

    def _api(self, sid: str, start: date, end: date) -> list[Observation]:
        params = {
            "series_id": FRED_IDS[sid], "api_key": self.api_key or "", "file_type": "json",
            "observation_start": start.isoformat(), "observation_end": end.isoformat(),
        }
        try:
            text, url = get_text(self.client, API_URL, params)
        except SourceError as exc:
            raise SourceError(redact(str(exc), self.api_key)) from None
        url = redact(url, self.api_key)
        try:
            rows = json.loads(text)["observations"]
            return [
                Observation(sid, date.fromisoformat(r["date"]), parse_float(r["value"]), UNITS[sid], url) for r in rows
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError(f"unexpected FRED API response: {exc}") from exc

    def _csv(self, sid: str, start: date, end: date) -> list[Observation]:
        fred_id = FRED_IDS[sid]
        text, url = get_text(self.client, CSV_URL, {"id": fred_id, "cosd": start.isoformat(), "coed": end.isoformat()})
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 2 or header[1].strip() != fred_id:
            raise SourceError(f"unexpected FRED CSV header for {fred_id}: {header}")
        out = []
        for row in reader:
            if not row:
                continue
            d = date.fromisoformat(row[0])
            if start <= d <= end:
                out.append(Observation(sid, d, parse_float(row[1] if len(row) > 1 else None), UNITS[sid], url))
        return out
