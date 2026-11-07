"""Fixtures for the storage and ingestion tests. No test calls the internet: HTTP goes to
httpx.MockTransport serving recorded responses from tests/fixtures/market/."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from hlag_engine.contracts.enums import ProvenanceLabel
from hlag_engine.data.connectors.base import FetchResult, Observation, SourceError
from hlag_engine.data.series import spec_for
from hlag_engine.data.store import MarketRow, Store

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "market"
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
TODAY = NOW.date()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def recorded_handler(requests: list[httpx.Request] | None = None, fail_hosts: tuple[str, ...] = ()):
    """Serve the recorded file that matches each request."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        host, path = request.url.host, request.url.path
        if host in fail_hosts:
            return httpx.Response(500, text="server error")
        if host == "data-api.ecb.europa.eu":
            if "/EXR/" in path:
                return httpx.Response(200, text=fixture_text("ecb_exr.csv"))
            if "/EST/" in path:
                return httpx.Response(200, text=fixture_text("ecb_estr.csv"))
            if "/YC/" in path:
                return httpx.Response(200, text=fixture_text("ecb_ea2y.csv"))
        if host == "markets.newyorkfed.org":
            return httpx.Response(200, text=fixture_text("nyfed_sofr.json"))
        if host == "fred.stlouisfed.org":
            return httpx.Response(200, text=fixture_text(f"fred_csv_{request.url.params['id']}.csv"))
        if host == "api.stlouisfed.org":
            if request.url.params["series_id"] == "DGS2":
                return httpx.Response(200, text=fixture_text("fred_api_DGS2.json"))
            return httpx.Response(400, text="no fixture")
        if host == "api.frankfurter.dev":
            return httpx.Response(200, text=fixture_text("frankfurter.json"))
        return httpx.Response(404, text=f"no fixture for {request.url}")

    return handler


def mock_client(**kwargs) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(recorded_handler(**kwargs)))


class FakeSource:
    """A source returning given observations, for controlled ingestion tests."""

    def __init__(self, data: dict[str, list[tuple[date, float | None]]], unit: dict[str, str] | None = None,
                 name: str = "Fake", fail: bool = False):
        self.data, self.unit, self.name, self.fail = data, unit or {}, name, fail
        self.series_ids = tuple(data)

    def fetch(self, series_ids, start, end):
        if self.fail:
            raise SourceError("down")
        obs = [
            Observation(sid, d, v, self.unit.get(sid, spec_for(sid).unit), f"fake://{sid}")
            for sid in series_ids
            for d, v in self.data[sid]
        ]
        return FetchResult(obs, self.name, "fake")


def market_row(series_id: str, day: date, value: float, *, filled: bool = False, confirmed: bool = True,
               days: int = 0) -> MarketRow:
    return MarketRow(
        series_id=series_id, date=day, value=value, unit=spec_for(series_id).unit, source="test",
        label=ProvenanceLabel.MARKET, retrieved_at=NOW, filled=filled, days_since_print=days,
        confirmed=confirmed, source_ref="test://",
    )


def daily(start: date, end: date, step: int = 1):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=step)


@pytest.fixture
def store():
    s = Store(":memory:", clock=lambda: NOW)
    yield s
    s.close()
