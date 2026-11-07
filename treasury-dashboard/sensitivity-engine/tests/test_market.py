from datetime import date, datetime, timedelta

import httpx
import pytest

from treasury_sensitivity.market import (FixtureProvider, LiveProvider, Observation, ObservationCache, Series, SeriesInvalid, historical_volatility,
                                         market_block, parse_sdmx_json, subtract_months)
from treasury_sensitivity.models import Provenance, ValidationProblem

NOW = datetime(2026, 10, 31, 9)
DAY = date(2026, 10, 31)


def make(obs, **kw):
    base = dict(series_id="T", name="t", unit="u", frequency="daily_business", source="s", source_kind="live", max_age_days=4,
                comparison_mode="previous_observation", caveat="c", observations=obs, retrieved_at=NOW)
    return Series(**{**base, **kw})


def test_missing_dates_stay_missing_and_gaps_reported():
    s = FixtureProvider().get_series("FX_CADUSD", as_of=DAY, now=NOW)
    dates = {o.date for o in s.observations}
    gap = s.gaps()
    assert len(gap) == 1 and gap[0]["calendar_days"] > 4
    start = date.fromisoformat(gap[0]["after"]) + timedelta(days=1)
    missing = [start + timedelta(days=i) for i in range((date.fromisoformat(gap[0]["before"]) - start).days)]
    assert any(d.weekday() < 5 and d not in dates for d in missing)          # a business day that is absent, not filled
    assert len(s.history("All")) == len(s.observations)                       # no points added


def test_duplicate_and_unordered_dates_rejected():
    with pytest.raises(SeriesInvalid):
        make([Observation(date=date(2026, 10, 1), value=1), Observation(date=date(2026, 10, 1), value=2)])
    with pytest.raises(SeriesInvalid):
        make([Observation(date=date(2026, 10, 2), value=1), Observation(date=date(2026, 10, 1), value=2)])


def test_live_feed_with_conflicting_duplicate_dates_falls_back_instead_of_silently_merging(tmp_path):
    bad = {"dataSets": [{"series": {"0": {"observations": {"0": [2.0], "1": [2.1]}}}}],
           "structure": {"dimensions": {"observation": [{"values": [{"id": "2026-10-30"}, {"id": "2026-10-30"}]}]}}}
    s = live(lambda req: httpx.Response(200, json=bad), tmp_path).get_series("ECB_DFR", as_of=DAY, now=NOW)
    assert s.source_kind == "fixture" and s.force_stale and "conflicting duplicate observations" in s.error


def test_previous_change_mode_skips_unchanged_days():
    s = FixtureProvider().get_series("ECB_DFR", as_of=DAY, now=NOW)
    assert (s.latest().value, s.previous().value, s.previous().date) == (2.25, 2.0, date(2026, 10, 28))


def test_freshness():
    s = FixtureProvider().get_series("SCFI", as_of=DAY, now=NOW)
    assert market_block(s, DAY).freshness == "FRESH"
    assert market_block(s, date(2026, 11, 20)).freshness == "STALE"
    assert market_block(make([]), DAY).freshness == "UNAVAILABLE"
    assert make([]).provenance == Provenance.UNAVAILABLE


def test_fixture_is_illustrative_live_is_observed():
    assert FixtureProvider().get_series("SCFI", as_of=DAY, now=NOW).provenance == Provenance.ILLUSTRATIVE
    assert make([Observation(date=DAY, value=1)]).provenance == Provenance.OBSERVED


def test_reference_date_hides_later_observations():
    s = FixtureProvider().get_series("SCFI", as_of=date(2026, 10, 25), now=NOW)
    assert s.latest().date == date(2026, 10, 23) and s.latest().value == 1600.0


def test_history_ranges_use_actual_points_only():
    s = FixtureProvider().get_series("SCFI", as_of=DAY, now=NOW)
    counts = {w: len(s.history(w)) for w in ("1M", "3M", "6M", "1Y", "All")}
    assert counts["1M"] < counts["3M"] < counts["6M"] < counts["1Y"] < counts["All"]
    assert all(o.date >= subtract_months(s.latest().date, 3) for o in s.history("3M"))
    with pytest.raises(ValidationProblem):
        s.history("5Y")
    assert subtract_months(date(2026, 3, 31), 1) == date(2026, 2, 28)


def test_historical_volatility_needs_enough_points():
    s = FixtureProvider().get_series("ECB_ESTR", as_of=DAY, now=NOW)
    assert historical_volatility(s, 60) > 0
    assert historical_volatility(make([Observation(date=DAY, value=1)]), 60) is None


SDMX = {"dataSets": [{"series": {"0:0:0:0:0:0:0": {"observations": {"0": [2.0, 0, 0, None], "1": [2.25, 0, 0, None], "2": [None, 0, 0, None]}}}}],
        "structure": {"dimensions": {"observation": [{"values": [{"id": "2026-10-28"}, {"id": "2026-10-30"}, {"id": "2026-10-31"}]}]}}}


def test_sdmx_parser_skips_blank_observations():
    obs = parse_sdmx_json(SDMX)
    assert [(o.date, o.value) for o in obs] == [(date(2026, 10, 28), 2.0), (date(2026, 10, 30), 2.25)]
    with pytest.raises(Exception):
        parse_sdmx_json({"nope": 1})


def live(handler, tmp_path):
    return LiveProvider(FixtureProvider(), ObservationCache(tmp_path), client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_live_success_is_observed_and_cached(tmp_path):
    def handler(req):
        if "data-api.ecb" in str(req.url):
            return httpx.Response(200, json=SDMX)
        return httpx.Response(200, json={"date": "2026-10-30", "base": "CAD", "quote": "USD", "rate": 0.7312})
    p = live(handler, tmp_path)
    ecb = p.get_series("ECB_DFR", as_of=DAY, now=NOW)
    assert ecb.source_kind == "live" and ecb.provenance == Provenance.OBSERVED and ecb.previous().value == 2.0
    fx = p.get_series("FX_CADUSD", as_of=DAY, now=NOW)
    assert fx.latest().value == 0.7312 and fx.provenance == Provenance.OBSERVED and len(fx.observations) == 1
    assert ObservationCache(tmp_path).read("FX_CADUSD")[0].value == 0.7312


def test_live_failure_falls_back_visibly_and_marked_stale(tmp_path):
    p = live(lambda req: httpx.Response(503), tmp_path)
    s = p.get_series("ECB_DFR", as_of=DAY, now=NOW)
    assert s.source_kind == "fixture" and s.force_stale and "unavailable" in s.error.lower()
    assert market_block(s, DAY).freshness == "STALE" and s.provenance == Provenance.ILLUSTRATIVE


def test_live_failure_prefers_cache_over_fixture(tmp_path):
    ObservationCache(tmp_path).merge("ECB_DFR", [Observation(date=date(2026, 10, 20), value=2.0)])
    s = live(lambda req: httpx.Response(500), tmp_path).get_series("ECB_DFR", as_of=DAY, now=NOW)
    assert s.source_kind == "cache" and s.latest().date == date(2026, 10, 20) and s.error


def test_bunker_and_freight_never_call_the_network(tmp_path):
    def boom(req):
        raise AssertionError("no network call expected")
    p = live(boom, tmp_path)
    assert p.get_series("VLSFO_ROTTERDAM", as_of=DAY, now=NOW).source_kind == "fixture"
    assert p.get_series("SCFI", as_of=DAY, now=NOW).source_kind == "fixture"
