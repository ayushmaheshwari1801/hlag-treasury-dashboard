"""Interest rate module (spec 8): rate paths and spread moves to dated interest cash on both sides
of the balance sheet, maturities and refinancing, the ZIM switch and the hypothetical swap tool."""

from hlag_engine.contracts.alert_rule import AlertRule
from hlag_engine.contracts.enums import Currency, LineCode, ProvenanceLabel
from hlag_engine.contracts.exposure import ExposureItem, MissingInput
from hlag_engine.contracts.playbook import PlaybookEntry
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.generator.calendar import add_months, month_end, months_between
from hlag_engine.generator.inputs import Inputs
from hlag_engine.generator.maturities import build_maturity_schedule
from hlag_engine.generator.recipes import rates_items
from hlag_engine.modules.interface import (
    HedgeTarget,
    Metric,
    ModuleInputs,
    ModuleOutput,
    ModuleRunContext,
    ProfileContext,
)
from hlag_engine.modules.rates import rules
from hlag_engine.modules.rates.calc import RatesCalc, RatesState
from hlag_engine.modules.rates.swap import hedge_targets, rates_tests, run_swaps

A, D, M = ProvenanceLabel.ASSUMPTION, ProvenanceLabel.DERIVED, ProvenanceLabel.MARKET

NOTES = [
    "Special fund: income is disclosed (USD 41m in H1 2026), but its fair-value effects (+-50 bp ~ EUR 13.8m) are "
    "non-cash; the fund's interest income is not modelled as rate-sensitive cash.",
    "Covenants: equity, liquidity and loan-to-value covenants exist and were complied with; levels are not disclosed.",
    "Embedded bond options: non-cash fair-value effects, excluded.",
    "2024 sensitivity: the published 2024 +-100 bp figures do not reconcile with a simple recompute; they are not used.",
    "Effectively unhedged: the only disclosed swap (USD 2.2m nominal, 5.30% fixed) is under 0.1% of floating debt.",
]
SERIES_METRICS = ("SOFR", "ESTR", "UST_2Y", "EA_2Y", "BB_OAS")


