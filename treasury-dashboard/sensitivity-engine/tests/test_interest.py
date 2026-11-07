import pytest

from treasury_sensitivity.engines.common import ScenarioRequest
from treasury_sensitivity.models import Status, ValidationProblem


def test_negative_shock_uses_disclosed_asymmetry(service):
    r = service.compute("interest", ScenarioRequest(headline_shock=-100))
    assert r.headline.impact.value == 21.5
    r = service.compute("interest", ScenarioRequest(headline_shock=100))
    assert r.headline.impact.value == -20.8


def test_mode_a_is_not_scaled_by_horizon(service):
    a = service.compute("interest", ScenarioRequest(overrides={"horizon_months": 3}))
    b = service.compute("interest", ScenarioRequest(overrides={"horizon_months": 12}))
    assert a.headline.impact.value == b.headline.impact.value == -5.2
    assert (a.horizon_days, b.horizon_days) == (91, 365)      # the horizon is a label for Mode A, not a multiplier


def test_beyond_100bps_warns_about_extrapolation(service):
    r = service.compute("interest", ScenarioRequest(headline_shock=150))
    assert any("extrapolation" in w for w in r.warnings)
    with pytest.raises(ValidationProblem):
        service.compute("interest", ScenarioRequest(headline_shock=250))


def test_mode_b_needs_principal_and_never_shows_zero(service):
    r = service.compute("interest", ScenarioRequest(mode="B"))
    assert r.headline.impact.value is None and r.headline.impact.provenance.value == "UNAVAILABLE"
    assert r.assessment.status == Status.NOT_ASSESSABLE
    assert any("debt schedule" in a.action for a in r.treasury_actions)


def test_mode_b_cash_interest(service):
    req = ScenarioRequest(mode="B", overrides={"principal_eur_m": 1000, "days_affected": 182, "hedged_fraction": 0.5})
    r = service.compute("interest", req)
    assert r.headline.impact.value == round(-1000 * 0.5 * 0.0025 * 182 / 365, 2)    # -0.62
    assert r.impact_type.value.endswith("ILLUSTRATIVE cash interest")
    dep = service.compute("interest", ScenarioRequest(mode="B", options={"position_type": "floating_deposit"},
                                                      overrides={"principal_eur_m": 1000, "days_affected": 182}))
    assert dep.headline.impact.value > 0                                             # deposits gain when rates rise


def test_mode_b_converts_with_dated_rate_for_status(service):
    r = service.compute("interest", ScenarioRequest(mode="B", overrides={"principal_eur_m": 100000, "days_affected": 365}))
    assert r.assessment.status in (Status.HIGH, Status.CRITICAL) and r.assessment.liquidity_ratio is not None


def test_mode_a_status_needs_an_approved_limit(service):
    from treasury_sensitivity.materiality import ApprovedLimit
    r = service.compute("interest")
    assert r.assessment.status == Status.NOT_ASSESSABLE
    r = service.compute("interest", ScenarioRequest(limit=ApprovedLimit(name="EaR", amount=6, currency="EUR")))
    assert r.assessment.limit_utilisation is not None and r.assessment.status == Status.HIGH   # 5.2 / 6 = 86.7%


def test_benchmark_choice_and_labels(service):
    r = service.compute("interest", ScenarioRequest(benchmark="ECB_ESTR"))
    assert r.market.series_id == "ECB_ESTR" and r.market.change_unit == "bps"
    assert any("not Euribor" in w for w in service.compute("interest").warnings)
    with pytest.raises(ValidationProblem):
        service.compute("interest", ScenarioRequest(benchmark="EURIBOR_6M"))


def test_conflicting_shock_inputs_rejected(service):
    with pytest.raises(ValidationProblem):
        service.compute("interest", ScenarioRequest(headline_shock=10, overrides={"shock_bps": 20}))
