"""API history loading: checks, carry-forward, fallback, staleness, audit."""

import json
from datetime import date, timedelta

import pytest

from hlag_engine.data.connectors import default_sources
from hlag_engine.data.ingest import load_api_history, years_before
from tests.data.conftest import FakeSource, mock_client

AS_OF = date(2026, 1, 6)
DEC31 = date(2025, 12, 31)


def test_full_load_from_recorded_responses(store):
    report = load_api_history(store, default_sources(mock_client(), fred_api_key=""), AS_OF)
    assert report.start == date(2024, 1, 6)
    assert set(report.series) == {
        "FX_USD_PER_EUR", "FX_USD_PER_CAD", "FX_USD_PER_GBP", "FX_USD_PER_CNY", "FX_USD_PER_INR",
        "FX_USD_PER_BRL", "ESTR", "SOFR", "UST_2Y", "EA_2Y", "BB_OAS", "BRENT",
    }
    assert report.paths["UST_2Y"] == "fred_csv" and report.paths["ESTR"] == "ecb_data_api"
    assert report.paths["SOFR"] == "nyfed_markets_api"
    assert report.stale_series == []
    assert all(not r.errors and not r.rejected for r in report.series.values())

    # carry-forward on 1 Jan (no publication): filled, MARKET, last value, days_since_print 1
    jan1 = store.get_market_row("FX_USD_PER_EUR", date(2026, 1, 1))
    assert jan1.filled and jan1.value == 1.175 and jan1.days_since_print == 1 and jan1.label.value == "MARKET"
    real = store.get_market_row("FX_USD_PER_EUR", DEC31)
    assert not real.filled and real.confirmed and real.days_since_print == 0 and real.source == "ECB Data Portal"
    assert "data-api.ecb.europa.eu" in real.source_ref
    assert store.get_market_row("UST_2Y", date(2026, 1, 1)).filled  # FRED empty cell is a gap
    assert report.series["UST_2Y"].missing_dates == [date(2026, 1, 1)]
    # 29 Dec .. 6 Jan is 9 calendar days, all present
    assert len(store.get_series("SOFR")) == 9

    [entry] = store.list_audit(action="market_data_load")
    assert entry.details["series"]["UST_2Y"]["path"] == "fred_csv"


def test_fred_api_path_reported_and_key_not_stored(store):
    report = load_api_history(store, default_sources(mock_client(), fred_api_key="SECRET123"), AS_OF, series_ids=["UST_2Y"])
    assert report.paths == {"UST_2Y": "fred_api"}
    dump = json.dumps([e.details for e in store.list_audit()])
    rows = store.get_series("UST_2Y")
    assert "SECRET123" not in dump and all("SECRET123" not in r.source_ref for r in rows)


def test_ecb_failure_falls_back_to_frankfurter_for_fx(store):
    client = mock_client(fail_hosts=("data-api.ecb.europa.eu",))
    report = load_api_history(store, default_sources(client, fred_api_key=""), AS_OF,
                              series_ids=["FX_USD_PER_EUR", "FX_USD_PER_INR", "ESTR"])
    assert report.paths["FX_USD_PER_INR"] == "frankfurter"
    assert any("ECB" in e for e in report.series["FX_USD_PER_INR"].errors)
    assert store.get_market_row("FX_USD_PER_INR", DEC31).source.startswith("Frankfurter")
    assert report.series["ESTR"].path is None and "no source could supply this series" in report.series["ESTR"].errors
    assert report.series["ESTR"].stale


def test_failed_checks_not_stored_and_gap_carried(store):
    d = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    source = FakeSource({"BB_OAS": [(d[0], 1.7), (d[1], -0.1), (d[2], 1.8)]})
    report = load_api_history(store, [source], d[2], series_ids=["BB_OAS"])
    rejected = report.series["BB_OAS"].rejected
    assert [(r.date, r.reasons) for r in rejected] == [(d[1], ["non_positive_value"])]
    row = store.get_market_row("BB_OAS", d[1])
    assert row.filled and row.value == 1.7
    assert store.list_audit(action="market_data_load")[0].details["series"]["BB_OAS"]["rejected"][0]["reasons"] == ["non_positive_value"]


@pytest.mark.parametrize("sid", ["SOFR", "ESTR", "UST_2Y", "EA_2Y"])
def test_negative_rates_allowed(store, sid):
    report = load_api_history(store, [FakeSource({sid: [(AS_OF, -0.4)]})], AS_OF, series_ids=[sid])
    assert report.series[sid].rejected == [] and store.get_market_row(sid, AS_OF).value == -0.4


@pytest.mark.parametrize("sid", ["FX_USD_PER_EUR", "BRENT", "BB_OAS"])
def test_non_positive_rejected_for_prices_fx_and_spread(store, sid):
    report = load_api_history(store, [FakeSource({sid: [(AS_OF, 0.0)]})], AS_OF, series_ids=[sid])
    assert report.series[sid].rejected[0].reasons == ["non_positive_value"]
    assert store.get_market_row(sid, AS_OF) is None


def test_wrong_unit_rejected(store):
    source = FakeSource({"BRENT": [(AS_OF, 60.0)]}, unit={"BRENT": "USD/t"})
    report = load_api_history(store, [source], AS_OF, series_ids=["BRENT"])
    assert report.series["BRENT"].rejected[0].reasons == ["wrong_unit"]


def test_carry_forward_to_as_of_and_staleness(store):
    last = date(2026, 1, 1)
    source = FakeSource({"SOFR": [(last, 3.7)], "ESTR": [(last, 1.9)]})
    report = load_api_history(store, [source], last + timedelta(days=5), series_ids=["SOFR"])
    assert not report.series["SOFR"].stale and report.series["SOFR"].days_since_print == 5
    assert store.get_market_row("SOFR", last + timedelta(days=5)).days_since_print == 5
    assert report.series["SOFR"].filled == 5
    report = load_api_history(store, [source], last + timedelta(days=6), series_ids=["ESTR"])
    assert report.series["ESTR"].stale and report.stale_series == ["ESTR"]


def test_values_outside_window_ignored(store):
    source = FakeSource({"SOFR": [(date(2023, 1, 1), 1.0), (AS_OF, 3.7), (AS_OF + timedelta(days=1), 9.0)]})
    load_api_history(store, [source], AS_OF, series_ids=["SOFR"], years=1)
    assert [r.date for r in store.get_series("SOFR")] == [AS_OF]


def test_reload_is_idempotent(store):
    source = FakeSource({"SOFR": [(date(2026, 1, 2), 3.7), (AS_OF, 3.6)]})
    load_api_history(store, [source], AS_OF, series_ids=["SOFR"])
    first = store.get_series("SOFR")
    load_api_history(store, [source], AS_OF, series_ids=["SOFR"])
    assert [(r.date, r.value, r.filled) for r in store.get_series("SOFR")] == [(r.date, r.value, r.filled) for r in first]


def test_agent_series_not_loadable_from_api(store):
    with pytest.raises(ValueError, match="not an API series"):
        load_api_history(store, [], AS_OF, series_ids=["WCI_SHA_RTM"])


def test_years_before_leap_day():
    assert years_before(date(2028, 2, 29), 2) == date(2026, 2, 28)
