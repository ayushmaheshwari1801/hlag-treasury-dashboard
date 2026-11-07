"""Bunker back-tests (spec 7.13) and the blended anchor vs the disclosed price paid (3.7)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BT2Result:
    modelled: float  # consumption x price, USD m
    reported: float  # bunker and emissions expense - CO2 certificates, USD m
    difference: float
    tolerance: float

    @property
    def passed(self) -> bool:
        return abs(self.difference) <= self.tolerance


def bt2_cost_level(consumption_mt: float, price: float, expense_m: float, co2_m: float,
                   tolerance_m: float) -> BT2Result:
    modelled = consumption_mt * price
    reported = expense_m - co2_m
    return BT2Result(modelled, reported, modelled - reported, tolerance_m)


@dataclass(frozen=True)
class BT3Result:
    prior_consumption_mt: float
    price_effect: float
    volume_effect: float
    reported_change: float
    residual: float


def bt3_bridge(consumption_mt: float, consumption_change: float, price: float, prior_price: float,
               expense_m: float, co2_m: float, prior_expense_m: float, prior_co2_m: float) -> BT3Result:
    prior = consumption_mt / (1 + consumption_change)
    price_effect = (price - prior_price) * consumption_mt
    volume_effect = (consumption_mt - prior) * prior_price
    reported = (expense_m - co2_m) - (prior_expense_m - prior_co2_m)
    return BT3Result(prior, price_effect, volume_effect, reported, reported - (price_effect + volume_effect))


def delivered_premium(anchor_blend: float, price_paid: float) -> float:
    """Disclosed average price paid minus the blended anchor hub price (3.7)."""
    return price_paid - anchor_blend
