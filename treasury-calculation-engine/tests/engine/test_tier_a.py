"""Tier A rule engine (4.5), alert lifecycle and auto-build requests."""

from datetime import date, timedelta

import pytest

from hlag_engine.company_profile.schema import profile_from_dict
from hlag_engine.contracts.enums import AlertSeverity, AlertState, ProvenanceLabel
from hlag_engine.data.series import SERIES
from hlag_engine.data.store import MarketRow, Store, StoreError
from hlag_engine.engine import alerts
from hlag_engine.engine.daily import evaluate_tier_a
from hlag_engine.engine.run import Engine
from hlag_engine.engine.tier_a import SeriesSource, evaluate_rule, volatility_regime
from hlag_engine.modules.bunker import BunkerModule
from hlag_engine.modules.freight import FreightModule
from hlag_engine.modules.fx import FxModule
from hlag_engine.modules.rates import RatesModule
from tests.engine.conftest import LEVELS, NOW, T0, loaded_store
from tests.engine.test_run import anchored_raw


def rule(module, rule_id):
    return next(r for r in module.alert_rules() if r.rule_id == rule_id)


def put(store, sid, points, confirmed=True):
    spec = SERIES[sid]
    store.upsert_market_rows([MarketRow(sid, d, v, spec.unit, "test", ProvenanceLabel.MARKET, NOW, False, 0, confirmed, "t://")
                              for d, v in points])


def weekly(end, values):
    n = len(values)
    return [(end - timedelta(weeks=n - 1 - i), v) for i, v in enumerate(values)]


def daily(end, values):
    n = len(values)
    return [(end - timedelta(days=n - 1 - i), v) for i, v in enumerate(values)]


def test_pct_move_severe_and_unconfirmed():
    store = Store(":memory:")
    put(store, "WCI_SHA_RTM", weekly(T0 - timedelta(days=7), [2000.0] * 30))
    put(store, "WCI_SHA_RTM", [(T0, 2500.0)], confirmed=False)
    o = evaluate_rule(rule(FreightModule(), "freight.route_move.wci_sha_rtm"), SeriesSource(store, T0))
    assert o.evaluable and o.severity is AlertSeverity.SEVERE and o.observed == pytest.approx(25.0)
    assert o.unconfirmed


def test_bp_move_on_benchmark():
    store = Store(":memory:")
    put(store, "SOFR", daily(T0, [4.0] * 30 + [4.15]))
    o = evaluate_rule(rule(RatesModule(), "rates.benchmark_step.sofr"), SeriesSource(store, T0))
    assert o.severity is AlertSeverity.WARNING and o.observed == pytest.approx(15.0)
    put(store, "UST_2Y", daily(T0, [3.8] * 30 + [4.15]))  # weekly window on a daily series: 35 bp
    o = evaluate_rule(rule(RatesModule(), "rates.expectation_shift.ust_2y"), SeriesSource(store, T0))
    assert o.severity is AlertSeverity.SEVERE and o.observed == pytest.approx(35.0)


def test_vol_multiple_fires_below_the_pct_threshold():
    store = Store(":memory:")
    calm = [1.1500 if i % 2 else 1.1505 for i in range(80)]
    put(store, "FX_USD_PER_EUR", daily(T0, calm + [calm[-1] * 1.008]))  # +0.8% < 1.0% but far beyond 2.5x vol
    o = evaluate_rule(rule(FxModule(), "fx.rate_move.eur"), SeriesSource(store, T0))
    assert o.observed < 1.0 and o.vol_multiple > 4 and o.severity is AlertSeverity.SEVERE


def test_spread_change_and_divergence():
    store = Store(":memory:")
    put(store, "VLSFO_SIN", weekly(T0, [600.0] * 10 + [640.0]))
    put(store, "HSFO_SIN", weekly(T0, [450.0] * 11))
    o = evaluate_rule(rule(BunkerModule(), "bunker.spread_move.sin"), SeriesSource(store, T0))
    assert o.observed == pytest.approx(40.0) and o.severity is AlertSeverity.WARNING
    store2 = Store(":memory:")
    put(store2, "VLSFO_SIN", weekly(T0, [600.0] * 10 + [690.0]))  # +15% over 4 weeks
    put(store2, "VLSFO_RTM", weekly(T0, [580.0] * 10 + [667.0]))
    put(store2, "BRENT", daily(T0, [80.0] * 60))  # Brent flat
    profile = profile_from_dict(anchored_raw())
    o = evaluate_rule(rule(BunkerModule(), "bunker.brent_vlsfo_divergence"), SeriesSource(store2, T0, profile))
    assert o.observed == pytest.approx(15.0) and o.severity is AlertSeverity.WARNING


