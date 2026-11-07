"""Hypothetical fuel hedge tool (spec 5.3, 7.7). A hedge test is state: identical in both passes.

Targets fuel_all (blended price), vlsfo and hsfo (hub-weighted grade price). Decisions (P41):
- hedged tonnes in month m = coverage x C0 x target share (1, 1 - s(T0) or s(T0)), prorated by the
  days of m on or after start_date; months from the month of start_date for tenor_months;
- strike K = the target price at T0 (only strike_rule spot_at_T0 is accepted);
- swap: HEDGE_SETTLEMENT = (P_m - K) x hedged tonnes on the last day of month m, P_m the monthly
  average of the target price; posted margin M(t) = max(0, -MTM(t)) x margin factor with
  MTM(t) = (P(t) - K) x hedged tonnes not yet settled (flat forward); MARGIN_CALL on each date
  = -(M(t) - M(t - 1)). Margin is one-sided (collateral posted, never received);
- fixed-price supply: the same (P_m - K) x tonnes, on the purchase payment dates, no margin.
The margin-eligible test needs liquidity tier T4 and runs in the engine (4.4, step 5).
"""

from dataclasses import dataclass
from datetime import date, timedelta

from hlag_engine.contracts.enums import Currency, HedgeInstrument, HedgeModule, LineCode, ProvenanceLabel
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.generator.calendar import add_months, first_of_month, month_end
from hlag_engine.modules.bunker.calc import Blend, Prices
from hlag_engine.modules.interface import HedgeTarget, LineBook, ModuleInputError, ModuleRunContext
from hlag_engine.modules.paths import daterange

TARGETS = {
    "fuel_all": "Blended fuel price (both hubs, both grades)",
    "vlsfo": "Hub-weighted VLSFO price",
    "hsfo": "Hub-weighted HSFO price",
}
INSTRUMENTS = (HedgeInstrument.SWAP, HedgeInstrument.FIXED_PRICE_SUPPLY)


def hedge_targets() -> list[HedgeTarget]:
    return [HedgeTarget(t, tuple(i.value for i in INSTRUMENTS), d) for t, d in TARGETS.items()]


def bunker_tests(tests: list[HedgeTest]) -> list[HedgeTest]:
    out = []
    for test in tests:
        if test.module is not HedgeModule.BUNKER:
            continue
        if test.target not in TARGETS:
            raise ModuleInputError(f"bunker hedge target {test.target!r} is not one of {sorted(TARGETS)}")
        if test.instrument not in INSTRUMENTS:
            raise ModuleInputError(f"bunker hedge instrument {test.instrument.value!r} is not swap or fixed_price_supply")
        if test.strike_rule != "spot_at_T0":
            raise ModuleInputError(f"strike rule {test.strike_rule!r} is not supported (spot_at_T0 only)")
        out.append(test)
    return out


@dataclass
class HedgeResult:
    peak_margin: float = 0.0
    settlements: float = 0.0


def target_price(blend: Blend, prices: Prices, target: str, d: date, t0: bool = False) -> float:
    def price(sid: str) -> float:
        return prices.t0[sid] if t0 else prices.paths[sid](d)

    if target == "fuel_all":
        return blend.blend(d, price)
    w_sin, _ = blend.weights(d)
    grade = target.upper()
    return w_sin * price(f"{grade}_SIN") + (1 - w_sin) * price(f"{grade}_RTM")


def run_hedges(ctx: ModuleRunContext, tests: list[HedgeTest], prices: Prices, blend: Blend,
               book: LineBook) -> HedgeResult:
    result = HedgeResult()
    label = ProvenanceLabel.ASSUMPTION
    c0 = float(ctx.resolve("bunker.monthly_consumption_t"))
    margin_factor = float(ctx.resolve("bunker.margin_factor"))
    for i, test in enumerate(bunker_tests(tests), start=1):
        s0 = float(ctx.resolve("bunker.scrubber_share"))
        share = {"fuel_all": 1.0, "vlsfo": 1 - s0, "hsfo": s0}[test.target]
        strike = target_price(blend, prices, test.target, ctx.as_of, t0=True)
        start = test.start_date
        tonnes: dict[date, float] = {}
        for k in range(test.tenor_months):
            month = add_months(first_of_month(start), k)
            days = [d for d in daterange(month, month_end(month)) if d >= start]
            tonnes[month] = test.coverage_pct / 100 * c0 * share * len(days) / len(list(daterange(month, month_end(month))))

        def p_m(month: date) -> float:
            days = list(daterange(month, month_end(month)))
            return sum(target_price(blend, prices, test.target, d) for d in days) / len(days)

        tag = f"bunker:hedge:{test.target}:{test.instrument.value}:{i}"
        if test.instrument is HedgeInstrument.SWAP:
            for month, t in tonnes.items():
                amount = (p_m(month) - strike) * t
                result.settlements += amount
                book.add(tag, LineCode.HEDGE_SETTLEMENT, Currency.USD, month_end(month), amount, label)
            last = month_end(max(tonnes))
            posted = 0.0
            for d in daterange(start, last):
                remaining = sum(t for m, t in tonnes.items() if month_end(m) > d)
                mtm = (target_price(blend, prices, test.target, d) - strike) * remaining
                new = max(0.0, -mtm) * margin_factor
                if new != posted:
                    book.add(f"bunker:margin:{test.target}:{i}", LineCode.MARGIN_CALL, Currency.USD, d,
                             -(new - posted), label)
                    posted = new
                result.peak_margin = max(result.peak_margin, posted)
        else:
            for month, t in tonnes.items():
                days = [d for d in daterange(month, month_end(month)) if d >= start]
                per_day = (p_m(month) - strike) * t / len(days)
                for d in days:
                    pay = d + timedelta(days=int(ctx.resolve("bunker.supplier_lag_days", d)))
                    result.settlements += per_day
                    book.add(tag, LineCode.HEDGE_SETTLEMENT, Currency.USD, pay, per_day, label)
    return result
