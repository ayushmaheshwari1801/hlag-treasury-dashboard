"""Freight module (spec 6): route-index moves to dated freight revenue cash, plus volume lines."""

from datetime import date

from hlag_engine.contracts.alert_rule import AlertRule
from hlag_engine.contracts.enums import LineCode, ProvenanceLabel, Trade
from hlag_engine.contracts.exposure import ExposureItem
from hlag_engine.contracts.playbook import PlaybookEntry
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.generator.inputs import Inputs
from hlag_engine.generator.recipes import freight_items
from hlag_engine.modules.freight import rules
from hlag_engine.modules.freight.calc import INDEX_SERIES, WCI_UNIT, group_exposures, run_freight, usd_per_teu
from hlag_engine.modules.freight.checks import check_trade_volumes
from hlag_engine.modules.interface import (
    HedgeTarget,
    Metric,
    ModuleInputs,
    ModuleOutput,
    ModuleRunContext,
    ProfileContext,
)

A, D, M = ProvenanceLabel.ASSUMPTION, ProvenanceLabel.DERIVED, ProvenanceLabel.MARKET

NOTES = [
    "Demurrage and detention not modelled: revenue not assigned to trades, mainly demurrage and detention, "
    "was USD 1,065m in H1 2026, about 10% of Liner revenue.",
    "Fuel share of the base rate: about 15% of the all-in rate (0.359 t x USD 592/t of USD 1,406/TEU); freight "
    "results are overstated by roughly that share.",
    "Terminal & Infrastructure revenue (USD 360m in H1 2026) is excluded.",
    "No freight hedges in v1; the rate-locked (contract) share is shown as information only.",
]


class FreightModule:
    name = "freight"
    line_codes = {LineCode.FREIGHT_REVENUE}
    gross_codes = {LineCode.FREIGHT_REVENUE}
    offset_codes: set[LineCode] = set()
    material_series = list(INDEX_SERIES.values())
    required_params = [
        "freight.beta", "freight.pricing_lag_days", "freight.kappa", "freight.clause_lag_days", "freight.gamma",
        "freight.leakage_gap_down", "freight.leakage_gap_up", "freight.leakage_share_down",
        "freight.leakage_share_up", "freight.leakage_volume_loss", "freight.delay_affected_share",
        "freight.collection_lag_days", "freight.transport_volume_h1_teu",
        *(f"freight.non_usd_share.{t.value}" for t in Trade),
        *(f"freight.invoice_currency.{t.value}" for t in Trade),
        *(f"freight.h1_volume_teu.{t.value}" for t in Trade),
    ]

    def build_exposures(self, ctx: ProfileContext) -> list[ExposureItem]:
        start, end = ctx.horizon
        return freight_items(Inputs(ctx.resolver), start, end)

    def run(self, ctx: ModuleRunContext, params: ScenarioParams, exposures: list[ExposureItem],
            inputs: ModuleInputs) -> ModuleOutput:
        check_trade_volumes(ctx.profile)
        freight = [e for e in exposures if e.owner_spec.value == "freight"]
        result = run_freight(ctx, params, freight)
        lines, watch = result.book.build(ctx.horizon[1])
        out = ModuleOutput(cash_lines=lines, volume_lines=result.volume_lines, watch_list=watch,
                           notes=list(NOTES))
        metrics, derived = self._metrics(ctx, freight, result)
        out.metrics.update(metrics)
        out.diagnostics["derived_inputs"] = {"week_on_week_pct": derived}
        out.diagnostics["leakage_events"] = result.leakage_events
        out.diagnostics["daily_revenue_usd"] = result.daily_revenue
        out.diagnostics["revenue_by_class_usd"] = result.revenue_by_class
        if watch:
            out.metrics["watch_list_usd"] = Metric(sum(w.amount for w in watch), "USD (native)", A,
                                                   "sum of freight cash dated after the horizon")
        return out

    def _metrics(self, ctx: ModuleRunContext, exposures: list[ExposureItem], result) -> tuple[dict[str, Metric], dict]:
        metrics: dict[str, Metric] = {}
        books = group_exposures(exposures)
        weighted, weight = 0.0, 0.0
        stored = ctx.derived_inputs.get("freight")  # replay: values derived from history in the original run (P71)
        derived = dict((stored or {}).get("week_on_week_pct", {}))
        for trade, book in books.items():
            series = INDEX_SERIES[trade]
            t0 = ctx.snapshot(series, WCI_UNIT)
            metrics[f"index_level_usd_per_teu.{trade.value}"] = Metric(
                usd_per_teu(t0.value), "USD/TEU", t0.label, f"{series} (USD/FEU) / 2")
            teu = result.teu_by_trade.get(trade, 0.0)
            if teu:
                metrics[f"realised_rate_usd_per_teu.{trade.value}"] = Metric(
                    result.revenue_by_trade[trade] / teu, "USD/TEU", A,
                    "revenue earned / TEU shipped, volume-weighted across classes (model estimate)")
            metrics[f"rate_locked_share.{trade.value}"] = Metric(
                sum(c.share for c in book.cohorts if c.reset > ctx.as_of), "fraction", A,
                "contract cohorts not yet reset at T0 (information only)")
            wow = derived.get(trade.value) if stored else week_on_week(ctx.history.get(series, ()))
            derived[trade.value] = wow
            if wow is not None:
                metrics[f"week_on_week_pct.{trade.value}"] = Metric(wow, "pct", M, "last weekly print / previous - 1")
            h1 = float(ctx.resolve(f"freight.h1_volume_teu.{trade.value}"))
            weighted += h1 * (t0.value / ctx.profile.anchor(series) - 1)
            weight += h1
        if weight:
            metrics["index_move_since_anchor_pct"] = Metric(
                weighted / weight * 100, "pct", D,
                "H1 2026 volume-weighted change of the route indices from the anchor period to T0; shown as "
                "'base rate from <anchor period>; index has moved X% since then'")
        metrics["revenue_exposed_two_quarters_usd"] = Metric(
            result.exposed_two_quarters, "USD", A,
            "revenue on shipments in the next 182 days from spot, index-linked, leaked and cohorts resetting by then")
        return metrics, derived

    def alert_rules(self) -> list[AlertRule]:
        return rules.alert_rules()

    def playbook(self) -> list[PlaybookEntry]:
        return rules.playbook()

    def hedge_targets(self) -> list[HedgeTarget]:
        return []  # none in v1 (6.7)


def week_on_week(history) -> float | None:
    if len(history) < 2:
        return None
    (_, prev), (_, last) = sorted(history)[-2:]
    return (last / prev - 1) * 100


def cumulative_by_date(amounts: dict[date, float]) -> dict[date, float]:
    total, out = 0.0, {}
    for d in sorted(amounts):
        total += amounts[d]
        out[d] = total
    return out
