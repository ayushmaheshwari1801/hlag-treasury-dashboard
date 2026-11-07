"""Market-data layer: validated series, previous-comparable logic, freshness and adapters.

Rules enforced here (spec s2, s3, s10):
* never fabricate or interpolate: a missing date stays missing and is reported as a gap;
* every block carries source, observation timestamp, retrieval timestamp and freshness;
* fixtures are tagged ILLUSTRATIVE, live/cached observations OBSERVED;
* an unavailable API produces a visible error and a STALE fixture fallback, never silent substitution.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

import httpx

from .config import FIXTURE_DIR
from .models import D, MarketBlock, Provenance, StrictModel, ValidationProblem

GAP_DAYS = {"daily_business": 4, "weekly": 8}
RANGES = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12}


class SeriesInvalid(Exception):
    """A series failed data-quality checks (duplicate/unordered dates, non-finite values).
    Deliberately not a ValueError: pydantic would wrap that, and callers must be able to fall back."""


class Observation(StrictModel):
    date: date
    value: float


class Series(StrictModel):
    series_id: str
    name: str
    unit: str
    frequency: Literal["daily_business", "weekly", "monthly"]
    currency: str | None = None
    source: str
    source_kind: Literal["fixture", "live", "cache"]
    max_age_days: int
    comparison_mode: Literal["previous_observation", "previous_change", "one_week"]
    caveat: str
    observations: list[Observation]
    retrieved_at: datetime
    error: str | None = None
    force_stale: bool = False

    def model_post_init(self, __context) -> None:
        seen: set[date] = set()
        last: date | None = None
        for obs in self.observations:
            if not math.isfinite(obs.value):
                raise SeriesInvalid(f"{self.series_id}: non-finite value on {obs.date}")
            if obs.date in seen:
                raise SeriesInvalid(f"{self.series_id}: duplicate observation date {obs.date}")
            if last is not None and obs.date < last:
                raise SeriesInvalid(f"{self.series_id}: observations must be in date order")
            seen.add(obs.date)
            last = obs.date

    @property
    def provenance(self) -> Provenance:
        if not self.observations:
            return Provenance.UNAVAILABLE
        return Provenance.ILLUSTRATIVE if self.source_kind == "fixture" else Provenance.OBSERVED

    def latest(self) -> Observation | None:
        return self.observations[-1] if self.observations else None

    def previous(self) -> Observation | None:
        """Previous comparable observation. Never interpolated or estimated."""
        if len(self.observations) < 2:
            return None
        latest = self.observations[-1]
        if self.comparison_mode == "previous_observation":
            return self.observations[-2]
        if self.comparison_mode == "one_week":            # latest observation on or before (latest date - 7 days); none -> unavailable
            cutoff = latest.date - timedelta(days=7)
            earlier = [o for o in self.observations[:-1] if o.date <= cutoff]
            return earlier[-1] if earlier else None
        for obs in reversed(self.observations[:-1]):      # previous_change: last different level
            if obs.value != latest.value:
                return obs
        return None

    def gaps(self) -> list[dict]:
        limit = GAP_DAYS.get(self.frequency)
        if limit is None:
            return []
        found = []
        for a, b in zip(self.observations, self.observations[1:]):
            days = (b.date - a.date).days
            if days > limit:
                found.append({"after": a.date.isoformat(), "before": b.date.isoformat(), "calendar_days": days})
        return found

    def history(self, window: str = "All") -> list[Observation]:
        """Actual points only. 1M/3M/6M/1Y are measured back from the latest observation date."""
        latest = self.latest()
        if window == "All" or latest is None:
            return list(self.observations)
        if window not in RANGES:
            raise ValidationProblem(f"range must be one of {', '.join([*RANGES, 'All'])}")
        start = subtract_months(latest.date, RANGES[window])
        return [o for o in self.observations if o.date >= start]

    def freshness(self, as_of: date) -> tuple[str, int | None]:
        latest = self.latest()
        if latest is None:
            return "UNAVAILABLE", None
        age = (as_of - latest.date).days
        stale = self.force_stale or age > self.max_age_days
        return ("STALE" if stale else "FRESH"), age


def subtract_months(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last))


def market_block(series: Series, as_of: date, *, change_in_bps: bool = False) -> MarketBlock:
    latest, previous = series.latest(), series.previous()
    freshness, age = series.freshness(as_of)
    change = change_pct = None
    change_unit = None
    if latest and previous:
        raw = D(latest.value) - D(previous.value)
        if change_in_bps:
            change, change_unit = float(raw * 100), "bps"
        else:
            change, change_unit = float(raw), series.unit
        if previous.value:
            change_pct = float(raw / D(previous.value) * 100)
    return MarketBlock(
        series_id=series.series_id, name=series.name, unit=series.unit, source=series.source,
        source_kind=series.source_kind, latest_value=latest.value if latest else None,
        latest_date=latest.date if latest else None, previous_value=previous.value if previous else None,
        previous_date=previous.date if previous else None, change=change, change_unit=change_unit,
        change_pct=change_pct, comparison_mode=series.comparison_mode,
        observation_timestamp=latest.date.isoformat() if latest else None,
        retrieval_timestamp=series.retrieved_at, freshness=freshness, age_days=age,
        provenance=series.provenance, caveat=series.caveat, gaps=series.gaps(), error=series.error)


def to_usd_per_unit(rate: Decimal, convention: Literal["usd_per_unit", "unit_per_usd"]) -> Decimal:
    """Pair-inversion check: spec quotes are USD per 1 unit of foreign currency."""
    if rate <= 0:
        raise ValidationProblem("FX rate must be positive")
    return rate if convention == "usd_per_unit" else Decimal(1) / rate


def historical_volatility(series: Series, window: int, *, scale: Decimal = Decimal(100)) -> Decimal | None:
    """Sample st.dev. of consecutive-observation changes (x100 for % -> bps). None if too few points."""
    pts = series.observations[-(window + 1):]
    if len(pts) < 3:
        return None
    diffs = [(D(b.value) - D(a.value)) * scale for a, b in zip(pts, pts[1:])]
    mean = sum(diffs) / len(diffs)
    var = sum((x - mean) ** 2 for x in diffs) / (len(diffs) - 1)
    return var.sqrt()


class ProviderUnavailable(RuntimeError):
    pass


class MarketProvider(Protocol):
    def get_series(self, series_id: str, *, as_of: date, now: datetime) -> Series: ...


# ------------------------------------------------------------------ fixtures
class FixtureProvider:
    def __init__(self, path: Path | None = None):
        self.path = path or FIXTURE_DIR / "market.json"
        self._raw = json.loads(self.path.read_text(encoding="utf-8"))["series"]

    def ids(self) -> list[str]:
        return list(self._raw)

    def get_series(self, series_id: str, *, as_of: date, now: datetime) -> Series:
        if series_id not in self._raw:
            raise ProviderUnavailable(f"Unknown series {series_id}")
        raw = dict(self._raw[series_id])
        obs = [Observation(date=date.fromisoformat(o["date"]), value=o["value"]) for o in raw.pop("observations")]
        obs = [o for o in obs if o.date <= as_of]       # never show observations after the as-of date
        raw.pop("series_id")
        return Series(series_id=series_id, source_kind="fixture", retrieved_at=now, observations=obs, **raw)


# ---------------------------------------------------------------------- live
ECB_BASE = "https://data-api.ecb.europa.eu/service/data"
ECB_KEYS = {"ECB_DFR": "FM/D.U2.EUR.4F.KR.DFR.LEV", "ECB_ESTR": "EST/B.EU000A2X2A25.WT"}
FRANKFURTER_BASE = "https://api.frankfurter.dev/v2"
FRANKFURTER_PAIRS = {"FX_CADUSD": ("cad", "usd"), "FX_EURUSD": ("eur", "usd")}


def parse_sdmx_json(payload: dict) -> list[Observation]:
    """ECB Data Portal `format=jsondata` (SDMX-JSON 1.0): the first series' observations keyed by time index."""
    try:
        series = next(iter(payload["dataSets"][0]["series"].values()))
        times = payload["structure"]["dimensions"]["observation"][0]["values"]
        out = []
        for index, cell in series["observations"].items():
            if cell and cell[0] is not None:
                out.append(Observation(date=date.fromisoformat(times[int(index)]["id"]), value=float(cell[0])))
    except (KeyError, IndexError, StopIteration, ValueError, TypeError) as exc:
        raise ProviderUnavailable(f"Unexpected ECB response shape: {exc}") from exc
    by_date: dict[date, float] = {}
    for o in out:
        if o.date in by_date and by_date[o.date] != o.value:
            raise ProviderUnavailable(f"conflicting duplicate observations for {o.date}")
        by_date[o.date] = o.value
    return [Observation(date=d, value=v) for d, v in sorted(by_date.items())]


