"""run_scenario with the four real modules on the complete test profile; record, replay and zero shock."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hlag_engine.company_profile.scenario_validation import ScenarioValidationError
from hlag_engine.company_profile.schema import profile_from_dict
from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.enums import AdjustmentStatus, Currency, LineCode, OwnerSpec, PassType, Trade
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.data.ingest import load_api_history
from hlag_engine.engine.conversion import FxConversionError
from hlag_engine.engine.run import Engine
from hlag_engine.engine.snapshot import build_snapshot, default_as_of
from hlag_engine.generator.calendar import month_end
from tests.conftest import complete_raw
from tests.data.conftest import FakeSource
from tests.engine.conftest import LEVELS, NOW, T0, loaded_store

RED_SEA = dict(
    name="Red Sea re-escalation", start_date=date(2026, 11, 1), duration_days=182, source="preset",
    preset_id="red_sea_reescalation",
    freight={"rate_shock_pct": {"ASIA_EUROPE": 40}, "transit_delay_days": {"ASIA_EUROPE": 12}},
    bunker={"vlsfo_shock_pct": 15, "hsfo_shock_pct": 12, "consumption_uplift_pct": 30, "uplift_affected_share": 0.35},
    fx={"move_pct": {"EUR": 5}},
    rates={"rate_path": {"USD": [{"date": "2026-12-09", "step_bp": -25}]}},
)


def anchored_raw(raw=None):
    """The complete test profile with the anchor snapshot equal to the test market (5.5): A = 0."""
    raw = raw or complete_raw()
    for sid, entry in raw["anchor_snapshot"]["series"].items():
        if sid in LEVELS:
            entry["value"], entry["status"] = LEVELS[sid], "ok"
    return raw


@pytest.fixture(scope="module")
def profile_c():
    return profile_from_dict(anchored_raw())


@pytest.fixture
def engine(profile_c):
    return Engine(loaded_store(), profile=profile_c, clock=lambda: NOW)


@pytest.fixture
def red_sea(engine):
    return engine, engine.run_scenario(ScenarioParams(**RED_SEA), T0)


def test_record_and_lines_written_in_one_go(red_sea):
    engine, result = red_sea
    stored = engine.store.get_run_record(result.run_id)
    assert stored is not None and stored.run_id == "run_20261031_red_sea_re_escalation_1"
    lines = engine.store.get_cash_lines(result.run_id)
    assert len(lines) == len(result.base_lines) + len(result.scen_lines)
    assert {l.pass_type for l in lines} == {PassType.BASE, PassType.SCENARIO}
    assert all(l.run_id == result.run_id and l.fx_rate_base is not None and l.amount_usd_scenario is not None for l in lines)
    assert stored.status.value in {"LOW", "WATCH", "HIGH", "CRITICAL"}  # computed (step 5b replaced the placeholder)
    assert "incomplete_reasons" not in stored.results and stored.results["summary"]["run_id"] == result.run_id
    [audit] = engine.store.list_audit(action="run_completed")
    assert audit.object_id == result.run_id
    # direct emits lines only when the scenario states direct cash flows (tests/engine/test_direct_cash.py)
    assert {l.owner_spec for l in lines} == set(OwnerSpec) - {OwnerSpec.DIRECT}
    assert stored.market_snapshot["SOFR"].unit == "%" and stored.market_snapshot["WCI_SHA_RTM"].value == Decimal("2800.0")
    assert stored.registers_hash.startswith("sha256:") and stored.profile_hash == engine.profile.content_hash


def test_attribution_sums_exactly_and_gross_adverse(red_sea):
    _, result = red_sea
    att = result.attribution
    assert sum(att.bars().values()) == att.total
    assert att.total == sum(p.delta_usd_exact for p in result.pairs)
    assert abs(att.total - sum(p.delta_usd for p in result.pairs)) <= Decimal("0.01") * len(result.pairs)
    assert set(result.gross_adverse) == {"freight", "bunker", "rates", "fx"}
    assert result.gross_adverse["freight"].value > 0 and result.gross_adverse["bunker"].value > 0
    assert result.gross_adverse["fx"].value > 0  # EUR +5% on EUR outflows
    bars = result.record.results["attribution_full_horizon_usd_m"]
    assert bars["total"] == round(float(att.total) / 1e6, 3)


def test_order_and_volume_lines_feed_bunker(red_sea):
    _, result = red_sea
    for pass_type in (PassType.BASE, PassType.SCENARIO):
        outs = result.outputs[pass_type]
        assert list(outs) == ["freight", "bunker", "rates", "fx"]
        assert outs["freight"].volume_lines and all(v.pass_type is pass_type for v in outs["freight"].volume_lines)
    mfr = [l for l in result.scen_lines if l.line_code is LineCode.MFR_RECOVERY]
    assert mfr  # bunker read freight's scenario volume lines


def test_dates_are_snapped(red_sea):
    _, result = red_sea
    cutoff = T0 + timedelta(days=91)
    for line in result.base_lines + result.scen_lines:
        assert line.date <= cutoff or line.date == month_end(line.date)
    keys = [l.line_key for l in result.base_lines]
    assert len(keys) == len(set(keys))  # same key after snapping is merged


def test_replay_reproduces_every_result(red_sea):
    engine, result = red_sea
    assert engine.replay_differences(result.run_id) == []
    # replay reads the record, never the live market store
    engine.store.conn.execute("DELETE FROM market_data")
    assert engine.replay_differences(result.run_id) == []


def test_zero_shock_gives_exactly_zero_on_every_pair(engine):
    result = engine.run_scenario(ScenarioParams(**RED_SEA).zero_shock(), T0)
    assert result.pairs
    assert all(p.delta_native == 0 and p.delta_usd == 0 and p.delta_usd_exact == 0 for p in result.pairs)
    assert result.attribution.total == 0 and all(v == 0 for v in result.attribution.bars().values())
    assert all(g.value == 0 for g in result.gross_adverse.values())


def test_validation_error_writes_nothing(engine):
    bad = ScenarioParams(**{**RED_SEA, "freight": {"rate_shock_pct": {"ASIA_EUROPE": 500}}})
    with pytest.raises(ScenarioValidationError) as exc:
        engine.run_scenario(bad, T0)
    assert any("500" in p for p in exc.value.problems)
    assert engine.store.conn.execute("SELECT COUNT(*) FROM run_records").fetchone()[0] == 0


def test_missing_currency_stops_the_run(profile_c):
    engine = Engine(loaded_store(skip={"FX_USD_PER_BRL"}), profile=profile_c, clock=lambda: NOW)
    with pytest.raises(FxConversionError, match="BRL"):
        engine.run_scenario(ScenarioParams(**RED_SEA), T0)
    assert engine.store.conn.execute("SELECT COUNT(*) FROM cash_lines").fetchone()[0] == 0


def test_zim_switch_filled_from_profile(profile_c):
    raw = anchored_raw()
    raw["parameters"]["rates.zim_tranche_on"]["value"] = True
    engine = Engine(loaded_store(), profile=profile_from_dict(raw), clock=lambda: NOW)
    result = engine.run_scenario(ScenarioParams(**RED_SEA), T0)
    assert result.record.params.rates.zim_tranche_on is True
    assert [l for l in result.base_lines if l.line_code is LineCode.ACQUISITION_PAYMENT]
    plain = Engine(loaded_store(), profile=profile_c, clock=lambda: NOW).run_scenario(ScenarioParams(**RED_SEA), T0)
    assert plain.record.params.rates.zim_tranche_on is False


def test_missing_inputs_are_collected(v2_profile):
    """Profile v2 still has pending anchor values: freight cannot run and the INR/BRL hedges are skipped."""
    engine = Engine(loaded_store(), profile=v2_profile, clock=lambda: NOW)
    result = engine.run_scenario(ScenarioParams(**RED_SEA), T0)
    keys = {m["key"] for m in result.record.results["missing_inputs"]}
    assert {"fx.lock_rate_usd_per_unit.INR", "fx.lock_rate_usd_per_unit.BRL"} <= keys
    assert "anchor_snapshot.WCI_SHA_RTM" in keys  # freight's module error
    assert any(m["status"] == "module_error" for m in result.record.results["missing_inputs"])
    assert engine.store.get_run_record(result.run_id) is not None  # the run still completes (INCOMPLETE)


def test_material_series_status_and_stale_flag(profile_c):
    store = loaded_store()
    store.conn.execute("DELETE FROM market_data WHERE series_id = 'VLSFO_SIN' AND date > '2026-10-15'")
    store.conn.commit()
    engine = Engine(store, profile=profile_c, clock=lambda: NOW)
    result = engine.run_scenario(ScenarioParams(**RED_SEA), T0)
    assert "VLSFO_SIN" in result.record.results["material_series"]["stale"]
    assert result.record.market_snapshot["VLSFO_SIN"].stale is True


def test_unapproved_adjustments_only_in_auto_built_runs(profile_c):
    store = loaded_store()
    adj = Adjustment(adjustment_id="adj_1", param_key="bunker.scrubber_share", old_value=0.15, new_value=0.05,
                     step="large", effective_date=date(2026, 11, 1), review_date=date(2027, 4, 1), event_ids=["evt_1"],
                     justification="test", confidence=0.8, proposed_by="agent")
    store.save_adjustment(adj)
    engine = Engine(store, profile=profile_c, clock=lambda: NOW)
    auto = ScenarioParams(**{**RED_SEA, "source": "auto_built", "unapproved_adjustment_ids": ["adj_1"]})
    result = engine.run_scenario(auto, T0)
    assert result.record.flags.includes_unapproved_adjustments and result.record.adjustment_ids == ["adj_1"]
    assert result.record.adjustments[0].new_value == 0.05


def test_replay_uses_adjustments_as_they_were(profile_c):
    store = loaded_store()
    approved = Adjustment(adjustment_id="adj_2", param_key="bunker.scrubber_share", old_value=0.15, new_value=0.05,
                          step="large", effective_date=date(2026, 11, 1), review_date=date(2027, 4, 1),
                          event_ids=["evt_2"], justification="test", confidence=0.8, proposed_by="agent",
                          status="approved", approver="treasurer", decided_at=datetime(2026, 11, 1, tzinfo=UTC))
    store.save_adjustment(approved)
    engine = Engine(store, profile=profile_c, clock=lambda: NOW)
    result = engine.run_scenario(ScenarioParams(**RED_SEA), T0)
    store.save_adjustment(approved.model_copy(update={"status": AdjustmentStatus.REVOKED}))  # revoked afterwards
    assert engine.replay_differences(result.run_id) == []


def test_default_as_of_counts_filled_rows():
    """FRED's Brent is 6 days late; T0 stays at the load date and Brent shows as stale (P72)."""
    store = loaded_store(skip={"BRENT"})
    load_date = T0
    brent = FakeSource({"BRENT": [(load_date - timedelta(days=d), 80.0) for d in range(6, 40)]})
    load_api_history(store, [brent], load_date, series_ids=["BRENT"])
    assert store.get_market_row("BRENT", load_date).filled
    assert default_as_of(store) == load_date
    snap = build_snapshot(store, load_date)
    assert snap["BRENT"].stale and snap["BRENT"].days_since_print == 6 and snap["BRENT"].value_date == load_date - timedelta(days=6)
    assert not snap["SOFR"].stale


