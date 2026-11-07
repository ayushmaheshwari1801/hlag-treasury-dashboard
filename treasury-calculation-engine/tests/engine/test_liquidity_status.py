"""Step 5b: liquidity path, anchor adjustment, status, desk exceptions and the run summary."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import yaml

from hlag_engine.company_profile.registers import load_registers
from hlag_engine.company_profile.resolve import RunContext
from hlag_engine.company_profile.schema import profile_from_dict
from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import BaselineTreatment, PassType, ScenarioStatus
from hlag_engine.contracts.exposure import ExposureRegister
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.engine.liquidity import LiquidityInputs, TreatmentFlags, from_profile, line_treatment, margin_test, tiers
from hlag_engine.engine.run import Engine
from hlag_engine.engine.status import load_tier_b, tier_b_status
from tests.engine.conftest import LEVELS, NOW, T0, loaded_store
from tests.engine.test_run import RED_SEA, anchored_raw
from tests.modules.helpers import FIXTURES, load_fixture

M = Decimal(1_000_000)


def usd_m(amount, spec: str) -> bool:
    places = len(spec.split(".")[1]) if "." in spec else 0
    return round(Decimal(str(amount)) / M, places) == Decimal(spec)


def fixed_liquidity(t1, t2, capex, reserve=750_000_000):
    return LiquidityInputs(t1_start=Decimal(t1), t2_start=Decimal(t2), t3_start=Decimal(0), reserve=Decimal(reserve),
                           ocf_daily=Decimal(0), capex=[(d, Decimal(a)) for d, a in capex])


def fixed_register(items):
    def build(profile, as_of, resolver, cash_at_t0):
        from hlag_engine.generator.calendar import horizon

        start, end = horizon(as_of)
        return ExposureRegister(as_of=as_of, horizon_start=start, horizon_end=end, profile_version=profile.profile_version,
                                profile_hash=profile.content_hash, items=items)
    return build


# ------------------------------------------------------------------ walkthrough (4.9): path and status


@pytest.fixture
def walkthrough():
    from tests.engine.test_walkthrough import ToyBunker, ToyFreight, ToyFx, ToyRates
    from hlag_engine.data.store import Store
    from tests.engine.conftest import rows_for

    raw = anchored_raw()
    raw["anchor_snapshot"]["series"]["FX_USD_PER_EUR"].update(value=1.15, status="ok")  # anchor = T0 (5.5)
    store = Store(":memory:", clock=lambda: NOW)
    store.upsert_market_rows(rows_for("FX_USD_PER_EUR", 1.15))
    return Engine(store, profile=profile_from_dict(raw), modules=[ToyFreight(), ToyBunker(), ToyRates(), ToyFx()],
                  clock=lambda: NOW, liquidity=fixed_liquidity(1_000_000_000, 200_000_000, [(date(2026, 12, 15), -150_000_000)]),
                  register_builder=fixed_register([]))


def wt_params(**blocks):
    return ScenarioParams(name="walkthrough", start_date=T0, source="custom", **blocks)


def test_walkthrough_baseline_path(walkthrough):
    result = walkthrough.run_scenario(wt_params(), T0)
    h = result.liquidity.base.headroom
    assert h[date(2026, 11, 1)] == Decimal("450000000") and h[date(2026, 12, 14)] == Decimal("450000000")
    assert h[date(2026, 12, 15)] == Decimal("300000000") and h[date(2027, 10, 31)] == Decimal("300000000")
    assert (result.liquidity.h_b, result.liquidity.h_b_date) == (Decimal("300000000"), date(2026, 12, 15))
    assert result.liquidity.h_s == result.liquidity.h_b and result.liquidity.c_pct == 0
    assert result.record.status is ScenarioStatus.LOW and result.summary.headroom.consumed_pct == 0
    assert result.liquidity.anchor.computed and not result.liquidity.anchor.total  # A = 0


def test_walkthrough_scenario_path_and_status(walkthrough):
    shock = wt_params(bunker={"vlsfo_shock_pct": 20, "hsfo_shock_pct": 20}, fx={"move_pct": {"EUR": 10}})
    result = walkthrough.run_scenario(shock, T0)
    h = result.liquidity.scen.headroom
    assert h[date(2026, 11, 29)] == Decimal("450000000")
    assert h[date(2026, 11, 30)] == Decimal("439700000.00")
    assert h[date(2026, 12, 15)] == Decimal("289700000.00")
    assert (result.liquidity.h_s, result.liquidity.h_s_date) == (Decimal("289700000.00"), date(2026, 12, 15))
    s = result.summary
    assert s.headroom.consumed_pct == 3.43 and s.status is ScenarioStatus.LOW and not s.opportunity
    assert s.headroom.first_breach_date is None and s.headroom.shortfall_tier.value == "within_operating_cash"
    g = result.gross_adverse["bunker"].value / result.liquidity.h_b * 100
    assert round(g, 2) == Decimal("2.67") and s.desk_exceptions == []
    assert walkthrough.replay_differences(result.run_id) == []


# ------------------------------------------------------------------ joint Red Sea test (14.3, corrected figures)


def joint_engine(capex=None):
    spec = yaml.safe_load((FIXTURES / "joint_red_sea.yaml").read_text())
    market = {sid: {"value": v, "unit": loaded_store().get_market_row(sid, T0 if sid not in
              ("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE", "VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM")
              else date(2026, 10, 29)).unit, "value_date": date(2026, 10, 29)} for sid, v in LEVELS.items()}
    fx = load_fixture(spec["base"], profile_values=spec["profile_values"], market=market)
    liq = spec["liquidity"]
    capex = capex if capex is not None else liq["capex"]
    engine = Engine(loaded_store(), profile=fx.profile, clock=lambda: NOW,
                    liquidity=fixed_liquidity(liq["t1"], liq["t2"], [tuple(c) for c in capex], liq["reserve"]),
                    register_builder=fixed_register(fx.exposures))
    return engine, fx, spec


@pytest.fixture(scope="module")
def joint():
    engine, fx, spec = joint_engine()
    return engine, engine.run_scenario(fx.scenario, T0), spec["expected"]


def test_joint_teu_transfer_and_fx(joint):
    _, result, exp = joint
    [vl] = result.outputs[PassType.SCENARIO]["freight"].volume_lines
    assert vl.teu == exp["teu_transfer"]
    eur = [l for l in result.base_lines + result.scen_lines if l.currency.value == "EUR"]
    assert eur and all(l.fx_rate_base == l.fx_rate_scenario == Decimal("1.15") for l in eur)
    assert result.attribution.fx == 0


def test_joint_cumulative_delta_and_attribution(joint):
    _, result, exp = joint
    cum = result.record.results["cumulative_delta_usd"]
    for d, spec in exp["cumulative_net_delta"].items():
        value = [Decimal(v) for k, v in sorted(cum.items()) if k <= d.isoformat()][-1]
        assert usd_m(value, spec), (d, value)
    bars = {**result.attribution.bars(), "total": result.attribution.total}
    for k, spec in exp["attribution"].items():
        assert usd_m(bars[k], spec), (k, bars[k])
    assert result.summary.attribution_usd_m.total == -37.56


def test_joint_headroom_status_and_desk_exception(joint):
    engine, result, exp = joint
    liq = result.liquidity
    assert usd_m(liq.base.headroom[date(2026, 12, 1)], exp["h_b"]["before"])
    assert usd_m(liq.h_b, exp["h_b"]["min"]) and liq.h_b_date == exp["h_b"]["min_date"]
    assert usd_m(liq.scen.headroom[date(2026, 12, 31)], exp["h_s_2026_12_31"])
    assert usd_m(liq.h_s, exp["h_s_min"]["value"]) and liq.h_s_date == exp["h_s_min"]["date"]
    s = result.summary
    assert f"{s.headroom.consumed_pct:.2f}" == exp["consumed_pct"] and s.status.value == exp["status"]
    [desk] = s.desk_exceptions
    assert desk.kind.value == "driver" and desk.driver.value == "freight"
    assert f"{desk.gross_adverse_usd_m:.2f}" == exp["desk_exceptions"]["freight"]["g"]
    assert f"{desk.pct_of_headroom:.1f}" == exp["desk_exceptions"]["freight"]["pct"]
    g_b = result.gross_adverse["bunker"].value
    assert usd_m(g_b, exp["bunker_g"]["g"]) and f"{float(g_b / liq.h_b * 100):.1f}" == exp["bunker_g"]["pct"]
    assert f"alert_{result.run_id}_desk_freight" in s.alert_ids
    assert result.liquidity.anchor.computed and not result.liquidity.anchor.total  # anchor = T0: A = 0
    assert engine.replay_differences(result.run_id) == []


def test_joint_breach_variant():
    engine, fx, spec = joint_engine(capex=[(date(2027, 9, 30), spec_capex := -920_000_000)])
    result = engine.run_scenario(fx.scenario, T0)
    exp = spec["expected"]["breach_variant"]
    assert usd_m(result.liquidity.h_b, exp["h_b_min"]) and usd_m(result.liquidity.h_s, exp["h_s_min"])
    assert result.summary.status.value == exp["status"] and result.summary.headroom.first_breach_date == date(2027, 9, 30)
    assert result.summary.headroom.shortfall_tier.value == "reserve_breached"  # T1 + T2 = 742.44 < 750
    assert spec_capex == spec["breach_variant_capex"]


# ------------------------------------------------------------------ status precedence (4.6)


@pytest.mark.parametrize(
    ("c", "baseline_breach", "scenario_breach", "proxy", "incomplete", "status", "flag"),
    [
        (Decimal("5"), False, False, False, [], ScenarioStatus.LOW, False),
        (Decimal("12"), False, False, False, [], ScenarioStatus.WATCH, False),
        (Decimal("30"), False, False, False, [], ScenarioStatus.HIGH, False),
        (Decimal("60"), False, False, False, [], ScenarioStatus.CRITICAL, False),
        (Decimal("5"), False, True, False, [], ScenarioStatus.CRITICAL, False),
        (None, True, False, False, [], ScenarioStatus.CRITICAL, False),  # baseline breach regardless
        (Decimal("5"), False, False, True, [], ScenarioStatus.WATCH, False),  # proxy raises LOW to WATCH
        (Decimal("5"), False, False, False, ["stale"], ScenarioStatus.INCOMPLETE, True),
        (Decimal("12"), False, False, False, ["stale"], ScenarioStatus.INCOMPLETE, True),
        (Decimal("30"), False, False, False, ["stale"], ScenarioStatus.HIGH, True),  # never replaces HIGH
        (Decimal("60"), False, False, False, ["stale"], ScenarioStatus.CRITICAL, True),
        (None, True, False, False, ["stale"], ScenarioStatus.CRITICAL, True),
        (Decimal("5"), False, False, True, ["stale"], ScenarioStatus.INCOMPLETE, True),
    ],
)
def test_status_precedence(c, baseline_breach, scenario_breach, proxy, incomplete, status, flag):
    result = tier_b_status(c, baseline_breach, scenario_breach, proxy, incomplete, load_tier_b())
    assert result.status is status and result.incomplete_data is flag


def test_opportunity_and_boundaries():
    t = load_tier_b()
    assert tier_b_status(Decimal("-10"), False, False, False, [], t).opportunity
    assert not tier_b_status(Decimal("-9.99"), False, False, False, [], t).opportunity
    assert tier_b_status(Decimal("10"), False, False, False, [], t).status is ScenarioStatus.WATCH
    assert tier_b_status(Decimal("25"), False, False, False, [], t).status is ScenarioStatus.HIGH
    assert tier_b_status(Decimal("50"), False, False, False, [], t).status is ScenarioStatus.CRITICAL


# ------------------------------------------------------------------ anchor adjustment (3.7, D69)


def test_anchor_adjustment_nonzero_and_split_sums():
    raw = anchored_raw()
    raw["anchor_snapshot"]["series"]["WCI_SHA_RTM"]["value"] = 2000.0  # T0 is 2,800: the index moved +40%
    raw["anchor_snapshot"]["series"]["VLSFO_SIN"]["value"] = 500.0
    engine = Engine(loaded_store(), profile=profile_from_dict(raw), clock=lambda: NOW)
    plain = Engine(loaded_store(), profile=profile_from_dict(anchored_raw()), clock=lambda: NOW)
    params = ScenarioParams(**RED_SEA)
    moved, flat = engine.run_scenario(params, T0, write=False), plain.run_scenario(params, T0, write=False)
    anchor = moved.liquidity.anchor
    assert anchor.computed and anchor.by_driver["freight"] and anchor.by_driver["bunker"]
    assert not anchor.by_driver["rates"] and not anchor.by_driver["fx"]
    assert sum(anchor.total.values()) == sum(sum(p.values()) for p in anchor.by_driver.values())
    # A never enters the scenario deltas: the scenario path stays the baseline plus the cumulative delta
    for d in moved.liquidity.paths.dates:
        assert moved.liquidity.paths.l_scen[d] - moved.liquidity.paths.l_base[d] == \
            sum((v for k, v in moved.liquidity.paths.delta.items() if k <= d), Decimal(0))
    headline = moved.liquidity.anchor_headline
    assert sum(headline.values()) == moved.liquidity.h_b - flat.liquidity.h_b
    assert moved.record.results["liquidity"]["anchor_adjustment"]["label"] == "ASSUMPTION"


def test_pending_anchor_values_make_the_run_incomplete(v2_profile):
    engine = Engine(loaded_store(), profile=v2_profile, clock=lambda: NOW)
    result = engine.run_scenario(ScenarioParams(**RED_SEA), T0, write=False)
    keys = {m["key"] for m in result.record.results["missing_inputs"]}
    assert "anchor_snapshot.WCI_SHA_RTM" in keys and not result.liquidity.anchor.computed
    assert result.summary.flags.incomplete_data
    assert result.summary.status in (ScenarioStatus.INCOMPLETE, ScenarioStatus.HIGH, ScenarioStatus.CRITICAL)


# ------------------------------------------------------------------ roll-forward, treatment, tiers, margin


def test_roll_forward_from_the_active_profile(real_profile):
    resolver = RunContext(real_profile, T0)
    inputs = from_profile(resolver)
    days, months = 123, 4  # 1 Jul - 31 Oct 2026; Jul, Aug, Sep and Oct month ends
    expected = (Decimal(3_150_000_000) + Decimal("4690607.73") * days
                + (Decimal(69_000_000) - Decimal(236_000_000)) / 181 * days
                - Decimal(1_302_682_360) / 12 * months - Decimal(42_500_000) * months)
    assert inputs.t1_start == expected
    rf = inputs.roll_forward
    assert rf["label"] == "DERIVED" and rf["days"] == days and rf["month_ends"] == months
    assert inputs.t2_start == Decimal(725_000_000) and inputs.reserve == Decimal(750_000_000)
    assert len(inputs.lease) == len(inputs.capex) == 12 and inputs.capex[0] == (date(2026, 11, 30), Decimal(-42_500_000))


def test_deposits_sized_on_rolled_forward_cash(profile_c_engine):
    from hlag_engine.generator.generate import generate

    engine = profile_c_engine
    result = engine.run_scenario(ScenarioParams(**RED_SEA), T0, write=False)
    t1 = result.liquidity.inputs.t1_start
    register = generate(engine.profile, T0, RunContext(engine.profile, T0), cash_at_t0=float(t1))
    deposits = [i for i in register.items if i.kind == "deposit"]
    assert sum(float(d.quantities["balance_usd"]) for d in deposits) == pytest.approx(float(t1) * 0.85)
    assert "rolled-forward cash" in deposits[0].sizing_note
    assert result.record.results["liquidity_inputs"]["roll_forward"]["label"] == "DERIVED"


@pytest.fixture
def profile_c_engine():
    return Engine(loaded_store(), profile=profile_from_dict(anchored_raw()), clock=lambda: NOW)


def line(code, sub_id="x"):
    return CashLine(sub_id=sub_id, date=date(2026, 11, 30), currency="USD", amount=Decimal("1.00"),
                    owner_spec="rates" if code in ("INTEREST_EXPENSE", "ACQUISITION_PAYMENT") else "fx",
                    line_code=code, label="ASSUMPTION")


def test_line_treatment_flags():
    off = TreatmentFlags(False, False, False)
    on = TreatmentFlags(True, True, True)
    assert line_treatment(line("INTEREST_EXPENSE"), off) is BaselineTreatment.BASE_AMOUNT
    assert line_treatment(line("INTEREST_EXPENSE"), on) is BaselineTreatment.DELTA_ONLY
    lease = line("INTEREST_EXPENSE", "rates:lease:USD:LEASE")
    assert line_treatment(lease, TreatmentFlags(False, True, False)) is BaselineTreatment.DELTA_ONLY  # D51
    assert line_treatment(lease, off) is BaselineTreatment.BASE_AMOUNT
    assert line_treatment(line("FX_TRANSACTION"), off) is BaselineTreatment.BASE_AMOUNT
    assert line_treatment(line("FX_TRANSACTION"), TreatmentFlags(False, False, True)) is BaselineTreatment.DELTA_ONLY
    assert line_treatment(line("ACQUISITION_PAYMENT"), on) is BaselineTreatment.BASE_AMOUNT  # P63


def test_availability_cut_and_restore(real_profile):
    registers = load_registers()
    adj = Adjustment(adjustment_id="adj_bank", param_key="liquidity.T1.BANK_A", old_value=1.0, new_value=0.0,
                     step="large", effective_date=date(2026, 12, 1), review_date=date(2027, 3, 1), event_ids=["evt_b"],
                     justification="bank failure", confidence=0.9, status="approved", approver="treasurer",
                     decided_at=datetime(2026, 11, 2, tzinfo=UTC), proposed_by="agent")
    resolver = RunContext(real_profile, T0, adjustments=(adj,), registers=registers)
    from hlag_engine.engine.liquidity import path_dates

    levels = {d: Decimal(3_000_000_000) for d in path_dates(T0)}
    inputs = fixed_liquidity(3_000_000_000, 725_000_000, [])
    t = tiers(levels, inputs, resolver, registers)
    assert t.t1[date(2026, 11, 30)] == Decimal(3_000_000_000)
    assert t.t1[date(2026, 12, 1)] == Decimal(3_000_000_000) * Decimal("0.80")  # BANK_A holds 20% of deposits
    assert t.t1[date(2027, 2, 28)] == Decimal(2_400_000_000)
    assert t.t1[date(2027, 3, 31)] == Decimal(3_000_000_000)  # restored at expiry (P78; review date snaps to month end)
    assert t.t2[date(2026, 12, 1)] == Decimal(725_000_000)  # T2 unaffected
    assert [e.get("cut", e.get("restored")) for e in t.cuts] == [Decimal(600_000_000), Decimal(600_000_000)]


def test_margin_test_shortfall():
    from hlag_engine.engine.liquidity import Tiers

    d1, d2 = date(2026, 11, 2), date(2026, 11, 3)
    t = Tiers({}, {}, {}, {d1: Decimal(100), d2: Decimal(100)}, {}, [])
    calls = [CashLine(sub_id="bunker:margin:fuel_all:1", date=d1, currency="USD", amount=Decimal("-60.00"),
                      owner_spec="bunker", line_code="MARGIN_CALL", label="ASSUMPTION", amount_usd_base=Decimal("-60.00"),
                      fx_rate_base=Decimal(1), fx_rate_scenario=Decimal(1), amount_usd_scenario=Decimal("-60.00")),
             CashLine(sub_id="bunker:margin:fuel_all:1", date=d2, currency="USD", amount=Decimal("-50.00"),
                      owner_spec="bunker", line_code="MARGIN_CALL", label="ASSUMPTION", amount_usd_base=Decimal("-50.00"),
                      fx_rate_base=Decimal(1), fx_rate_scenario=Decimal(1), amount_usd_scenario=Decimal("-50.00"))]
    result = margin_test(t, calls, "amount_usd_base", T0)
    assert result.lowest == Decimal("-10.00") and result.shortfall_date == d2 and not result.passed


def test_swap_run_carries_margin_test(profile_c_engine):
    hedge = HedgeTest(module="bunker", instrument="swap", target="fuel_all", coverage_pct=100, tenor_months=3,
                      start_date=date(2026, 11, 1))
    params = ScenarioParams(**{**RED_SEA, "bunker": {"vlsfo_shock_pct": -20, "hsfo_shock_pct": -20}}).model_copy(
        update={"hedge_tests": [hedge]})
    result = profile_c_engine.run_scenario(params, T0, write=False)
    margin = result.record.results["liquidity"]["margin_test"]
    assert set(margin) == {"base", "scenario"} and "lowest" in margin["scenario"]


def test_stale_material_series_is_a_data_desk_exception(profile_c_engine):
    store = profile_c_engine.store
    store.conn.execute("DELETE FROM market_data WHERE series_id = 'HSFO_RTM' AND date > '2026-10-10'")
    store.conn.commit()
    result = profile_c_engine.run_scenario(ScenarioParams(**RED_SEA), T0, write=False)
    data = [d for d in result.summary.desk_exceptions if d.kind.value == "data"]
    assert data and data[0].series == ["HSFO_RTM"]
    assert f"alert_{result.run_id}_desk_data" in result.summary.alert_ids
    assert result.summary.flags.incomplete_data
