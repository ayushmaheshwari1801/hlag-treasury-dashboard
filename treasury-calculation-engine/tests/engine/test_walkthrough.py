"""Engine walkthrough (spec 4.9) with three toy lines, run through the real engine (P74).

Part a reproduces the two passes, pairing, every delta, the attribution and its exact sum, bunker's gross
adverse delta and the cumulative delta on 30 Nov. The path and status figures come with step 5b.
"""

from datetime import date
from decimal import Decimal

import pytest

from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import Currency, LineCode, PassType, ProvenanceLabel
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.data.store import Store
from hlag_engine.engine.run import Engine
from hlag_engine.modules.interface import ModuleOutput
from hlag_engine.modules.paths import ShapeSpec, shape_factor
from tests.engine.conftest import NOW, T0, rows_for

NOV30 = date(2026, 11, 30)
M = Decimal(1_000_000)


class Toy:
    line_codes: set = set()
    gross_codes: set = set()
    offset_codes: set = set()
    material_series: list = []
    required_params: list = []

    def build_exposures(self, ctx):
        return []

    def alert_rules(self):
        return []

    def playbook(self):
        return []

    def hedge_targets(self):
        return []

    def line(self, owner, code, ccy, sub_id, amount_m):
        """Amounts are written in USD m as in 4.9 and stored in full units."""
        return CashLine(sub_id=sub_id, date=NOV30, currency=ccy, amount=Decimal(str(amount_m)) * M, owner_spec=owner,
                        line_code=code, label=ProvenanceLabel.ASSUMPTION)


class ToyFreight(Toy):
    name = "freight"
    gross_codes = {LineCode.FREIGHT_REVENUE}

    def run(self, ctx, params, exposures, inputs):
        return ModuleOutput(cash_lines=[self.line("freight", LineCode.FREIGHT_REVENUE, Currency.USD, "freight:spot:AE", "100.00")])


class ToyBunker(Toy):
    name = "bunker"
    gross_codes = {LineCode.FUEL_PURCHASE}

    def run(self, ctx, params, exposures, inputs):
        f = shape_factor(ShapeSpec.for_driver(params, params.bunker.shape), NOV30)
        amount = Decimal("-40.00") * (1 + Decimal(str(params.bunker.vlsfo_shock_pct)) / 100 * Decimal(str(f)))  # USD m
        return ModuleOutput(cash_lines=[self.line("bunker", LineCode.FUEL_PURCHASE, Currency.USD, "bunker:month:2026-11", amount)])


class ToyRates(Toy):
    name = "rates"
    debt_side_codes = {LineCode.INTEREST_EXPENSE}
    cash_side_codes = {LineCode.INTEREST_INCOME}

    def run(self, ctx, params, exposures, inputs):
        return ModuleOutput()


class ToyFx(Toy):
    name = "fx"
    gross_codes = {LineCode.FX_CONVERSION_EFFECT}

    def run(self, ctx, params, exposures, inputs):
        return ModuleOutput(cash_lines=[self.line("fx", LineCode.FX_OPERATING_COST, Currency.EUR, "fx:eur_opex:2026-11", "-20.00")])


@pytest.fixture
def engine(profile):
    store = Store(":memory:", clock=lambda: NOW)
    store.upsert_market_rows(rows_for("FX_USD_PER_EUR", 1.15))
    return Engine(store, profile=profile, modules=[ToyFreight(), ToyBunker(), ToyRates(), ToyFx()], clock=lambda: NOW)


def params(**blocks):
    return ScenarioParams(name="walkthrough", start_date=T0, source="custom", **blocks)


def by_owner(result):
    return {p.ref.owner_spec.value: p for p in result.pairs}


def test_run_1_zero_shock(engine):
    result = engine.run_scenario(params(), T0)
    pairs = by_owner(result)
    assert sorted(l.amount_usd_base / M for l in result.base_lines) == [Decimal("-40.00"), Decimal("-23.00"), Decimal("100.00")]
    assert [l.amount_usd_scenario for l in result.scen_lines] == [l.amount_usd_base for l in result.base_lines]
    assert pairs["fx"].base.amount_usd_base / M == Decimal("-23.00")
    assert all(p.delta_usd == 0 and p.delta_native == 0 for p in result.pairs)
    assert result.attribution.total == 0 and all(v == 0 for v in result.attribution.bars().values())


def test_run_2_bunker_and_fx_shock(engine):
    shock = params(bunker={"vlsfo_shock_pct": 20, "hsfo_shock_pct": 20}, fx={"move_pct": {"EUR": 10}})
    result = engine.run_scenario(shock, T0)
    p = by_owner(result)
    rows = {
        "freight": ("100.00", "100.00", "1", "1", "100.00", "100.00", "0.00"),
        "bunker": ("-40.00", "-48.00", "1", "1", "-40.00", "-48.00", "-8.00"),
        "fx": ("-20.00", "-20.00", "1.15", "1.265", "-23.00", "-25.30", "-2.30"),
    }
    for owner, (a, b, x, y, ax, by, d) in rows.items():
        pair = p[owner]
        assert (pair.a / M, pair.b / M) == (Decimal(a), Decimal(b))
        assert (pair.x, pair.y) == (Decimal(x), Decimal(y))
        assert pair.base.amount_usd_base / M == Decimal(ax) and pair.scen.amount_usd_scenario / M == Decimal(by)
        assert pair.delta_usd / M == Decimal(d) and pair.delta_usd_exact / M == Decimal(d)
    att = result.attribution
    assert (att.freight / M, att.bunker / M, att.rates, att.fx / M) == (Decimal("0.00"), Decimal("-8.00"), Decimal(0), Decimal("-2.30"))
    assert att.total / M == Decimal("-10.30") and sum(att.bars().values()) == att.total  # exactly
    assert result.gross_adverse["bunker"].value / M == Decimal("8.00") and result.gross_adverse["bunker"].date == NOV30
    cumulative = result.record.results["cumulative_delta_usd"]
    assert list(cumulative) == ["2026-11-30"] and Decimal(cumulative["2026-11-30"]) / M == Decimal("-10.30")
    assert engine.replay_differences(result.run_id) == []