def test_default_as_of_is_the_earliest_series_end():
    store = loaded_store()
    store.conn.execute("DELETE FROM market_data WHERE series_id = 'ESTR' AND date > '2026-10-29'")
    store.conn.commit()
    assert default_as_of(store) == date(2026, 10, 29)


def test_no_randomness_two_runs_identical(profile_c):
    a = Engine(loaded_store(), profile=profile_c, clock=lambda: NOW).run_scenario(ScenarioParams(**RED_SEA), T0)
    b = Engine(loaded_store(), profile=profile_c, clock=lambda: NOW).run_scenario(ScenarioParams(**RED_SEA), T0)
    assert a.record == b.record and a.base_lines == b.base_lines and a.scen_lines == b.scen_lines


def test_eur_lines_converted_freight_eur_invoicing(red_sea):
    _, result = red_sea
    eur = [l for l in result.scen_lines if l.currency is Currency.EUR and l.owner_spec is OwnerSpec.FREIGHT]
    assert eur and all(l.fx_rate_base == Decimal("1.15") for l in eur)
    assert any(l.fx_rate_scenario == Decimal("1.2075") for l in eur)  # +5% from 1 Nov
    assert any(Trade.ASIA_EUROPE.value in l.sub_id for l in eur)
    assert not [l for l in result.scen_lines if l.owner_spec is OwnerSpec.FREIGHT and l.currency is Currency.CNY
                and Trade.ASIA_AMERICA.value not in l.sub_id]  # CNY invoicing on Asia-America only
