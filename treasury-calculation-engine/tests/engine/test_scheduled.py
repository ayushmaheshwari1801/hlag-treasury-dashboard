"""Scheduled runs (4.10, D66) and the daily and weekly baseline change (D71)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from hlag_engine.company_profile.schema import profile_from_dict
from hlag_engine.contracts.baseline_change import BaselineChangeWindow
from hlag_engine.contracts.enums import PresetId, ScenarioSource
from hlag_engine.engine.daily import run_daily
from hlag_engine.engine.liquidity import LiquidityInputs
from hlag_engine.engine.presets import STANDARD_SET
from hlag_engine.engine.run import Engine
from hlag_engine.engine.scheduled import BASELINE_NAME, baseline_change, run_scheduled, scheduled_baselines
from tests.engine.conftest import LEVELS, NOW, T0, loaded_store
from tests.engine.test_run import anchored_raw

PREV = T0 - timedelta(days=7)


def test_standard_set_composition():
    ids = [p.value for p in STANDARD_SET]
    assert ids[:6] == ["red_sea_reescalation", "demand_slump", "oil_spike", "fed_cutting_cycle", "eur_rally", "em_stress"]
    assert len(ids) == 20 and "rates_up_100bp" in ids and "fx_brl_down_10" in ids


def test_scheduled_runs_with_zim_pair_and_skips():
    engine = Engine(loaded_store(), profile=profile_from_dict(anchored_raw()), clock=lambda: NOW)
    result = run_scheduled(engine, T0, [PresetId.EUR_RALLY, PresetId.FED_CUTTING_CYCLE, PresetId.RATES_UP_100BP])
    assert set(result.runs) == {"eur_rally", "rates_up_100bp"}
    # the test profile lists three FOMC dates, the cutting cycle needs four: listed, not fatal
    assert [s["preset"] for s in result.skipped] == ["fed_cutting_cycle"] and not result.failed
    for run_id in result.runs.values():
        assert engine.store.get_run_record(run_id).params.source is ScenarioSource.SCHEDULED
    base = engine.store.get_run_record(result.baseline_run_id)
    assert base.params.name == BASELINE_NAME and base.pair_id == result.zim_pair.pair_id
    assert result.zim_pair.run_b.scenario.name == BASELINE_NAME
    assert [b.run_id for b in scheduled_baselines(engine)] == [result.baseline_run_id]  # run B excluded


def two_day_engine(anchor_wci: float | None = None, liquidity=None):
    """A store where WCI and SOFR move between PREV and T0, and two scheduled baselines (PREV and T0)."""
    store = loaded_store()
    store.conn.execute("UPDATE market_data SET value = 3200 WHERE series_id = 'WCI_SHA_RTM' AND date > ?", (PREV.isoformat(),))
    store.conn.execute("UPDATE market_data SET value = 4.25 WHERE series_id = 'SOFR' AND date > ?", (PREV.isoformat(),))
    store.conn.commit()
    raw = anchored_raw()
    if anchor_wci is not None:
        raw["anchor_snapshot"]["series"]["WCI_SHA_RTM"]["value"] = anchor_wci
    engine = Engine(store, profile=profile_from_dict(raw), clock=lambda: NOW, liquidity=liquidity)
    run_scheduled(engine, PREV, [], with_zim_pair=False)
    run_scheduled(engine, T0, [], with_zim_pair=False)
    return engine


@pytest.fixture(scope="module")
def changes():
    engine = two_day_engine()
    daily = baseline_change(engine, T0, BaselineChangeWindow.DAILY)
    weekly = baseline_change(engine, T0, BaselineChangeWindow.WEEKLY)
    return engine, daily, weekly


def test_baseline_change_parts_sum_and_are_stored(changes):
    engine, daily, weekly = changes
    for outcome in (daily, weekly):
        c = outcome.change
        d = c.by_driver_usd_m
        assert abs(d.freight + d.bunker + d.rates + d.fx + c.other_usd_m - c.total_usd_m) <= 1e-8
        assert d.freight != 0 and d.rates != 0  # this week's WCI and SOFR moves
        assert d.fx == 0
    assert daily.change.previous_run_id == weekly.change.previous_run_id  # the only earlier baseline is 7 days old
    stored = engine.store.list_baseline_changes()
    assert {c.window for c in stored} == {BaselineChangeWindow.DAILY, BaselineChangeWindow.WEEKLY}
    assert engine.store.list_baseline_changes(window="weekly")[0].total_usd_m == weekly.change.total_usd_m


def test_weekly_skipped_without_a_baseline_7_days_old():
    engine = Engine(loaded_store(), profile=profile_from_dict(anchored_raw()), clock=lambda: NOW)
    run_scheduled(engine, T0 - timedelta(days=3), [], with_zim_pair=False)
    run_scheduled(engine, T0, [], with_zim_pair=False)
    assert baseline_change(engine, T0, BaselineChangeWindow.DAILY).change is not None
    weekly = baseline_change(engine, T0, BaselineChangeWindow.WEEKLY)
    assert weekly.change is None and "7 days" in weekly.skipped


PIN = LiquidityInputs(t1_start=Decimal(3_000_000_000), t2_start=Decimal(725_000_000), t3_start=Decimal(0),
                      reserve=Decimal(750_000_000), ocf_daily=Decimal(0),
                      capex=[(date(2027, 10, 31), Decimal(-1_500_000_000))])  # pins the minimum on the last path date


@pytest.fixture(scope="module")
def two_anchors():
    engines = [two_day_engine(anchor, PIN) for anchor in (2000.0, 2600.0)]
    for engine in engines:  # the property holds only while the minimum's date stays put (P87)
        assert {b.results["liquidity"]["h_b_min_date"] for b in scheduled_baselines(engine)} == {"2027-10-31"}
    a, b = (baseline_change(e, T0, BaselineChangeWindow.WEEKLY).change for e in engines)
    return a, b


def test_moving_the_anchor_leaves_bunker_rates_fx_parts_unchanged(two_anchors):
    """P87: bunker, rates and FX work in price levels, so changing the WCI anchor leaves their parts identical (the
    anchor term cancels within each swap step while the minimum's date stays put); only the freight part moves."""
    a, b = two_anchors
    for driver in ("bunker", "rates", "fx"):
        assert getattr(a.by_driver_usd_m, driver) == pytest.approx(getattr(b.by_driver_usd_m, driver), abs=1e-6)
    assert a.by_driver_usd_m.rates != 0 and a.by_driver_usd_m.freight != 0


def freight_fixture_change(anchor: float) -> float:
    """Weekly baseline change on the 6.14 freight fixture (one trade, November shipments) when the WCI moves from
    2,800 to 2,940 between the two scheduled baselines; returns the freight part in USD m."""
    from hlag_engine.contracts.exposure import ExposureRegister
    from hlag_engine.generator.calendar import horizon
    from tests.modules.helpers import load_fixture

    fx = load_fixture("freight_example.yaml", anchor={"WCI_SHA_RTM": anchor})
    store = loaded_store()
    store.conn.execute("UPDATE market_data SET value = 2940 WHERE series_id = 'WCI_SHA_RTM' AND date > ?", (PREV.isoformat(),))
    store.conn.commit()

    def register(profile, as_of, resolver, cash):
        start, end = horizon(as_of)
        return ExposureRegister(as_of=as_of, horizon_start=start, horizon_end=end, profile_version=profile.profile_version,
                                profile_hash=profile.content_hash, items=fx.exposures)

    engine = Engine(store, profile=fx.profile, clock=lambda: NOW, liquidity=PIN, register_builder=register)
    run_scheduled(engine, PREV, [], with_zim_pair=False)
    run_scheduled(engine, T0, [], with_zim_pair=False)
    change = baseline_change(engine, T0, BaselineChangeWindow.WEEKLY).change
    d = change.by_driver_usd_m
    assert d.bunker == 0 and d.rates == 0 and d.fx == 0
    return d.freight


@pytest.mark.parametrize("anchor", [2800.0, 2600.0])
def test_freight_part_scales_with_the_anchor_as_the_formula_implies(anchor):
    """P87: freight's P0 is tied to the anchor-period index level, so a WCI move of dI is worth P0 x factor x dI / A
    per TEU (rho = I / A - 1, 6.5). On the fixture, with a flat base-pass path (pricing lags do not matter):
    spot 4,000 TEU x beta 1.0 x 30 days + index-linked 1,000 TEU x kappa 0.8 x 30 days + cohort A (reset 15 Nov)
    2,500 TEU x gamma 0.5 x 16 days = 164,000 TEU-equivalents; freight part = 1,400 x 164,000 x dI / A
    = USD 229.6m x 140 / A (all collections land before the pinned minimum on 31 Oct 2027)."""
    expected = 1400 * (30 * (4000 * 1.0 + 1000 * 0.8) + 16 * 2500 * 0.5) * 140 / anchor / 1e6
    assert freight_fixture_change(anchor) == pytest.approx(expected, abs=1e-6)


def test_freight_part_is_inversely_proportional_to_the_anchor():
    # equal up to the cent rounding of the stored lines (a few cents on USD 12m)
    assert freight_fixture_change(2600.0) / freight_fixture_change(2800.0) == pytest.approx(2800 / 2600, rel=1e-7)


def test_daily_job_first_day(monkeypatch):
    engine = Engine(loaded_store(), profile=profile_from_dict(anchored_raw()), clock=lambda: NOW)
    out = run_daily(engine, T0, loader=None, preset_ids=[PresetId.EUR_RALLY])
    assert out["as_of"] == T0.isoformat() and out["scheduled"]["runs"]["eur_rally"]
    assert out["baseline_change"]["daily"] == {"skipped": "no scheduled baseline from an earlier date"}
    assert "market_monitor" in out["tier_a"] and out["scheduled"]["zim_pair"]


def test_cli_daily_wires_the_job(monkeypatch, tmp_path, capsys):
    from hlag_engine.data import cli
    import hlag_engine.engine.daily as daily

    calls = {}

    def fake(engine, as_of, loader, load_date=None, preset_ids=None):
        calls.update(as_of=as_of, loader=loader)
        return {"ok": True}

    monkeypatch.setattr(daily, "run_daily", fake)
    assert cli.main(["daily", "--db", str(tmp_path / "m.sqlite"), "--as-of", "2026-10-31", "--no-load"]) == 0
    assert calls == {"as_of": T0, "loader": None} and '"ok": true' in capsys.readouterr().out