class RatesModule:
    name = "rates"
    line_codes = {LineCode.INTEREST_EXPENSE, LineCode.INTEREST_INCOME, LineCode.REFINANCING_COST,
                  LineCode.DEBT_PRINCIPAL, LineCode.DEBT_DRAWDOWN, LineCode.ACQUISITION_PAYMENT,
                  LineCode.HEDGE_SETTLEMENT, LineCode.MARGIN_CALL}
    gross_codes = {LineCode.INTEREST_EXPENSE, LineCode.REFINANCING_COST, LineCode.INTEREST_INCOME}
    debt_side_codes = {LineCode.INTEREST_EXPENSE, LineCode.REFINANCING_COST}  # G_rates: each side alone (4.3)
    cash_side_codes = {LineCode.INTEREST_INCOME}
    offset_codes = {LineCode.HEDGE_SETTLEMENT, LineCode.MARGIN_CALL}
    # Principal and drawdown net to zero under rollover (8.8); the ZIM acquisition payment is state (P63).
    excluded_codes = {LineCode.DEBT_PRINCIPAL, LineCode.DEBT_DRAWDOWN, LineCode.ACQUISITION_PAYMENT}
    material_series = ["SOFR", "ESTR", "BB_OAS"]  # BB_OAS when maturities or ZIM are in the horizon (8.4)
    required_params = [
        "rates.floating_split_usd", "rates.new_issue_premium_bp", "rates.swap_spread_bp", "rates.bond_maturity_date",
        "rates.financial_debt_total_eur", "rates.lease_liabilities_eur", "fx.usd_per_eur_close_2025_12_31",
        "rates.interest_bearing_cash_share",  # catalogue entries (8.10), sizing in the generator
        "rates.zim_tranche_on",  # catalogue switch; step 5 copies it into params.rates.zim_tranche_on (P50)
        "rates.zim_amount_usd", "rates.zim_consideration_usd",  # P63, via the generator
    ]

    def exposures_and_missing(self, ctx: ProfileContext) -> tuple[list[ExposureItem], list[MissingInput]]:
        """Generator recipe for rates, with the placeholders it could not use (as the generator reports them)."""
        start, end = ctx.horizon
        inputs = Inputs(ctx.resolver)
        schedules = inputs.attempt("debt maturity schedule (rates and fx maturities)",
                                   lambda: [build_maturity_schedule(inputs, end)])
        items = rates_items(inputs, start, end, schedules[0] if schedules else None)
        return items, inputs.missing

    def build_exposures(self, ctx: ProfileContext) -> list[ExposureItem]:
        return self.exposures_and_missing(ctx)[0]

    def run(self, ctx: ModuleRunContext, params: ScenarioParams, exposures: list[ExposureItem],
            inputs: ModuleInputs) -> ModuleOutput:
        calc = RatesCalc(ctx, params, exposures)
        state = calc.run()
        swaps = {"peak_margin": 0.0, "swapped_pay_fixed_usd": 0.0, "strikes": {}}
        if rates_tests(params.hedge_tests):
            def swap_part():
                swaps.update(run_swaps(calc, params.hedge_tests))
            state.attempt("swap tool", swap_part)
        lines, watch = state.book.build(ctx.horizon[1])
        out = ModuleOutput(cash_lines=lines, watch_list=watch, notes=list(NOTES))
        out.metrics.update(self._metrics(ctx, calc, state, swaps))
        out.diagnostics["missing"] = state.missing
        out.diagnostics["floors"] = state.floors
        out.diagnostics["zim_on"] = params.rates.zim_tranche_on
        if not rates_tests(params.hedge_tests):
            out.notes.append("Unhedged: 100% (no interest rate hedges of note disclosed)")
        return out

    # ---------------------------------------------------------------- metrics (8.5, 8.8)
    def _metrics(self, ctx: ModuleRunContext, calc: RatesCalc, state: RatesState, swaps: dict) -> dict[str, Metric]:
        metrics: dict[str, Metric] = {}
        for series in SERIES_METRICS:
            if series in ctx.market:
                metrics[f"benchmark_pct.{series}"] = Metric(ctx.market[series].value, "%", M, f"{series} at T0")
        metrics["binding_floor"] = Metric(bool(state.floors), "flag", M,
                                          "a benchmark below 0% at a reset or roll fixed at the 0% floor")
        metrics["zim_tranche_on"] = Metric(calc.params.rates.zim_tranche_on, "flag", A, "state switch (P50)")
        if swaps["strikes"]:
            metrics["swap_peak_posted_margin"] = Metric(swaps["peak_margin"], "native", A, "max posted margin M(t)")
            metrics["swap_fixed_rate_pct"] = Metric(swaps["strikes"], "%", A, "2-year yield at T0 + swap spread")

        def nfp() -> None:
            fx = calc.market.fx_t0() if any(c is Currency.EUR for c in
                                             [*state.float_currency.values(), *state.deposit_currency.values()]) else 1.0
            path = {}
            for month in months_between(*ctx.horizon):
                d = month_end(month)
                cash = sum(n.outstanding(d) for n in state.deposit_balance_usd.values())
                floating = sum(n.outstanding(d) * (fx if state.float_currency[s] is Currency.EUR else 1.0)
                               for s, n in state.float_notional.items())
                floating += sum(amount for when, amount in state.refi if when <= d)
                if state.zim and d >= state.zim[0]:
                    floating += state.zim[1]
                path[d] = cash - floating
            metrics["net_floating_position_usd"] = Metric(
                path, "USD", A, "interest-bearing cash - floating debt incl. refinanced debt (- ZIM when on), "
                                "month ends, EUR at T0 FX")

        def fixed_share() -> None:
            fx = float(ctx.resolve("fx.usd_per_eur_close_2025_12_31"))
            total = float(ctx.resolve("rates.financial_debt_total_eur")) * fx
            bond = sum(float(e.quantities["notional"]) for e in calc.exposures
                       if e.owner_spec.value == "rates" and e.sub_id.endswith(":bond")) * fx
            fixed = (state.fixed_usd.outstanding(ctx.horizon[1]) if state.fixed_usd else 0.0) + bond
            metrics["fixed_share_horizon_adjusted"] = Metric(
                (fixed + swaps["swapped_pay_fixed_usd"]) / total, "fraction", A,
                "(fixed debt not maturing in the horizon + swapped notional) / financial debt")
            metrics["lease_liabilities_usd"] = Metric(float(ctx.resolve("rates.lease_liabilities_eur")) * fx, "USD", D,
                                                      "lease liabilities, shown beside the fixed share")

        def interest_path() -> None:
            fx = calc.market.fx_t0() if any(k.endswith(":EUR") for b in state.interest_by_month.values() for k in b) else 1.0
            path = {}
            for month, bucket in sorted(state.interest_by_month.items()):
                usd = {k: v * (fx if k.endswith(":EUR") else 1.0) for k, v in bucket.items()}
                expense = sum(v for k, v in usd.items() if not k.startswith("income"))
                income = sum(v for k, v in usd.items() if k.startswith("income"))
                path[month] = {"expense": expense, "income": income, "net": expense + income}
            metrics["interest_path_usd"] = Metric(path, "USD", A, "monthly interest cash by payment month, EUR at T0 FX")

        def watch_list() -> None:
            if not state.maturities:
                return
            last = state.maturities[-1]
            monthly = last["usd"] + last["eur_usd_at_disclosure"]
            h_end = ctx.horizon[1]
            base_rate = calc.market.value("SOFR") + (calc.market.value("BB_OAS") * 100
                                                     + float(ctx.resolve("rates.new_issue_premium_bp"))) / 100
            items = []
            for k in range(1, 13):
                when = month_end(add_months(h_end, k))
                if when > month_end(add_months(ctx.as_of, 24)):
                    break
                stressed = calc.market.path(Currency.USD)(when) + calc.new_issue_spread_bp(when) / 100
                items.append({"month": when.replace(day=1), "amount_usd": monthly,
                              "base_new_issue_rate_pct": base_rate, "stressed_new_issue_rate_pct": stressed})
            metrics["maturity_watch_list"] = Metric(items, "USD", A,
                                                    "maturities in months 12-24 (same monthly amount, P18); never cash")
            metrics["upcoming_maturities"] = Metric(
                [{"date": r["date"], "usd": r["usd"], "eur": r["eur"]} for r in state.maturities], "native", A,
                "maturity slices in the horizon (USD share in USD, EUR share in EUR)")

        state.attempt("net floating position metric", nfp)
        state.attempt("horizon-adjusted fixed share metric", fixed_share)
        state.attempt("interest path metric", interest_path)
        state.attempt("maturity watch list", watch_list)
        return metrics

    def alert_rules(self) -> list[AlertRule]:
        return rules.alert_rules()

    def playbook(self) -> list[PlaybookEntry]:
        return rules.playbook()

    def hedge_targets(self) -> list[HedgeTarget]:
        return hedge_targets()

