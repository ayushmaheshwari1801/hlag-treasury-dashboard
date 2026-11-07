"""ECB Data Portal connector (spec 3.6): FX reference rates, €STR and the euro area 2-year yield.

No API key. FX reference rates are quoted per EUR; they are converted once here to USD per
one unit of foreign currency: USD per EUR as published, USD per X = (USD per EUR) / (X per EUR).
EA_2Y is the AAA euro area curve, 2-year spot rate (P28).
"""

import csv
import io
from collections import defaultdict
from datetime import date

import httpx

from hlag_engine.data.connectors.base import FetchResult, Observation, SourceError, get_text, parse_float
from hlag_engine.data.series import PERCENT, USD_PER_UNIT

BASE_URL = "https://data-api.ecb.europa.eu/service/data"
FX_SERIES = {f"FX_USD_PER_{c}": c for c in ("EUR", "CAD", "GBP", "CNY", "INR", "BRL")}
RATE_FLOWS = {
    "ESTR": "EST/B.EU000A2X2A25.WT",
    "EA_2Y": "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y",
}


def fx_to_usd_per_unit(per_eur: dict[str, dict[date, float]]) -> dict[str, dict[date, float]]:
    """Per-EUR quotes {currency: {date: units per EUR}} -> {series_id: {date: USD per unit}}.

    A date without a USD quote gives no value for any currency.
    """
    usd = per_eur.get("USD", {})
    out: dict[str, dict[date, float]] = {"FX_USD_PER_EUR": dict(usd)}
    for ccy, values in per_eur.items():
        if ccy == "USD":
            continue
        out[f"FX_USD_PER_{ccy}"] = {d: usd[d] / v for d, v in values.items() if d in usd and v}
    return out


def _csv_rows(text: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if rows and not {"TIME_PERIOD", "OBS_VALUE"} <= rows[0].keys():
        raise SourceError("ECB response lacks TIME_PERIOD or OBS_VALUE columns")
    return rows


class ECBSource:
    name = "ECB Data Portal"
    series_ids = (*FX_SERIES, *RATE_FLOWS)

    def __init__(self, client: httpx.Client):
        self.client = client

    def _get(self, flow_key: str, start: date, end: date) -> tuple[list[dict[str, str]], str]:
        params = {"startPeriod": start.isoformat(), "endPeriod": end.isoformat(), "format": "csvdata"}
        text, url = get_text(self.client, f"{BASE_URL}/{flow_key}", params)
        return (_csv_rows(text) if text.strip() else []), url

    def fetch(self, series_ids: list[str], start: date, end: date) -> FetchResult:
        unknown = set(series_ids) - set(self.series_ids)
        if unknown:
            raise SourceError(f"ECB does not supply {sorted(unknown)}")
        observations: list[Observation] = []
        fx = [s for s in series_ids if s in FX_SERIES]
        if fx:
            currencies = sorted({"USD", *(FX_SERIES[s] for s in fx if s != "FX_USD_PER_EUR")})
            rows, url = self._get(f"EXR/D.{'+'.join(currencies)}.EUR.SP00.A", start, end)
            per_eur: dict[str, dict[date, float]] = defaultdict(dict)
            for row in rows:
                value = parse_float(row["OBS_VALUE"])
                if value is not None:
                    per_eur[row["CURRENCY"]][date.fromisoformat(row["TIME_PERIOD"])] = value
            converted = fx_to_usd_per_unit(per_eur)
            for sid in fx:
                for d, v in sorted(converted.get(sid, {}).items()):
                    observations.append(Observation(sid, d, v, USD_PER_UNIT, url))
        for sid in (s for s in series_ids if s in RATE_FLOWS):
            rows, url = self._get(RATE_FLOWS[sid], start, end)
            for row in rows:
                observations.append(
                    Observation(sid, date.fromisoformat(row["TIME_PERIOD"]), parse_float(row["OBS_VALUE"]), PERCENT, url)
                )
        return FetchResult(observations, self.name, "ecb_data_api")
