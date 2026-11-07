"""Connectors against recorded responses (no internet)."""

from datetime import date

import httpx
import pytest

from hlag_engine.data.connectors import (
    ECBSource,
    FREDSource,
    FrankfurterSource,
    MarketDataSource,
    NYFedSource,
    SourceError,
    default_sources,
)
from hlag_engine.data.connectors.ecb import fx_to_usd_per_unit
from tests.data.conftest import mock_client

START, END = date(2025, 12, 29), date(2026, 1, 6)
DEC31 = date(2025, 12, 31)


def values(result, sid):
    return {o.date: o.value for o in result.observations if o.series_id == sid}


def test_sources_implement_interface():
    client = mock_client()
    for source in default_sources(client, fred_api_key=""):
        assert isinstance(source, MarketDataSource)


def test_fx_conversion_rule():
    out = fx_to_usd_per_unit({"USD": {DEC31: 1.175}, "INR": {DEC31: 105.5965, date(2026, 1, 1): 1.0}})
    assert out["FX_USD_PER_EUR"][DEC31] == 1.175
    assert out["FX_USD_PER_INR"] == {DEC31: pytest.approx(1.175 / 105.5965)}  # no USD quote, no value


def test_ecb_fx_converted_once_to_usd_per_unit():
    requests = []
    result = ECBSource(mock_client(requests=requests)).fetch(
        [f"FX_USD_PER_{c}" for c in ("EUR", "CAD", "GBP", "CNY", "INR", "BRL")], START, END
    )
    assert values(result, "FX_USD_PER_EUR")[DEC31] == 1.175
    assert values(result, "FX_USD_PER_INR")[DEC31] == pytest.approx(1.175 / 105.5965)
    assert values(result, "FX_USD_PER_GBP")[DEC31] == pytest.approx(1.175 / 0.8726)
    assert date(2026, 1, 1) not in values(result, "FX_USD_PER_EUR")  # no publication
    assert all(o.unit == "USD per unit" for o in result.observations)
    assert len(requests) == 1 and "EXR/D.BRL+CAD+CNY+GBP+INR+USD.EUR.SP00.A" in str(requests[0].url)
    assert result.path == "ecb_data_api"


def test_ecb_estr_and_aaa_two_year_yield():
    requests = []
    result = ECBSource(mock_client(requests=requests)).fetch(["ESTR", "EA_2Y"], START, END)
    assert values(result, "ESTR")[DEC31] == 1.921
    assert values(result, "EA_2Y")[DEC31] == pytest.approx(2.108454926)
    urls = [str(r.url) for r in requests]
    assert any("EST/B.EU000A2X2A25.WT" in u for u in urls)
    assert any("YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y" in u for u in urls)  # AAA curve (P28)


def test_frankfurter_matches_ecb_conversion():
    result = FrankfurterSource(mock_client()).fetch(["FX_USD_PER_EUR", "FX_USD_PER_INR"], START, END)
    assert values(result, "FX_USD_PER_INR")[DEC31] == pytest.approx(1.175 / 105.5965)
    assert result.path == "frankfurter"


def test_nyfed_sofr_sorted():
    result = NYFedSource(mock_client()).fetch(["SOFR"], START, END)
    dates = [o.date for o in result.observations]
    assert dates == sorted(dates) and values(result, "SOFR")[DEC31] == 3.87
    assert result.observations[0].unit == "%"


def test_fred_csv_path_without_key():
    requests = []
    result = FREDSource(mock_client(requests=requests)).fetch(["UST_2Y", "BB_OAS", "BRENT"], START, END)
    assert result.path == "fred_csv"
    assert values(result, "UST_2Y")[date(2026, 1, 1)] is None  # empty cell on a holiday
    assert values(result, "BRENT")[DEC31] == 61.35
    assert {o.unit for o in result.observations if o.series_id == "BRENT"} == {"USD/bbl"}
    assert all("api_key" not in str(r.url) for r in requests)
    assert {r.url.params["id"] for r in requests} == {"DGS2", "BAMLH0A1HYBB", "DCOILBRENTEU"}


def test_fred_api_path_with_key_never_leaks_key():
    requests = []
    result = FREDSource(mock_client(requests=requests), api_key="SECRET123").fetch(["UST_2Y"], START, END)
    assert result.path == "fred_api"
    assert requests[0].url.params["api_key"] == "SECRET123"
    assert values(result, "UST_2Y")[date(2026, 1, 1)] is None  # "." marker
    assert values(result, "UST_2Y")[DEC31] == 3.47
    assert all("SECRET123" not in o.source_ref for o in result.observations)


def test_fred_api_error_does_not_leak_key():
    with pytest.raises(SourceError) as exc:
        FREDSource(mock_client(), api_key="SECRET123").fetch(["BB_OAS"], START, END)
    assert "SECRET123" not in str(exc.value)


def test_http_error_becomes_source_error():
    with pytest.raises(SourceError):
        ECBSource(mock_client(fail_hosts=("data-api.ecb.europa.eu",))).fetch(["ESTR"], START, END)


def test_unexpected_format_is_source_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(SourceError):
        NYFedSource(client).fetch(["SOFR"], START, END)
    with pytest.raises(SourceError):
        FREDSource(client).fetch(["UST_2Y"], START, END)


def test_unsupported_series_rejected():
    with pytest.raises(SourceError):
        ECBSource(mock_client()).fetch(["SOFR"], START, END)
    with pytest.raises(SourceError):
        NYFedSource(mock_client()).fetch(["ESTR"], START, END)


def test_default_sources_read_fred_key_from_env(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "abc")
    fred = [s for s in default_sources(mock_client()) if isinstance(s, FREDSource)][0]
    assert fred.path == "fred_api"
    monkeypatch.setenv("FRED_API_KEY", "")
    fred = [s for s in default_sources(mock_client()) if isinstance(s, FREDSource)][0]
    assert fred.path == "fred_csv"