class ObservationCache:
    """Append-only local record of live observations so history accrues without interpolation."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _file(self, series_id: str) -> Path:
        return self.dir / f"{series_id}.json"

    def read(self, series_id: str) -> list[Observation]:
        try:
            raw = json.loads(self._file(series_id).read_text(encoding="utf-8"))
            return [Observation(date=date.fromisoformat(o["date"]), value=o["value"]) for o in raw]
        except (OSError, ValueError, KeyError):
            return []

    def merge(self, series_id: str, fresh: list[Observation]) -> list[Observation]:
        merged = {o.date: o for o in self.read(series_id)}
        merged.update({o.date: o for o in fresh})
        out = [merged[d] for d in sorted(merged)]
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._file(series_id).with_suffix(".tmp")
        tmp.write_text(json.dumps([{"date": o.date.isoformat(), "value": o.value} for o in out]), encoding="utf-8")
        tmp.replace(self._file(series_id))
        return out


class LiveProvider:
    """ECB (EUR rates) and Frankfurter (FX reference rates). Bunker and freight have no confirmed
    API/licence (spec s3) so they are served from fixtures. NOT exercised against the live endpoints
    in the build session (network allowlist); response parsing is covered by tests with canned payloads."""

    LIVE = set(ECB_KEYS) | set(FRANKFURTER_PAIRS)

    def __init__(self, fixtures: FixtureProvider, cache: ObservationCache, *,
                 client: httpx.Client | None = None, timeout: float = 10.0):
        self.fixtures, self.cache = fixtures, cache
        self.client = client or httpx.Client(timeout=timeout)

    def _fetch(self, series_id: str, as_of: date) -> list[Observation]:
        if series_id in ECB_KEYS:
            start = (as_of - timedelta(days=400)).isoformat()
            r = self.client.get(f"{ECB_BASE}/{ECB_KEYS[series_id]}",
                                params={"format": "jsondata", "startPeriod": start, "endPeriod": as_of.isoformat()})
            r.raise_for_status()
            return parse_sdmx_json(r.json())
        base, quote = FRANKFURTER_PAIRS[series_id]
        r = self.client.get(f"{FRANKFURTER_BASE}/rate/{base}/{quote}", params={"providers": "ecb"})
        r.raise_for_status()
        body = r.json()
        return [Observation(date=date.fromisoformat(body["date"]), value=float(body["rate"]))]

    def get_series(self, series_id: str, *, as_of: date, now: datetime) -> Series:
        template = self.fixtures.get_series(series_id, as_of=as_of, now=now)
        if series_id not in self.LIVE:
            return template        # fixture, tagged ILLUSTRATIVE; caveat already states the licence position
        meta = template.model_dump(exclude={"observations", "source", "source_kind", "error", "force_stale"})
        try:
            fresh = [o for o in self._fetch(series_id, as_of) if o.date <= as_of]
            if not fresh:
                raise ProviderUnavailable("API returned no observations")
            merged = self.cache.merge(series_id, fresh)
            return Series(**meta, source=f"Live: {'ECB Data Portal' if series_id in ECB_KEYS else 'Frankfurter (ECB reference)'}",
                          source_kind="live", observations=merged)
        except (httpx.HTTPError, ValueError, SeriesInvalid, ProviderUnavailable) as exc:
            cached = self.cache.read(series_id)
            error = f"Live API unavailable ({type(exc).__name__}: {exc})"
            if cached:
                return Series(**meta, source="Cache of earlier live observations", source_kind="cache",
                              observations=[o for o in cached if o.date <= as_of], error=error, force_stale=True)
            return Series(**meta, source=template.source, source_kind="fixture", observations=template.observations,
                          error=error + "; showing synthetic fixture", force_stale=True)
