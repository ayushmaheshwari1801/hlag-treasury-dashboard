"""Acceptance values from the specification (s4-s7, s10) with default inputs."""
from decimal import Decimal as D

from treasury_sensitivity.engines.common import ScenarioRequest
from treasury_sensitivity.engines.fx import fx_cash_effect
from treasury_sensitivity.models import Provenance, Reliability, Status


def values(result):
    return [r.impact.value for r in result.scenarios]


def test_cad_table(service):
    r = service.compute("fx")
    assert [s.shock for s in r.scenarios] == [-10, -5, 0, 5, 10]
    assert values(r) == [1.278, 0.639, 0.0, -0.639, -1.278]
    assert r.impact_type.value == "FX modelled cash effect"
    assert r.horizon_days == 91


def test_interest_mode_a_table(service):
    r = service.compute("interest")
    assert r.mode == "A"
    assert r.market.latest_value == 2.25 and r.market.previous_value == 2.0 and r.market.change == 25.0
    assert [s.shock for s in r.scenarios] == [0, 12.5, 25, 50]
    assert values(r) == [0.0, -2.6, -5.2, -10.4]
    assert r.headline.impact.value == -5.2
    assert "not cash" in r.impact_type.value.lower()


def test_bunker_table(service):
    r = service.compute("bunker")
    assert r.market.latest_value == 700 and r.market.previous_value == 650
    assert [s.shock for s in r.scenarios] == [0, 25, 50, 75]
    assert values(r) == [0.0, -48.0, -96.0, -144.0]


def test_freight_table(service):
    r = service.compute("freight")
    assert r.market.latest_value == 1728 and r.market.previous_value == 1600
    spec_rows = [s for s in r.scenarios if s.shock >= 0]
    assert [s.shock for s in spec_rows] == [0, 4, 8, 12]
    assert [s.impact.value for s in spec_rows] == [0.0, 376.8, 753.6, 1130.4]


def test_all_modules_are_low_reliability(service):
    for m in ("fx", "interest", "bunker", "freight"):
        assert service.compute(m).reliability == Reliability.LOW


def test_every_number_has_exactly_one_provenance_tag(service):
    for m in ("fx", "interest", "bunker", "freight"):
        r = service.compute(m)
        quantities = [i.quantity for i in r.inputs] + r.intermediate + r.disclosed_reference + [s.impact for s in r.scenarios]
        for q in quantities:
            assert isinstance(q.provenance, Provenance)
            if q.value is None:
                assert q.provenance == Provenance.UNAVAILABLE      # missing is never shown as a number


def test_rounding_is_half_away_from_zero():
    assert fx_cash_effect(D("17.5"), D("0.73"), D(10), "payable") == D("-1.2775")
