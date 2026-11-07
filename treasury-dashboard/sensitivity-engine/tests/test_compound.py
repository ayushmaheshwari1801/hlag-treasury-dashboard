import pytest

from treasury_sensitivity.compound import CompoundRequest
from treasury_sensitivity.engines.common import ScenarioRequest
from treasury_sensitivity.materiality import ApprovedLimit
from treasury_sensitivity.models import Status, ValidationProblem


def test_default_compound_excludes_incompatible_components(service):
    c, _ = service.compound()
    included = {l.module for l in c.included}
    excluded = {l.module: l.reason for l in c.excluded}
    assert included == {"bunker", "freight"}
    assert "Horizon 91 days differs" in excluded["fx"]
    assert "Earnings proxy" in excluded["interest"]


def test_surcharge_overlap_is_removed_from_freight(service):
    c, _ = service.compound()
    # bunker -96 net of 20% recovery; recovery = 50 x 2.4 x 0.2 = 24, removed from the +753.6 freight gain
    assert c.overlap_adjustment.amount_usd_m == -24.0
    assert c.total_usd_m == pytest.approx(-96 + 753.616 - 24, abs=1e-3)      # sums unrounded values


def test_overlap_can_be_switched_off_and_is_noted(service):
    c, _ = service.compound(CompoundRequest(bunker_surcharge_in_freight_rate=False))
    assert c.overlap_adjustment is None and c.total_usd_m == pytest.approx(-96 + 753.616, abs=1e-3)
    assert any("double counted" in n for n in c.notes)


def test_no_adjustment_when_freight_falls(service):
    req = CompoundRequest(scenarios={"freight": ScenarioRequest(headline_shock=-8)})
    c, _ = service.compound(req)
    assert c.overlap_adjustment is None and c.total_usd_m == pytest.approx(-96 - 753.616, abs=1e-3)
    assert c.assessment.status == Status.CRITICAL


def test_matching_horizon_includes_fx_and_converts_mode_b(service):
    req = CompoundRequest(target_horizon_days=91, modules=["fx", "interest", "bunker"], scenarios={
        "interest": ScenarioRequest(mode="B", overrides={"principal_eur_m": 1000, "days_affected": 91}),
        "bunker": ScenarioRequest(overrides={"horizon_days": 91})})
    c, _ = service.compound(req)
    assert {l.module for l in c.included} == {"fx", "interest", "bunker"}
    eur = next(l for l in c.included if l.module == "interest")
    assert eur.amount_usd_m == pytest.approx(-1000 * 0.0025 * 91 / 365 * 1.063, abs=0.002)   # dated EUR/USD 1.063 (latest fixture)


def test_missing_component_is_excluded_not_zero(service):
    req = CompoundRequest(target_horizon_days=182, modules=["interest", "bunker"], scenarios={"interest": ScenarioRequest(mode="B")})
    c, _ = service.compound(req)
    assert next(l for l in c.excluded if l.module == "interest").reason.startswith("NOT ASSESSABLE")
    assert c.total_usd_m == -96.0


def test_nothing_compatible_is_not_assessable(service):
    c, _ = service.compound(CompoundRequest(modules=["interest"]))
    assert c.total_usd_m is None and c.assessment.status == Status.NOT_ASSESSABLE


def test_validation(service):
    with pytest.raises(ValidationProblem):
        service.compound(CompoundRequest(modules=["fx", "fx"]))
    with pytest.raises(ValidationProblem):
        service.compound(CompoundRequest(modules=["crypto"]))


def test_combined_status_respects_limit(service):
    c, _ = service.compound(CompoundRequest(modules=["bunker"]), scenario_req=ScenarioRequest(limit=ApprovedLimit(name="cash", amount=100)))
    assert c.assessment.limit_utilisation == pytest.approx(0.96) and c.assessment.status == Status.HIGH   # liquidity 1.92% and limit 96% are both HIGH
