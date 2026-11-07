import pytest
from datetime import date
from decimal import Decimal as D

from treasury_sensitivity.engines.common import ScenarioRequest
from treasury_sensitivity.engines.fx import Hedge, Position, fx_cash_effect, match_positions
from treasury_sensitivity.market import to_usd_per_unit
from treasury_sensitivity.models import ValidationProblem

M = date(2027, 1, 29)


def pos(id="P1", amount="100", direction="payable", ccy="CAD", und="ops", mat=M):
    return Position(id=id, currency=ccy, direction=direction, amount_m=amount, underlying=und, maturity=mat)


def hdg(id="H1", n="60", side="buy_forward", ccy="CAD", und="ops", mat=M):
    return Hedge(id=id, currency=ccy, side=side, notional_m=n, underlying=und, maturity=mat, verified=True)


def test_sign_flips_for_receivables():
    pay = fx_cash_effect(D(10), D("0.5"), D(10), "payable")
    rec = fx_cash_effect(D(10), D("0.5"), D(10), "receivable")
    assert pay == D("-0.5") and rec == D("0.5")


def test_quote_inversion():
    assert to_usd_per_unit(D("1.25"), "unit_per_usd") == D("0.8")
    assert to_usd_per_unit(D("0.8"), "usd_per_unit") == D("0.8")
    with pytest.raises(ValidationProblem):
        to_usd_per_unit(D(0), "unit_per_usd")


def test_matching_basic():
    m = match_positions([pos()], [hdg()], currency="CAD", direction="payable")
    assert (m.gross, m.matched, m.residual) == (100, 60, 40)


@pytest.mark.parametrize("hedge,reason", [
    (hdg(side="sell_forward"), "does not offset"),
    (hdg(und="other"), "no exposure with the same underlying"),
    (hdg(mat=date(2027, 6, 1)), "no exposure with the same underlying"),
])
def test_ineligible_hedges_are_rejected_and_reported(hedge, reason):
    m = match_positions([pos()], [hedge], currency="CAD", direction="payable")
    assert m.matched == 0 and m.residual == 100
    assert any(reason in r["reason"] for r in m.rejected)


def test_duplicate_ids_counted_once_and_lookalikes_flagged():
    m = match_positions([pos()], [hdg("H1"), hdg("H1")], currency="CAD", direction="payable")
    assert m.matched == 60 and any(r["reason"] == "duplicate hedge id" for r in m.rejected)
    m = match_positions([pos()], [hdg("H1", "30"), hdg("H2", "30")], currency="CAD", direction="payable")
    assert m.matched == 60 and any("possible duplicate" in w for w in m.warnings)
    m = match_positions([pos("P1"), pos("P1")], [], currency="CAD", direction="payable")
    assert m.gross == 100 and any("Duplicate exposure id" in w for w in m.warnings)


def test_over_hedge_is_capped_and_flagged():
    m = match_positions([pos(amount="50")], [hdg(n="80")], currency="CAD", direction="payable")
    assert m.matched == 50 and m.residual == 0 and m.over_hedged == 30
    assert any("Over-hedged" in w for w in m.warnings)


def test_unverified_hedge_flagged_in_result(service):
    r = service.compute("fx")
    assert any("not verified live matched" in w for w in r.warnings)
    assert r.extra["matching"]["matched"] == "57.5"


def test_other_currencies_are_assumed_and_disclosures_separate(service):
    r = service.compute("fx")
    assert {o["currency"] for o in r.extra["other_currencies"]} == {"EUR", "CNY", "GBP"}
    assert all("ILLUSTRATIVE" in o["exposure_basis"] for o in r.extra["other_currencies"])
    eur = service.compute("fx", ScenarioRequest(options={"currency": "EUR"}))
    assert eur.extra["direction"] == "receivable" and any("ASSUMPTION" in w for w in eur.warnings)
    assert all(q.value is None and q.provenance.value == "UNAVAILABLE" for q in r.disclosed_reference)


def test_overrides_recalculate_and_validate(service):
    r = service.compute("fx", ScenarioRequest(overrides={"hedge_coverage": 0.5}))
    assert r.scenarios[-1].impact.value == -2.738      # residual 37.5 x 0.073
    r = service.compute("fx", ScenarioRequest(overrides={"gross_exposure": 100, "hedge_notional": 0}))
    assert r.scenarios[-1].impact.value == -7.3
    with pytest.raises(ValidationProblem):
        service.compute("fx", ScenarioRequest(overrides={"hedge_coverage": 0.5, "hedge_notional": 10}))
    with pytest.raises(ValidationProblem):
        service.compute("fx", ScenarioRequest(shocks=[25]))
    with pytest.raises(ValidationProblem):
        service.compute("fx", ScenarioRequest(overrides={"nonsense": 1}))


def test_receivable_mode_flips_sign(service):
    r = service.compute("fx", ScenarioRequest(mode="receivable", overrides={"gross_exposure": 10, "hedge_notional": 0}))
    assert r.scenarios[-1].impact.value > 0


def test_coverage_above_quota_warns(service):
    r = service.compute("fx", ScenarioRequest(overrides={"hedge_coverage": 0.9}))
    assert any("exceeds the disclosed hedging quota" in w for w in r.warnings)
