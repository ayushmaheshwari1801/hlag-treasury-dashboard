"""Hypothetical interest rate swap tool (spec 5.3, 8.7). A hedge test is state: identical in both passes.

Targets: floating_debt_usd, floating_debt_eur (pay-fixed) and deposits_usd, deposits_eur
(receive-fixed). Decisions (P54):
- the swap notional is coverage x each underlying tranche's or cohort's T0 notional, following that
  item's resets; a period counts when it starts on or after start_date and before start + tenor;
- K = the currency's 2-year government yield at T0 + rates.swap_spread_bp, fixed at T0 (D20, D43);
- settlement for [r_n, r_{n+1}) = notional x (b(r_n) - K) x days / 360, paid at r_{n+1}, matching the
  hedged debt's fixing (sign reversed for receive-fixed); the floating leg has no floor;
- margin: MTM(t) = sign x (b(t) - K) x notional x remaining years (days / 365); posted margin
  M(t) = max(0, -MTM(t)); MARGIN_CALL = -(M(t) - M(t - 1)), daily; one-sided. The margin-eligible
  test needs liquidity tier T4 and runs in the engine (4.4, step 5).
"""


from hlag_engine.contracts.enums import Currency, HedgeInstrument, HedgeModule, LineCode, ProvenanceLabel
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.generator.calendar import add_months
from hlag_engine.modules.interface import HedgeTarget, ModuleInputError
from hlag_engine.modules.paths import daterange
from hlag_engine.modules.rates.calc import RatesCalc

TARGETS = {
    "floating_debt_usd": (Currency.USD, "float", 1, "Pay-fixed swap on USD floating debt"),
    "floating_debt_eur": (Currency.EUR, "float", 1, "Pay-fixed swap on EUR floating debt"),
    "deposits_usd": (Currency.USD, "deposit", -1, "Receive-fixed swap on USD deposits"),
    "deposits_eur": (Currency.EUR, "deposit", -1, "Receive-fixed swap on EUR deposits"),
}
TWO_YEAR = {Currency.USD: "UST_2Y", Currency.EUR: "EA_2Y"}


def hedge_targets() -> list[HedgeTarget]:
    return [HedgeTarget(t, (HedgeInstrument.SWAP.value,), d) for t, (_, _, _, d) in TARGETS.items()]


def rates_tests(tests: list[HedgeTest]) -> list[HedgeTest]:
    out = []
    for test in tests:
        if test.module is not HedgeModule.RATES:
            continue
        if test.target not in TARGETS:
            raise ModuleInputError(f"rates hedge target {test.target!r} is not one of {sorted(TARGETS)}")
        if test.instrument is not HedgeInstrument.SWAP:
            raise ModuleInputError(f"rates hedge instrument must be swap, got {test.instrument.value!r}")
        if test.strike_rule != "spot_at_T0":
            raise ModuleInputError(f"strike rule {test.strike_rule!r} is not supported (spot_at_T0 only)")
        out.append(test)
    return out


def run_swaps(calc: RatesCalc, tests: list[HedgeTest]) -> dict:
    """Emit settlements and margin calls; returns {peak_margin, swapped_pay_fixed_usd, strikes}."""
    state, market = calc.state, calc.market
    label = ProvenanceLabel.ASSUMPTION
    summary = {"peak_margin": 0.0, "swapped_pay_fixed_usd": 0.0, "strikes": {}}
    for i, test in enumerate(rates_tests(tests), start=1):
        ccy, kind, sign, _ = TARGETS[test.target]
        strike = market.value(TWO_YEAR[ccy]) + float(calc.ctx.resolve("rates.swap_spread_bp")) / 100
        summary["strikes"][f"{test.target}:{i}"] = strike
        end_of_tenor = add_months(test.start_date, test.tenor_months)
        if kind == "float":
            underlying = {s: (state.float_notional[s].base, state.float_resets[s])
                          for s, c in state.float_currency.items() if c is ccy}
        else:
            underlying = {s: (state.deposit_balance[s], state.deposit_rolls[s])
                          for s, c in state.deposit_currency.items() if c is ccy}
        if not underlying:
            raise ModuleInputError(f"no {test.target} exposures to hedge")
        total = 0.0
        for sub_id, (notional, resets) in underlying.items():
            swap_notional = test.coverage_pct / 100 * notional
            total += swap_notional
            months = 3
            edges = [*resets, add_months(resets[-1], months)]
            for start, end in zip(edges, edges[1:]):
                if not test.start_date <= start < end_of_tenor:
                    continue
                bench = market.fixing(ccy, start)
                amount = sign * swap_notional * (bench - strike) / 100 * (end - start).days / 360
                calc.emit(f"rates:swap:{test.target}:{i}", LineCode.HEDGE_SETTLEMENT, ccy, end, amount, label)
        if sign > 0:
            summary["swapped_pay_fixed_usd"] += total * (market.fx_t0() if ccy is Currency.EUR else 1.0)
        posted = 0.0
        for d in daterange(test.start_date, end_of_tenor):
            remaining = (end_of_tenor - d).days / 365
            mtm = sign * (market.path(ccy)(d) - strike) / 100 * total * remaining
            new = max(0.0, -mtm)
            if new != posted:
                calc.emit(f"rates:margin:{test.target}:{i}", LineCode.MARGIN_CALL, ccy, d, -(new - posted), label)
                posted = new
            summary["peak_margin"] = max(summary["peak_margin"], posted)
    return summary
