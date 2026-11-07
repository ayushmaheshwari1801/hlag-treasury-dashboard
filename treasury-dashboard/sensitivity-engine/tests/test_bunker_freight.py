import pytest

from treasury_sensitivity.engines.common import ScenarioRequest
from treasury_sensitivity.models import Status, ValidationProblem


def test_bunker_overrides_recalculate(service):
    r = service.compute("bunker", ScenarioRequest(overrides={"consumption_mt": 3, "recovery_fraction": 0.5, "cash_factor": 0.5}))
    assert r.headline.impact.value == -37.5          # -50 x 3 x 0.5 x 0.5
    r = service.compute("bunker", ScenarioRequest(headline_shock=-30))
    assert r.headline.impact.value == 57.6           # price fall is favourable


def test_bunker_disclosures_are_separate_and_never_summed(service):
    r = service.compute("bunker")
    labels = [q.label for q in r.disclosed_reference]
    assert "Middle East-related cash costs" in labels and all(q.provenance.value == "DISCLOSED HISTORICAL" for q in r.disclosed_reference)
    assert r.headline.impact.value == -96.0          # unchanged by disclosures
    assert any("not the company procurement price" in w for w in r.warnings)


def test_bunker_status_uses_comparable_liquidity(service):
    r = service.compute("bunker")
    assert (r.assessment.status, r.assessment.rule_id) == (Status.HIGH, "MAT-H1")
    assert r.assessment.liquidity_ratio == pytest.approx(96 / 5909)


def test_bounds_and_unknown_keys(service):
    for bad in ({"recovery_fraction": 1.5}, {"cash_factor": -0.1}, {"consumption_mt": -1}, {"bogus": 1}):
        with pytest.raises(ValidationProblem):
            service.compute("bunker", ScenarioRequest(overrides=bad))
    with pytest.raises(ValidationProblem):
        service.compute("bunker", ScenarioRequest(headline_shock=400))


def test_out_of_range_stress_rows_are_omitted_not_fatal(service):
    r = service.compute("bunker", ScenarioRequest(headline_shock=250))
    assert r.headline.impact.value == -480.0
    assert any("omitted" in w for w in r.warnings) and max(s.shock for s in r.scenarios) <= 300


def test_freight_overrides(service):
    r = service.compute("freight", ScenarioRequest(overrides={"transmission_k": 0.5, "cash_collection_c": 0.5, "volume_m_teu": 3.35}))
    assert r.headline.impact.value == 94.2           # 1406 x 3.35 x 8% x 0.5 x 0.5
    with pytest.raises(ValidationProblem):
        service.compute("freight", ScenarioRequest(overrides={"transmission_k": 1.2}))


def test_freight_downside_rows_are_adverse(service):
    r = service.compute("freight")
    down = [s for s in r.scenarios if s.shock < 0]
    assert [s.impact.value for s in down] == [-376.8, -753.6, -1130.4]
    assert all(s.status == Status.CRITICAL for s in down) and r.worst_case_status == Status.CRITICAL
    assert r.assessment.status == Status.LOW         # the headline (+8%) alone would hide this; both are exposed


def test_freight_warnings(service):
    w = " ".join(service.compute("freight").warnings)
    assert "not the company realised freight rate" in w and "not automatically a forecast" in w and "count it twice" in w


def test_custom_shocks_are_used_as_given(service):
    r = service.compute("freight", ScenarioRequest(shocks=[-10, 10]))
    assert [s.shock for s in r.scenarios] == [-10, 10] and r.scenarios[0].impact.value == -942.0
