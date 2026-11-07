"""Freight module: worked example 6.14 to the cent and the acceptance tests of 6.15."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from hlag_engine.company_profile.resolve import RunContext
from hlag_engine.contracts.enums import Currency, LineCode, PassType, ProvenanceLabel, Trade
from hlag_engine.contracts.exposure import ExposureItem
from hlag_engine.contracts.scenario import FxParams, ScenarioParams
from hlag_engine.modules.bunker import BunkerModule
from hlag_engine.modules.freight import FreightModule
from hlag_engine.modules.freight.checks import trade_bridge
from hlag_engine.modules.freight.presets import build_freight_reversal, build_history_replay
from hlag_engine.modules.interface import (
    ModuleInputError,
    ModuleInputs,
    ModuleRunContext,
    ProfileContext,
    SnapshotEntry,
)
from tests.conftest import T0
from tests.modules.helpers import (
    FIXTURES,
    cumulative,
    cumulative_on,
    deltas_by_date,
    gross_adverse,
    load_fixture,
    run_both,
    run_pass,
    usd_m,
)

FREIGHT = FreightModule()
NOV = [date(2026, 11, d) for d in range(1, 31)]
SHARES = {"spot": 4000, "index": 1000, "A": 2500, "B": 2500}


@pytest.fixture(scope="module")
def fx():
    return load_fixture("freight_example.yaml")


@pytest.fixture(scope="module")
def passes(fx):
    return run_both(FREIGHT, fx)


def line(output, sub_id, when, currency=Currency.USD):
    found = [l for l in output.cash_lines if l.sub_id == sub_id and l.date == when and l.currency is currency]
    return found[0].amount if found else Decimal(0)


def class_rates(output, d: date, lag: int) -> list[Decimal]:
    """Realised rate per class on shipment day d, read back from its cash line (cash on d + lag)."""
    when = d + timedelta(days=lag)
    suffix = ":arr"
    spot = line(output, "freight:spot:ASIA_EUROPE" + suffix, when) / SHARES["spot"]
    index = line(output, "freight:index:ASIA_EUROPE" + suffix, when) / SHARES["index"]
    a = line(output, "freight:contract:ASIA_EUROPE:A" + suffix, when) / SHARES["A"]
    leaked = line(output, "freight:leaked:ASIA_EUROPE:B" + suffix, when)
    leaked_teu = Decimal(250) if leaked else Decimal(0)
    b = line(output, "freight:contract:ASIA_EUROPE:B" + suffix, when) / (SHARES["B"] - leaked_teu)
    return [spot, index, a, b]


# ------------------------------------------------------------------ worked example 6.14


def test_rates_by_class_and_day(fx, passes):
    base, scen = passes
    exp = fx.expected["rates_usd_per_teu"]
    for d, rates in exp["base"].items():
        assert class_rates(base, d, 49) == [Decimal(r) for r in rates]
    for d, rates in exp["scenario"].items():
        assert class_rates(scen, d, 61) == [Decimal(r) for r in rates]


def test_revenue_per_day(fx, passes):
    for pass_name, output in zip(("base", "scenario"), passes):
        daily = output.diagnostics["daily_revenue_usd"]
        for d, spec in fx.expected["revenue_per_day"][pass_name].items():
            assert usd_m(daily[d], spec), (pass_name, d, daily[d])


def test_leakage_on_cohort_b_only(fx, passes):
    _, scen = passes
    exp = fx.expected["leakage"]
    events = scen.diagnostics["leakage_events"]
    assert [(e["cohort"], e["date"], e["direction"]) for e in events] == [(exp["cohort"], exp["date"], exp["direction"])]
    leaked = line(scen, "freight:leaked:ASIA_EUROPE:B:arr", exp["date"] + timedelta(days=61))
    assert leaked == Decimal(exp["leaked_teu_per_day"]) * 1960


def test_november_revenue_and_delta(fx, passes):
    base, scen = passes
    total_b = sum(base.diagnostics["daily_revenue_usd"].values())
    total_s = sum(scen.diagnostics["daily_revenue_usd"].values())
    assert usd_m(total_b, fx.expected["november_revenue_base"])
    assert usd_m(total_s, fx.expected["november_revenue_scenario"])
    assert usd_m(total_s - total_b, fx.expected["revenue_delta"])
    # stored lines, to the cent
    assert sum(l.amount for l in base.cash_lines) == Decimal("420000000.00")
    assert sum(l.amount for l in scen.cash_lines) == Decimal("435960000.00")


def test_delta_parts(fx, passes):
    base, scen = passes

    def total(output, prefix):
        return sum(l.amount for l in output.cash_lines if l.sub_id.startswith(prefix))

    parts = fx.expected["revenue_delta_parts"]
    assert usd_m(total(scen, "freight:contract:ASIA_EUROPE:A") - total(base, "freight:contract:ASIA_EUROPE:A"),
                 parts["cohort_a"])
    assert usd_m(total(scen, "freight:spot") - total(base, "freight:spot"), parts["spot"])
    # leakage gain: the leaked TEU's spot revenue minus what they would have earned on contract
    leak = total(scen, "freight:leaked") - Decimal(250 * 1400 * 2)
    assert usd_m(leak, parts["leakage"])
    assert total(scen, "freight:index") == total(base, "freight:index")


def test_realised_average_rate(fx, passes):
    value = passes[1].metrics["realised_rate_usd_per_teu.ASIA_EUROPE"].value
    assert round(Decimal(str(value)), 2) == Decimal(fx.expected["realised_rate_scenario_usd_per_teu"])


def test_cash_dates(fx, passes):
    base, scen = passes
    assert [min(l.date for l in base.cash_lines), max(l.date for l in base.cash_lines)] == fx.expected["base_cash_dates"]
    assert [min(l.date for l in scen.cash_lines), max(l.date for l in scen.cash_lines)] == fx.expected["scenario_cash_dates"]


def test_cumulative_cash_delta_and_gross_adverse(fx, passes):
    cum = cumulative(deltas_by_date(*passes))
    for d, spec in fx.expected["cumulative_cash_delta"].items():
        assert usd_m(cumulative_on(cum, d), spec), (d, cumulative_on(cum, d))
    assert cumulative_on(cum, date(2026, 12, 31)) == Decimal("-168000000.00")
    assert cumulative_on(cum, date(2027, 1, 18)) == Decimal("-165200000.00")
    worst, when = gross_adverse(cum)
    assert usd_m(worst, fx.expected["gross_adverse"]["value"]) and when == fx.expected["gross_adverse"]["date"]


def test_volume_lines(fx, passes):
    for pass_name, output in zip(("base", "scenario"), passes):
        [vl] = output.volume_lines
        assert vl.teu == fx.expected["volume_lines_teu"][pass_name] and vl.month == date(2026, 11, 1)
        assert vl.pass_type is PassType(pass_name) and vl.label is ProvenanceLabel.ASSUMPTION


def test_delay_affected_share_50():
    fx50 = load_fixture("freight_example.yaml", profile_values={"freight.delay_affected_share": 0.5})
    cum = cumulative(deltas_by_date(*run_both(FREIGHT, fx50)))
    assert usd_m(cumulative_on(cum, date(2026, 12, 31)), fx50.expected["delay_share_50_cumulative_2026_12_31"])


def test_delay_affected_share_zero_leaves_cash_dates():
    fx0 = load_fixture("freight_example.yaml", profile_values={"freight.delay_affected_share": 0.0})
    base, scen = run_both(FREIGHT, fx0)
    assert {l.date for l in scen.cash_lines} == {l.date for l in base.cash_lines}
    assert all(l.sub_id.endswith(":dep") for l in scen.cash_lines)
    cum = cumulative(deltas_by_date(base, scen))
    assert min(cum.values()) >= 0 and usd_m(cumulative_on(cum, date(2027, 1, 30)), "15.96")


# ------------------------------------------------------------------ acceptance 6.15


def test_h1_bridge():
    data = yaml.safe_load((FIXTURES / "module_backtests.yaml").read_text())["freight_bridge"]
    r = trade_bridge(data["current"], data["prior"], data["reported_change_m"])
    assert round(r.volume_effect, 1) == 107.0 and round(r.rate_effect, 1) == -11.9
    assert abs(r.residual) <= 5 and r.volume_effect > 0 and r.rate_effect < 0


def test_zero_shock_gives_zero_delta(fx):
    base, scen = run_both(FREIGHT, fx, fx.scenario.zero_shock())
    assert base.cash_lines == scen.cash_lines and base.volume_lines[0].teu == scen.volume_lines[0].teu


def test_downside_leakage_and_cohort_a_reprices_to_1120(fx):
    down = fx.scenario.model_copy(update={"freight": fx.scenario.freight.model_copy(
        update={"rate_shock_pct": {Trade.ASIA_EUROPE: -40.0}})})
    _, scen = run_both(FREIGHT, fx, down)
    events = scen.diagnostics["leakage_events"]
    assert [(e["cohort"], e["direction"]) for e in events] == [("ASIA_EUROPE:B", "down")]
    assert events[0]["date"] == date(2026, 11, 29)  # spot 840 is 40% below cohort B's 1,400
    assert class_rates(scen, date(2026, 11, 15), 61)[2] == Decimal(1120)
    assert class_rates(scen, date(2026, 11, 29), 61)[0] == Decimal(840)


def _months_fixture(fx, months):
    items = [e for e in fx.exposures if e.kind != "volume"]
    for m in months:
        days = (date(m.year + (m.month == 12), m.month % 12 + 1, 1) - m).days
        items.append(ExposureItem(sub_id=f"freight:volume:ASIA_EUROPE:{m:%Y-%m}", owner_spec="freight",
                                  kind="volume", label="ASSUMPTION", sizing_note="test",
                                  quantities={"teu": 10000 * days, "daily_teu": 10000, "days": days},
                                  dates={"month": m}))
    return items


def test_contract_not_repriced_before_reset_and_leaked_not_repriced_at_reset(fx):
    months = [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1), date(2027, 3, 1)]
    exposures = _months_fixture(fx, months)
    persistent = fx.scenario.model_copy(update={"duration_days": None})
    scen = run_pass(FREIGHT, fx, persistent, PassType.SCENARIO, exposures=exposures)
    before, at = date(2027, 2, 28), date(2027, 3, 1)
    b_before = line(scen, "freight:contract:ASIA_EUROPE:B:arr", before + timedelta(days=61)) / 2250
    b_at = line(scen, "freight:contract:ASIA_EUROPE:B:arr", at + timedelta(days=61)) / 2250
    leaked_at = line(scen, "freight:leaked:ASIA_EUROPE:B:arr", at + timedelta(days=61)) / 250
    assert b_before == Decimal(1400) and b_at == Decimal(1680)
    assert leaked_at == Decimal(1960)  # stays at spot (P39), never repriced at reset


@pytest.fixture(scope="module")
def full():
    """The complete test profile through the generator, all four trades."""
    from tests.conftest import complete_raw
    from hlag_engine.company_profile.schema import profile_from_dict

    profile = profile_from_dict(complete_raw())
    market = {sid: SnapshotEntry(100.0, date(2026, 10, 29), "USD/FEU")
              for sid in ("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE")}
    market["FX_USD_PER_EUR"] = SnapshotEntry(1.15, date(2026, 10, 30), "USD per unit")
    market["FX_USD_PER_CNY"] = SnapshotEntry(0.14, date(2026, 10, 30), "USD per unit")
    exposures = FREIGHT.build_exposures(ProfileContext(RunContext(profile, T0)))
    return profile, market, exposures


def run_full(full, params, pass_type):
    profile, market, exposures = full
    ctx = ModuleRunContext(ProfileContext(RunContext.for_scenario(profile, T0, params)), pass_type, market)
    return FREIGHT.run(ctx, params.zero_shock() if pass_type is PassType.BASE else params, exposures, ModuleInputs())


def test_full_profile_zero_shock_and_trade_isolation(full):
    params = ScenarioParams(name="AE +40", start_date=date(2026, 11, 1), source="custom",
                            freight={"rate_shock_pct": {"ASIA_EUROPE": 40}})
    base = run_full(full, params, PassType.BASE)
    zero = run_full(full, params.zero_shock(), PassType.SCENARIO)
    assert base.cash_lines == zero.cash_lines
    scen = run_full(full, params, PassType.SCENARIO)
    others_b = [l for l in base.cash_lines if "ASIA_EUROPE" not in l.sub_id]
    others_s = [l for l in scen.cash_lines if "ASIA_EUROPE" not in l.sub_id]
    assert others_b == others_s and others_b
    assert {l.label for l in scen.cash_lines} == {ProvenanceLabel.ASSUMPTION}
    assert len(scen.volume_lines) == 4 * 12
    assert {l.currency for l in scen.cash_lines} == {Currency.USD, Currency.EUR, Currency.CNY}
    assert not [l for l in scen.cash_lines if l.date > date(2027, 10, 31)] and scen.watch_list


def test_non_usd_share_on_fixture(fx):
    fx5 = load_fixture("freight_example.yaml", profile_values={"freight.non_usd_share.ASIA_EUROPE": 0.05},
                       market={"FX_USD_PER_EUR": {"value": 1.15, "unit": "USD per unit", "value_date": date(2026, 10, 30)}})
    base, _ = run_both(FREIGHT, fx5)
    eur = sum(l.amount for l in base.cash_lines if l.currency is Currency.EUR)
    usd = sum(l.amount for l in base.cash_lines if l.currency is Currency.USD)
    assert abs(eur * Decimal("1.15") - Decimal("21000000")) < Decimal("1.00")  # 5% of 420m at T0 FX
    assert abs(usd - Decimal("399000000")) < Decimal("1.00")
    # an EUR move changes no freight line (only the FX bar, in the conversion layer)
    fx_move = fx5.scenario.zero_shock().model_copy(update={"fx": FxParams(move_pct={Currency.EUR: 10.0})})
    b2, s2 = run_both(FREIGHT, fx5, fx_move)
    assert b2.cash_lines == s2.cash_lines


def test_volume_shock_reduces_volume_lines_and_bunker_reads_them():
    fx = load_fixture("bunker_example.yaml")
    shocked = fx.scenario.model_copy(update={"freight": fx.scenario.freight.model_copy(
        update={"volume_shock_pct": {Trade.ASIA_EUROPE: -10.0}})})
    fb, fs = run_both(FREIGHT, fx, shocked)
    assert fs.volume_lines[0].teu == Decimal("270000.0") and fb.volume_lines[0].teu == Decimal("300000.0")
    bunker = BunkerModule()
    s_full = run_pass(bunker, fx, fx.scenario, PassType.SCENARIO, inputs=ModuleInputs(tuple(run_both(FREIGHT, fx)[1].volume_lines)))
    s_cut = run_pass(bunker, fx, shocked, PassType.SCENARIO, inputs=ModuleInputs(tuple(fs.volume_lines)))
    full_mfr = s_full.metrics["mfr_change_usd"].value
    assert s_cut.metrics["mfr_change_usd"].value == pytest.approx(full_mfr * 0.9)


def test_wci_halved_and_teu_tteu_mixup(fx, passes):
    assert passes[0].metrics["index_level_usd_per_teu.ASIA_EUROPE"].value == 1400.0
    bad = load_fixture("freight_example.yaml", profile_values={"freight.h1_volume_teu.ASIA_EUROPE": 1866})
    with pytest.raises(ModuleInputError, match="TEU vs TTEU"):
        run_both(FREIGHT, bad)
    teu = load_fixture("freight_example.yaml", market={"WCI_SHA_RTM": {"value": 1400.0, "unit": "USD/TEU",
                                                                        "value_date": date(2026, 10, 29)}})
    with pytest.raises(ModuleInputError, match="USD/FEU"):
        run_both(FREIGHT, teu)


def _wci_history():
    out = {}
    for i, sid in enumerate(("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE")):
        start = date(2026, 3, 5)
        out[sid] = [(start + timedelta(weeks=w), 2000.0 + 40 * w * (1 if i != 2 else -1)) for w in range(36)]
    return out


def test_reversal_preset_stores_numbers():
    params = build_freight_reversal(_wci_history(), T0, date(2026, 11, 1))
    shocks = params.freight.rate_shock_pct
    assert Trade.EUROPE_AMERICA not in shocks  # that route fell; no reversal
    now, then = 2000 + 40 * 34, 2000 + 40 * 8  # last print on or before T0 and T0 - 26 weeks
    assert shocks[Trade.ASIA_EUROPE] == pytest.approx(-0.5 * (now - then) / now * 100)
    assert params.default_shape.value == "ramp" and params.ramp_or_revert_weeks == 12 and params.duration_days is None
    assert ScenarioParams.model_validate_json(params.model_dump_json()) == params


def test_replay_preset_stores_path_and_runs_without_history(fx):
    params = build_history_replay(_wci_history(), date(2026, 3, 5), date(2026, 6, 4), date(2026, 10, 31))
    path = params.freight.rate_shock_path[Trade.ASIA_EUROPE]
    assert len(path) == 13 and path[0].date == date(2026, 11, 7) and path[0].pct == pytest.approx(2.0)
    assert params.freight.history_replay.start == date(2026, 3, 5)
    replay = params.model_copy(update={"freight": params.freight.model_copy(
        update={"rate_shock_path": {Trade.ASIA_EUROPE: path}})})
    assert not fx.history  # no history in the run context
    base, scen = run_both(FREIGHT, fx, replay)
    again = run_both(FREIGHT, fx, replay)[1]
    assert scen.cash_lines == again.cash_lines and scen.cash_lines != base.cash_lines


def test_cash_after_horizon_goes_to_watch_list(fx):
    base = run_pass(FREIGHT, fx, fx.scenario, PassType.BASE, exposures=_months_fixture(fx, [date(2027, 10, 1)]))
    assert base.watch_list and all(w.date > date(2027, 10, 31) for w in base.watch_list)
    assert all(l.date <= date(2027, 10, 31) for l in base.cash_lines)
    assert "watch_list_usd" in base.metrics


def test_notes_and_metrics(passes):
    base, _ = passes
    assert any("1,065m" in n for n in base.notes) and any("15%" in n for n in base.notes)
    assert base.metrics["index_move_since_anchor_pct"].value == 0  # anchor = T0
    assert base.metrics["rate_locked_share.ASIA_EUROPE"].value == pytest.approx(0.5)


def test_collection_lag_rounds_to_nearest_day():
    """P64: 46.7 days of DSO collects on shipment day + 47, not + 46."""
    lag = load_fixture("freight_example.yaml", profile_values={"freight.collection_lag_days": 46.7})
    base, _ = run_both(FREIGHT, lag)
    assert min(l.date for l in base.cash_lines) == date(2026, 11, 1) + timedelta(days=47)
    low = load_fixture("freight_example.yaml", profile_values={"freight.collection_lag_days": 46.4})
    assert min(l.date for l in run_both(FREIGHT, low)[0].cash_lines) == date(2026, 11, 1) + timedelta(days=46)
