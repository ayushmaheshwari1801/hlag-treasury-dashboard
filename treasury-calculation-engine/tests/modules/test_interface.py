"""Both modules implement the DriverModule interface (5.1) and declare their codes, rules,
playbook entries and hedge targets."""

import pytest

from hlag_engine.contracts.alert_rule import AlertRule, OnSevere
from hlag_engine.contracts.enums import LineCode
from hlag_engine.modules.bunker import BunkerModule
from hlag_engine.modules.freight import FreightModule
from hlag_engine.modules.fx import FxModule
from hlag_engine.modules.rates import RatesModule
from hlag_engine.modules.interface import DriverModule


@pytest.mark.parametrize("module", [FreightModule(), BunkerModule(), RatesModule(), FxModule()])
def test_implements_interface(module):
    assert isinstance(module, DriverModule)
    if module.name == "fx":  # G_fx is the engine's conversion effect, an aggregation code (4.3)
        assert module.gross_codes == {LineCode.FX_CONVERSION_EFFECT} and module.offset_codes <= module.line_codes
    else:
        assert module.gross_codes | module.offset_codes | getattr(module, "excluded_codes", set()) == module.line_codes
    assert not module.gross_codes & module.offset_codes
    for rule in module.alert_rules():
        assert isinstance(rule, AlertRule) and rule.module.value == module.name
    ids = [e.entry_id for e in module.playbook()]
    assert ids and len(ids) == len(set(ids)) and all(i.startswith(module.name + ".") for i in ids)


def test_freight_declarations():
    m = FreightModule()
    assert m.line_codes == {LineCode.FREIGHT_REVENUE} and m.offset_codes == set()
    assert m.material_series == ["WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE"]
    assert m.hedge_targets() == []
    rules = {r.rule_id: r for r in m.alert_rules()}
    assert rules["freight.route_move.wci_sha_rtm"].on_severe is OnSevere.REQUEST_AUTO_BUILD
    assert rules["freight.index_realised_gap.asia_europe"].thresholds.severe.value == 25


def test_bunker_declarations():
    m = BunkerModule()
    assert m.gross_codes == {LineCode.FUEL_PURCHASE}
    assert m.offset_codes == {LineCode.MFR_RECOVERY, LineCode.HEDGE_SETTLEMENT, LineCode.MARGIN_CALL}
    assert {t.target for t in m.hedge_targets()} == {"fuel_all", "vlsfo", "hsfo"}
    assert all(t.instruments == ("swap", "fixed_price_supply") for t in m.hedge_targets())
    rules = {r.rule_id: r for r in m.alert_rules()}
    assert rules["bunker.spread_move.sin"].thresholds.warning.value == 30
    assert rules["bunker.early_warning_brent"].thresholds.severe.value == 7
    assert rules["bunker.brent_vlsfo_divergence"].window == "4w"


def test_build_exposures_uses_generator(profile):
    from hlag_engine.company_profile.resolve import RunContext
    from hlag_engine.modules.interface import ProfileContext
    from tests.conftest import T0

    ctx = ProfileContext(RunContext(profile, T0))
    freight = FreightModule().build_exposures(ctx)
    bunker = BunkerModule().build_exposures(ctx)
    assert {e.kind for e in freight} == {"volume", "spot", "index", "contract"}
    assert {e.kind for e in bunker} == {"purchase"} and len(bunker) == 12 * 4


def test_rates_declarations(profile):
    m = RatesModule()
    assert m.debt_side_codes | m.cash_side_codes == m.gross_codes
    assert m.excluded_codes == {LineCode.DEBT_PRINCIPAL, LineCode.DEBT_DRAWDOWN, LineCode.ACQUISITION_PAYMENT}
    assert {t.target for t in m.hedge_targets()} == {"floating_debt_usd", "floating_debt_eur", "deposits_usd", "deposits_eur"}
    rules = {r.rule_id: r for r in m.alert_rules()}
    assert rules["rates.expectation_shift.ust_2y"].thresholds.severe.value == 30
    assert rules["rates.credit_spread.bb_oas"].on_severe is OnSevere.REQUEST_AUTO_BUILD
    assert rules["rates.benchmark_step.sofr"].on_severe is OnSevere.NONE and rules["rates.benchmark_step.sofr"].window == "1d"
    # catalogue entries of 8.10 (built in step 2)
    cat = profile.catalogue
    premium = cat["rates.new_issue_premium_bp"]
    assert (premium.steps.small, premium.steps.medium, premium.steps.large) == (25, 50, 100)
    assert tuple(premium.hard_limits) == (-100, 400)
    share = cat["rates.interest_bearing_cash_share"]
    assert (share.steps.small, share.steps.large) == (0.05, 0.20) and tuple(share.hard_limits) == (0.5, 1.0)
    assert cat["rates.zim_tranche_on"].value_mode.value == "switch"


def test_fx_declarations(profile):
    m = FxModule()
    assert m.own_codes == {LineCode.FX_OPERATING_COST, LineCode.DIVIDEND, LineCode.DEBT_PRINCIPAL, LineCode.FX_TRANSACTION}
    assert {t.target for t in m.hedge_targets()} == {"cad_operating_costs", "eur_operating_costs", "eur_dividend",
                                                      "eur_debt_principal", "inr_brl_obligations"}
    rules = {r.rule_id: r for r in m.alert_rules()}
    assert rules["fx.rate_move.eur"].thresholds.warning.value == 1.0 and rules["fx.rate_move.inr"].thresholds.severe.value == 1.5
    assert rules["fx.vol_regime.brl"].type.value == "vol_regime" and rules["fx.vol_regime.brl"].on_severe is OnSevere.NONE
    assert len(m.material_series) == 6
    # catalogue entries of 9B.9 (built in step 2)
    cat = profile.catalogue
    assert tuple(cat["fx.hedge_ratio.cad"].hard_limits) == (0, 0.8) and cat["fx.hedge_ratio.cad"].steps.large == 0.20
    assert tuple(cat["fx.hedge_ratio.eur_debt"].hard_limits) == (0, 1) and cat["fx.hedge_ratio.eur_debt"].steps.small == 0.10
    assert tuple(cat["fx.obligation_shift_months"].hard_limits) == (0, 24)
    assert cat["fx.inr_brl_obligations_on"].value_mode.value == "switch"
