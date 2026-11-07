"""Bunker module (spec 7): fuel price and consumption moves to dated fuel purchases, the MFR change
(reading freight's TEU), and the hypothetical hedge tool."""

from hlag_engine.contracts.alert_rule import AlertRule
from hlag_engine.contracts.enums import LineCode, ProvenanceLabel
from hlag_engine.contracts.exposure import ExposureItem
from hlag_engine.contracts.playbook import PlaybookEntry
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.generator.calendar import add_months
from hlag_engine.generator.inputs import Inputs
from hlag_engine.generator.recipes import bunker_items
from hlag_engine.modules.bunker import rules
from hlag_engine.modules.bunker.calc import SERIES, BunkerResult, run_bunker
from hlag_engine.modules.bunker.checks import delivered_premium
from hlag_engine.modules.bunker.hedge import bunker_tests, hedge_targets, run_hedges
from hlag_engine.modules.interface import (
    HedgeTarget,
    Metric,
    ModuleInputs,
    ModuleOutput,
    ModuleRunContext,
    ProfileContext,
)

A, M, P = ProvenanceLabel.ASSUMPTION, ProvenanceLabel.MARKET, ProvenanceLabel.PROXY

NOTES = [
    "CO2 / EU ETS cost not included. This module covers fuel purchase cost and MFR recovery only. The cost of CO2 "
    "emission certificates under the EU Emissions Trading System is excluded, although Hapag-Lloyd reports it "
    "together with bunker cost. Disclosed size: about USD 112m in H1 2026 (H1 2025: USD 74m). It is paid in EUR and "
    "rising as shipping's EU ETS coverage phases in. Treasurers should treat it as an additional, growing EUR outflow "
    "alongside fuel cost.",
    "Other fuels: LNG, biomethane and biofuel use (AR pp. 11, 25) are not modelled.",
    "Inventory held steady; the disclosed fuel inventories (EUR 379.5m at 31 Dec 2025) are context only.",
    "Grade proxy: whether 'MFO 0.5% FOB Rotterdam' corresponds to VLSFO matters only for comparing with disclosed "
    "anchor prices.",
    "MFR uses a fixed fuel per TEU, so the extra burn from Cape routing is not recovered in the model.",
    "Net impact depends on the assumed MFR share.",
]
UNHEDGED = "Unhedged: 100% (no fuel hedges disclosed)"


