"""Methodology layer: the run breakdown adds up to the engine's own figures, and the descriptive data is tied to
real profile keys, series and playbook entries. Nothing here changes a calculation."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from hlag_engine.company_profile.resolve import RunContext
from hlag_engine.company_profile.schema import Source
from hlag_engine.contracts.enums import LineCode, PassType, Trade
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.engine import methodology as M
from hlag_engine.engine.run import Engine
from hlag_engine.engine.snapshot import build_snapshot
from hlag_engine.modules.bunker.module import NOTES as BUNKER_NOTES
from hlag_engine.modules.freight import FreightModule
from hlag_engine.modules.methodology import APPROXIMATION_LABEL, SymbolRef
from tests.engine.conftest import NOW, T0, loaded_store
from tests.engine.test_run import RED_SEA, anchored_raw
from hlag_engine.company_profile.schema import profile_from_dict

CENT = Decimal("0.01")


@pytest.fixture(scope="module")
def profile_c():
    return profile_from_dict(anchored_raw())


@pytest.fixture(scope="module")
def red_sea(profile_c):
    engine = Engine(loaded_store(), profile=profile_c, clock=lambda: NOW)
    return engine, engine.run_scenario(ScenarioParams(**RED_SEA), T0)


def totals(items, key=lambda i: (i["driver"], i["component"])):
    out = defaultdict(Decimal)
    for i in items:
        out[key(i)] += i["usd"]
    return out


def test_breakdown_adds_up_to_the_run_total_and_each_driver_bar(red_sea):
    _, res = red_sea
    b = M.breakdown(res)
    # The breakdown follows the stored line deltas (rounded to cents, 4.1), like the dashboard rows.
    assert sum(i["usd"] for i in b["items"]) == sum(p.delta_usd for p in res.pairs)
    by_driver = totals(b["items"], key=lambda i: i["driver"])
    for driver, bar in res.attribution.bars().items():
        if driver == "fx":  # FX also takes the per-line cent rounding: within half a cent per pair of the exact bar
            assert abs(by_driver[driver] - bar) <= CENT / 2 * len(res.pairs)
        else:
            assert by_driver[driver] == bar, driver


def test_driver_components_are_the_native_change_at_base_fx(red_sea):
    _, res = red_sea
    native = defaultdict(Decimal)
    for p in res.pairs:
        if p.ref.owner_spec.value != "fx":
            native[p.ref.owner_spec.value] += (p.b - p.a) * p.x
    by_driver = totals(M.breakdown(res)["items"], key=lambda i: i["driver"])
    for driver in ("freight", "bunker", "rates"):
        assert abs(by_driver[driver] - native[driver]) < Decimal("1e-6")


def test_every_component_is_declared_in_the_module_spec(red_sea):
    _, res = red_sea
    for item in M.breakdown(res)["items"]:
        assert item["component"] in {c.component_id for c in M.SPECS[item["driver"]].components}, item


def test_fuel_price_and_consumption_effects_add_up_to_the_purchase_change(red_sea):
    _, res = red_sea
    purchases = sum(p.b - p.a for p in res.pairs if p.ref.line_code is LineCode.FUEL_PURCHASE)
    t = totals(M.breakdown(res)["items"])
    assert abs(t[("bunker", "price_effect")] + t[("bunker", "consumption_effect")] - purchases) < Decimal("1e-6")
    assert t[("bunker", "price_effect")] < 0 and t[("bunker", "consumption_effect")] < 0  # price up, burn up (RED_SEA)


def test_no_consumption_effect_without_a_consumption_shock(profile_c):
    engine = Engine(loaded_store(), profile=profile_c, clock=lambda: NOW)
    res = engine.run_scenario(ScenarioParams(name="fuel", start_date=T0, source="custom", bunker={"vlsfo_shock_pct": 10,
                                                                                  "hsfo_shock_pct": 10}), T0)
    t = totals(M.breakdown(res)["items"])
    assert t[("bunker", "consumption_effect")] == 0 and t[("bunker", "price_effect")] < 0


def test_zero_shock_has_no_components(profile_c):
    engine = Engine(loaded_store(), profile=profile_c, clock=lambda: NOW)
    res = engine.run_scenario(ScenarioParams(name="Baseline", start_date=T0, source="custom"), T0)
    b = M.breakdown(res)
    assert b["items"] == [] and b["beyond_horizon"] == []


def test_freight_components_are_split_by_trade_and_fx_conversion_by_module(red_sea):
    _, res = red_sea
    items = M.breakdown(res)["items"]
    assert {i["detail"] for i in items if i["driver"] == "freight"} <= {t.value for t in Trade}
    assert {i["detail"] for i in items if i["component"] == "conversion"} <= {"freight", "bunker", "rates"}
    assert all(i["currency"] in M.FX_CODES + ("USD", "OTHER") for i in items)


def test_beyond_horizon_is_the_watch_list_change(red_sea):
    _, res = red_sea
    b = M.breakdown(res)
    usd = sum(Decimal(repr(w.amount)) for w in res.outputs[PassType.SCENARIO]["freight"].watch_list
              if w.currency.value == "USD") - sum(Decimal(repr(w.amount)) for w in
                                                  res.outputs[PassType.BASE]["freight"].watch_list
                                                  if w.currency.value == "USD")
    got = sum(i["usd"] for i in b["beyond_horizon"] if i["driver"] == "freight" and i["currency"] == "USD")
    assert abs(got - usd) < Decimal("1e-6")
    assert b["horizon_end"] == res.liquidity.paths.dates[-1].isoformat()


def test_classify_rates_and_fx_lines():
    assert M.classify("rates", LineCode.INTEREST_INCOME, "rates:deposit:EUR:1") == ("cash_side", "EUR")
    assert M.classify("rates", LineCode.INTEREST_EXPENSE, "rates:fixed:EUR:bond") == ("debt_side", "bond")
    assert M.classify("rates", LineCode.REFINANCING_COST, "rates:refi:2026-11") == ("refinancing", "new-issue interest")
    assert M.classify("fx", LineCode.HEDGE_SETTLEMENT, "fx:hedge:cad_operating_costs:fx:opex:CAD:2026-11-06") == \
        ("policy_hedge", "cad_operating_costs")
    assert M._hedged_currency("fx:hedge:inr_brl_obligations:fx:obligation:INR:1", "USD") == "INR"


@pytest.fixture(scope="module")
def described(profile_c):
    params = ScenarioParams(name="t", start_date=T0, source="custom", overrides={"freight.beta": 0.6})
    ctx = RunContext.for_scenario(profile_c, T0, params, ())
    snapshot = build_snapshot(loaded_store(), T0)
    return {d: M.describe(d, profile_c, ctx, snapshot, params) for d in M.SPECS}, profile_c


def test_every_assumption_and_param_symbol_is_a_profile_key(described):
    _, profile = described
    for spec in M.SPECS.values():
        assert set(spec.used_for) <= set(profile.parameters), spec.driver
        for f in (*spec.formulas, spec.approximation):
            for s in f.symbols:
                if s.ref is SymbolRef.PARAM:
                    keys = M._expand(s.key, spec.driver)
                    assert set(keys.values() if keys else [s.key]) <= set(profile.parameters), s.key


def test_describe_fills_current_values_and_override_status(described):
    out, _ = described
    freight = out["freight"]
    beta = next(a for a in freight["assumptions"] if a["key"] == "freight.beta")
    assert (beta["value"], beta["profile_value"], beta["layer"], beta["changed"]) == (0.6, 0.5, "override", True)
    index = next(s for f in freight["formulas"] for s in f["symbols"] if s["symbol"] == "I_k(d)")
    assert index["value"]["ASIA_EUROPE"] == 2800.0 and index["source"] == "Drewry WCI via Bloomberg Terminal"
    assert freight["approximation"]["label"] == APPROXIMATION_LABEL
    assert 3 <= len(freight["summary"]) <= 5


def test_disclosed_figures_carry_a_plain_source_name(described):
    out, _ = described
    p0 = next(a for a in out["freight"]["disclosed"] if a["key"] == "freight.p0.ASIA_EUROPE")
    assert p0["source"] == "Hapag-Lloyd Investor Report H1 2026, p. 10"
    assert all(a["source"] for a in out["freight"]["disclosed"])


def test_playbook_and_limitations_come_from_the_module(described):
    out, _ = described
    assert [p["entry_id"] for p in out["freight"]["playbook"]] == [p.entry_id for p in FreightModule().playbook()]
    assert out["bunker"]["limitations"][:len(BUNKER_NOTES)] == BUNKER_NOTES
    assert all(m["source"] for d in out.values() for m in d["market_inputs"])


def test_source_name():
    assert M.source_name(Source(report="Investor Report H1 2026", page="2, 10")) == \
        "Hapag-Lloyd Investor Report H1 2026, pp. 2, 10"
    assert M.source_name(Source(report="Hapag-Lloyd EUR Bond 2028 webpage", page="webpage")) == \
        "Hapag-Lloyd EUR Bond 2028 webpage"
    assert M.source_name(None) is None


def test_freight_composite_is_the_volume_weighted_index(profile_c):
    weights = M.composite_weights(profile_c)
    assert abs(sum(weights.values()) - 1) < 1e-12
    d1, d2 = date(2026, 10, 23), date(2026, 10, 30)
    history = {"WCI_SHA_RTM": [(d1, 100.0), (d2, 200.0)], "WCI_SHA_LAX": [(d1, 100.0), (d2, 100.0)],
               "WCI_RTM_NYC": [(d1, 100.0), (d2, 100.0)], "WCI_COMPOSITE": [(d2, 100.0)]}
    out = M.freight_composite(history, profile_c)
    assert [d for d, _ in out] == [d2]  # only dates every route has
    assert out[0][1] == pytest.approx(100 + 100 * weights["ASIA_EUROPE"])