def test_vol_regime_classes():
    calm = [100 * (1.001 if i % 2 else 0.999) for i in range(300)]
    from hlag_engine.engine.tier_a import Print
    from hlag_engine.data.series import Frequency

    prints = [Print(T0 - timedelta(days=300 - i), v) for i, v in enumerate(calm)]
    assert volatility_regime(prints, Frequency.DAILY)[0] == "calm"
    wild = calm + [100 * (1.02 if i % 2 else 0.98) for i in range(20)]
    prints = [Print(T0 - timedelta(days=len(wild) - i), v) for i, v in enumerate(wild)]
    assert volatility_regime(prints, Frequency.DAILY)[0] == "stressed"


def test_realised_rate_rules_not_evaluable_without_runs():
    o = evaluate_rule(rule(FreightModule(), "freight.index_realised_gap.asia_europe"), SeriesSource(Store(":memory:"), T0))
    assert not o.evaluable and "not enough" in o.reason


@pytest.fixture
def engine():
    return Engine(loaded_store(), profile=profile_from_dict(anchored_raw()), clock=lambda: NOW)


def test_lifecycle_and_one_auto_build_request_per_rule_per_day(engine):
    store = engine.store
    put(store, "WCI_SHA_RTM", [(T0, 3500.0)])  # +25% on the last weekly print (2,800 on T0 - 2 ... ) -> severe
    first = evaluate_tier_a(engine, T0)
    alert_id = f"alert_tierA_freight.route_move.wci_sha_rtm_{T0:%Y%m%d}"
    assert alert_id in first["opened"]
    again = evaluate_tier_a(engine, T0)
    assert alert_id in again["updated"] and not again["auto_build_requests"]
    requests = store.list_auto_build(source="tier_a")
    assert [r.dedupe_key for r in requests].count("freight.route_move.wci_sha_rtm") == 1
    assert requests[0].payload["series"] == ["WCI_SHA_RTM"] and requests[0].payload["alert_id"] == alert_id
    stored = store.get_alert(alert_id).alert
    assert stored.explanation.tier.value == "market" and stored.explanation.playbook_refs == ["freight.spike"]
    # the next week's print is flat: the rule is evaluable and quiet, so the alert clears
    put(store, "WCI_SHA_RTM", [(T0 + timedelta(days=7), 3500.0)])
    later = evaluate_tier_a(engine, T0 + timedelta(days=7))
    assert alert_id in later["cleared"] and store.get_alert(alert_id).alert.state is AlertState.CLEARED


def test_acknowledge_is_human_only_and_only_for_open_alerts(engine):
    put(engine.store, "WCI_SHA_RTM", [(T0, 3500.0)])
    evaluate_tier_a(engine, T0)
    alert_id = f"alert_tierA_freight.route_move.wci_sha_rtm_{T0:%Y%m%d}"
    acked = alerts.acknowledge_alert(engine.store, alert_id, "treasurer", NOW)
    assert acked.state is AlertState.ACKNOWLEDGED and acked.acknowledged_by == "treasurer"
    with pytest.raises(StoreError):
        alerts.acknowledge_alert(engine.store, alert_id, "treasurer", NOW)
    assert "acknowledge_alert" in alerts.HUMAN_ONLY_FUNCTIONS


def test_data_alerts(engine):
    store = engine.store
    store.add_queue_item(series_id="WCI_SHA_LAX", on=T0, value=6000.0, unit="USD/FEU", source_url="https://x",
                         submitted_by="agent", failed_checks=["outside_plausibility_band"], major_move_alert=True)
    store.conn.execute("DELETE FROM market_data WHERE series_id = 'HSFO_RTM' AND date > '2026-10-10'")
    store.conn.commit()
    result = evaluate_tier_a(engine, T0)
    assert f"alert_data_major_move_WCI_SHA_LAX_{T0:%Y%m%d}" in result["opened"]
    assert f"alert_data_stale_HSFO_RTM_{T0:%Y%m%d}" in result["opened"]
    major = store.get_alert(f"alert_data_major_move_WCI_SHA_LAX_{T0:%Y%m%d}").alert
    assert major.explanation.unconfirmed and "pending confirmation" in major.explanation.limitations[0]
    monitor = {m["series_id"]: m for m in result["market_monitor"]}
    assert monitor["SOFR"]["level"] == LEVELS["SOFR"] and "regime" in monitor["SOFR"]
