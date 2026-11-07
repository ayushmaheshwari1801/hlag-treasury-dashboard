"""Independent re-derivation: exact Fraction arithmetic written straight from the spec text, compared with the engines
over random assumptions (seeded). Catches sign and scale errors that the default-value checks cannot."""
import random
from fractions import Fraction as F

import pytest

from treasury_sensitivity.engines.common import ScenarioRequest

rng = random.Random(7)


def q(x, places=3):
    return F(str(round(x, places)))


def raw(result):
    return F(result.headline.impact.raw)


@pytest.mark.parametrize("_", range(25))
def test_bunker(service, _):
    dp, Q, r, t = rng.randint(-300, 300), q(rng.uniform(0, 10)), q(rng.random()), q(rng.random())
    res = service.compute("bunker", ScenarioRequest(headline_shock=dp, overrides={"consumption_mt": float(Q), "recovery_fraction": float(r), "cash_factor": float(t)}))
    assert raw(res) == -F(dp) * Q * (1 - r) * t


@pytest.mark.parametrize("_", range(25))
def test_freight(service, _):
    pct, k, c, V, F0 = q(rng.uniform(-50, 50)), q(rng.random()), q(rng.random()), q(rng.uniform(0, 20)), rng.randint(500, 4000)
    res = service.compute("freight", ScenarioRequest(headline_shock=float(pct), overrides={"transmission_k": float(k), "cash_collection_c": float(c),
                          "volume_m_teu": float(V), "realised_rate_usd_teu": F0}))
    assert raw(res) == F(F0) * V * (k * pct / 100) * c


@pytest.mark.parametrize("_", range(25))
def test_interest_mode_a(service, _):
    bps = q(rng.uniform(-200, 200), 1)
    res = service.compute("interest", ScenarioRequest(headline_shock=float(bps)))
    expected = F("-20.8") * bps / 100 if bps >= 0 else F("21.5") * abs(bps) / 100
    assert raw(res) == expected


@pytest.mark.parametrize("_", range(25))
def test_interest_mode_b(service, _):
    bps, P, h, days = q(rng.uniform(-200, 200), 1), rng.randint(1, 5000), q(rng.random()), rng.randint(1, 730)
    for ptype, sign in (("floating_debt", -1), ("floating_deposit", 1)):
        res = service.compute("interest", ScenarioRequest(mode="B", headline_shock=float(bps), options={"position_type": ptype},
                              overrides={"principal_eur_m": P, "hedged_fraction": float(h), "days_affected": days}))
        expected = sign * F(P) * (1 - h) * (bps / 10000) * F(days) / 365
        assert abs(raw(res) - expected) < F(1, 10**20)      # /365 does not terminate: Decimal keeps 28 digits, Fraction is exact


@pytest.mark.parametrize("_", range(25))
def test_fx(service, _):
    gross, share, shock = q(rng.uniform(1, 500)), q(rng.random()), q(rng.uniform(-20, 20), 2)
    hedge = gross * share
    for direction, sign in (("payable", -1), ("receivable", 1)):
        res = service.compute("fx", ScenarioRequest(mode=direction, headline_shock=float(shock),
                              overrides={"gross_exposure": float(gross), "hedge_notional": float(hedge)}))
        S0 = F("0.73")
        assert raw(res) == sign * (gross - hedge) * (S0 * (1 + shock / 100) - S0)