class BunkerModule:
    name = "bunker"
    line_codes = {LineCode.FUEL_PURCHASE, LineCode.MFR_RECOVERY, LineCode.HEDGE_SETTLEMENT, LineCode.MARGIN_CALL}
    gross_codes = {LineCode.FUEL_PURCHASE}
    offset_codes = {LineCode.MFR_RECOVERY, LineCode.HEDGE_SETTLEMENT, LineCode.MARGIN_CALL}
    material_series = list(SERIES.values())
    required_params = [
        "bunker.monthly_consumption_t", "bunker.fuel_per_teu_t", "bunker.hub_weight_sin", "bunker.scrubber_share",
        "bunker.supplier_lag_days", "bunker.mfr_share", "bunker.mfr_lag_months", "bunker.beta_fallback",
        "bunker.margin_factor", "bunker.average_price_paid_h1_usd_per_t",
        "freight.collection_lag_days", "freight.delay_affected_share",
    ]

    def build_exposures(self, ctx: ProfileContext) -> list[ExposureItem]:
        start, end = ctx.horizon
        return bunker_items(Inputs(ctx.resolver), start, end)

    def run(self, ctx: ModuleRunContext, params: ScenarioParams, exposures: list[ExposureItem],
            inputs: ModuleInputs) -> ModuleOutput:
        bunker = [e for e in exposures if e.owner_spec.value == "bunker"]
        result = run_bunker(ctx, params, bunker, inputs)
        hedge = run_hedges(ctx, params.hedge_tests, result.prices, result.blend, result.book)
        lines, watch = result.book.build(ctx.horizon[1])
        out = ModuleOutput(cash_lines=lines, watch_list=watch, notes=list(NOTES))
        if not bunker_tests(params.hedge_tests):
            out.notes.append(UNHEDGED)
        else:
            out.metrics["hedge_peak_posted_margin_usd"] = Metric(hedge.peak_margin, "USD", A,
                                                                 "max over dates of posted margin M(t)")
            out.metrics["hedge_settlements_usd"] = Metric(hedge.settlements, "USD", A, "sum of hedge settlements")
        out.metrics.update(self._metrics(ctx, result))
        out.diagnostics["proxy_based"] = bool(result.prices.proxy_series)
        out.diagnostics["proxy_series"] = result.prices.proxy_series
        out.diagnostics["derived_inputs"] = result.prices.brent.to_dict()
        out.diagnostics["monthly_price_usd_per_t"] = result.monthly_price
        out.diagnostics["monthly_consumption_t"] = result.monthly_consumption
        out.diagnostics["purchases_usd"] = result.purchases_by_month
        out.diagnostics["mfr_usd"] = result.mfr_by_month
        return out

    def _metrics(self, ctx: ModuleRunContext, r: BunkerResult) -> dict[str, Metric]:
        prices, blend, t0 = r.prices, r.blend, ctx.as_of
        metrics: dict[str, Metric] = {}
        for sid, value in prices.t0.items():
            metrics[f"hub_price_usd_per_t.{sid}"] = Metric(
                value, "USD/t", P if sid in prices.proxy_series else M,
                "Brent-filled: last print x (1 + beta x Brent % change)" if sid in prices.proxy_series
                else "last weekly print at or before T0")
        for hub in ("SIN", "RTM"):
            metrics[f"vlsfo_hsfo_spread_usd_per_t.{hub}"] = Metric(
                prices.t0[f"VLSFO_{hub}"] - prices.t0[f"HSFO_{hub}"], "USD/t", M, f"VLSFO_{hub} - HSFO_{hub} at T0")
        p_t0 = blend.blend(t0, lambda sid: prices.t0[sid])
        metrics["blended_price_t0_usd_per_t"] = Metric(p_t0, "USD/t", A, "sum_h w_h ((1 - s) VLSFO_h + s HSFO_h) at T0")
        if r.monthly_price:
            first = min(r.monthly_price)
            w_sin, _ = blend.weights(t0)

            def vlsfo(d):
                return w_sin * prices.paths["VLSFO_SIN"](d) + (1 - w_sin) * prices.paths["VLSFO_RTM"](d)

            v_t0 = w_sin * prices.t0["VLSFO_SIN"] + (1 - w_sin) * prices.t0["VLSFO_RTM"]
            metrics["fuel_mix_blended_change_pct"] = Metric(
                (r.monthly_price[first] / p_t0 - 1) * 100, "pct", A,
                f"P_m / P(T0) - 1 for {first:%Y-%m}; a fuel-mix effect, not a natural hedge")
            metrics["fuel_mix_vlsfo_only_change_pct"] = Metric(
                (blend.month_average(vlsfo, first) / v_t0 - 1) * 100, "pct", A,
                f"hub-weighted VLSFO change for {first:%Y-%m} (the blended change if s were 0)")
        anchor_blend = blend.blend(t0, lambda sid: prices.anchor[sid])
        paid = float(ctx.resolve("bunker.average_price_paid_h1_usd_per_t"))
        metrics["anchor_blended_price_usd_per_t"] = Metric(
            anchor_blend, "USD/t", A, "blend of the H1 2026 anchor hub prices with T0 hub weights and scrubber share")
        metrics["delivered_premium_usd_per_t"] = Metric(
            delivered_premium(anchor_blend, paid), "USD/t", A,
            f"disclosed average price paid (USD {paid:g}/t, IR pp. 10-11) - blended anchor hub price")
        horizon3 = add_months(t0, 3)
        outflow = sum(float(l.amount) for l in r.book.build(ctx.horizon[1])[0]
                      if l.line_code is LineCode.FUEL_PURCHASE and l.date <= horizon3)
        metrics["fuel_outflow_next_3_months_usd"] = Metric(outflow, "USD", A, "fuel purchase payments up to T0 + 3 months")
        metrics["mfr_change_usd"] = Metric(sum(r.mfr_by_month.values()), "USD", A,
                                           "sum_m sigma x (Ref_m - P_base) x fuel per TEU x TEU_m")
        for grade, beta in prices.brent.betas.items():
            metrics[f"brent_beta.{grade}"] = Metric(beta.value, "ratio", beta.label, beta.formula)
        if prices.brent.gap_pp is not None:
            metrics["brent_vlsfo_gap_pp"] = Metric(prices.brent.gap_pp, "pp", M,
                                                   "4-week % change of hub-weighted VLSFO minus that of Brent")
        return metrics

    def alert_rules(self) -> list[AlertRule]:
        return rules.alert_rules()

    def playbook(self) -> list[PlaybookEntry]:
        return rules.playbook()

    def hedge_targets(self) -> list[HedgeTarget]:
        return hedge_targets()
