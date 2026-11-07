"""FX module (spec 9B): the currency flows no other module owns, the translation memo positions and
the policy hedge definitions passed to the conversion layer (9A)."""

import math
from decimal import Decimal

from hlag_engine.contracts.alert_rule import AlertRule
from hlag_engine.contracts.enums import HedgeInstrument, LineCode, ProvenanceLabel
from hlag_engine.contracts.exposure import ExposureItem, MissingInput
from hlag_engine.contracts.playbook import PlaybookEntry
from hlag_engine.contracts.policy_hedge import HedgePolicy
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.generator.inputs import Inputs
from hlag_engine.generator.maturities import build_maturity_schedule
from hlag_engine.generator.recipes import fx_items
from hlag_engine.modules.fx import rules
from hlag_engine.modules.fx.calc import RATIO_KEYS, build_specs, emit_lines, memo_native
from hlag_engine.modules.fx.checks import sign_check
from hlag_engine.modules.interface import (
    HedgeTarget,
    Metric,
    ModuleInputs,
    ModuleOutput,
    ModuleRunContext,
    ProfileContext,
)

A, D, M = ProvenanceLabel.ASSUMPTION, ProvenanceLabel.DERIVED, ProvenanceLabel.MARKET
CURRENCIES = ("EUR", "CAD", "GBP", "CNY", "INR", "BRL")

NOTES = [
    "Capital controls and repatriation (CNY, INR, BRL): qualitative only, through the news engine; the CNY position "
    "makes this relevant.",
    "EUR reporting translation: non-cash, excluded.",
    "H1 2026 exchange-rate losses and gains (USD 237m and 244m): context only.",
    "Forward points: ignored (spot held flat), a documented simplification.",
    "CO2 certificates (about USD 112m in H1 2026) are an EUR outflow, noted only (7.11).",
]
TARGETS = {
    HedgePolicy.CAD_OPERATING_COSTS: "CAD rolling 13-week programme (ratio capped at 80%)",
    HedgePolicy.EUR_OPERATING_COSTS: "EUR operating costs (0% by default)",
    HedgePolicy.EUR_DIVIDEND: "EUR dividend (0% by default)",
    HedgePolicy.EUR_DEBT_PRINCIPAL: "EUR debt principal (100% by default)",
    HedgePolicy.INR_BRL_OBLIGATIONS: "INR and BRL obligations (100% at the 31 Dec 2025 rate)",
}


class FxModule:
    name = "fx"
    own_codes = {LineCode.FX_OPERATING_COST, LineCode.DIVIDEND, LineCode.DEBT_PRINCIPAL, LineCode.FX_TRANSACTION}
    line_codes = own_codes | {LineCode.HEDGE_SETTLEMENT}  # policy hedge settlements are emitted by 9A, owner fx
    gross_codes = {LineCode.FX_CONVERSION_EFFECT}  # G_fx: conversion effect before policy hedges (4.3, 9B.7)
    offset_codes = {LineCode.HEDGE_SETTLEMENT}
    material_series = [f"FX_USD_PER_{c}" for c in CURRENCIES]
    required_params = [*RATIO_KEYS.values(), "fx.inr_brl_obligations_on", "fx.lock_rate_usd_per_unit.INR",
                       "fx.lock_rate_usd_per_unit.BRL", "fx.cad_forward_notional_cad", "fx.cad_weekly_cost_cad"]

    def exposures_and_missing(self, ctx: ProfileContext) -> tuple[list[ExposureItem], list[MissingInput]]:
        start, end = ctx.horizon
        inputs = Inputs(ctx.resolver)
        schedules = inputs.attempt("debt maturity schedule (rates and fx maturities)",
                                   lambda: [build_maturity_schedule(inputs, end)])
        return fx_items(inputs, start, end, schedules[0] if schedules else None), inputs.missing

    def build_exposures(self, ctx: ProfileContext) -> list[ExposureItem]:
        return self.exposures_and_missing(ctx)[0]

    def run(self, ctx: ModuleRunContext, params: ScenarioParams, exposures: list[ExposureItem],
            inputs: ModuleInputs) -> ModuleOutput:
        obligations_on = bool(ctx.resolve("fx.inr_brl_obligations_on"))  # state switch, overridable per run (P66)
        state = emit_lines(ctx, exposures, obligations_on)
        build_specs(ctx, params, state)
        memo_native(ctx, state)
        lines, watch = state.book.build(ctx.horizon[1])
        out = ModuleOutput(cash_lines=lines, watch_list=watch, notes=list(NOTES), policy_hedges=list(state.specs))
        out.diagnostics["missing"] = state.missing
        out.diagnostics["memo_positions_usd"] = dict(state.memo_usd)
        out.diagnostics["memo_positions_native"] = dict(state.memo_native)
        out.diagnostics["inr_brl_obligations_on"] = obligations_on
        out.metrics.update(self._metrics(ctx, state))
        out.diagnostics["derived_inputs"] = state.derived
        return out

    def _metrics(self, ctx: ModuleRunContext, state) -> dict[str, Metric]:
        metrics: dict[str, Metric] = {}
        rates = {}
        stored = ctx.derived_inputs.get("fx")  # replay: values derived from history in the original run (P71)
        derived = {"daily_change_pct": {}, "volatility": {}}
        for ccy in CURRENCIES:
            sid = f"FX_USD_PER_{ccy}"
            if sid in ctx.market:
                rates[ccy] = ctx.market[sid].value
                metrics[f"rate_usd_per_unit.{ccy}"] = Metric(ctx.market[sid].value, "USD per unit", M, f"{sid} at T0")
            if stored:
                change = stored["daily_change_pct"].get(ccy)
                regime = tuple(stored["volatility"][ccy]) if stored["volatility"].get(ccy) else None
            else:
                hist = sorted(ctx.history.get(sid, ()))
                change = (hist[-1][1] / hist[-2][1] - 1) * 100 if len(hist) >= 2 else None
                regime = volatility_regime(hist)
            derived["daily_change_pct"][ccy] = change
            derived["volatility"][ccy] = list(regime) if regime else None
            if change is not None:
                metrics[f"daily_change_pct.{ccy}"] = Metric(change, "pct", M, "last value / previous - 1")
            if regime is not None:
                metrics[f"volatility_regime.{ccy}"] = Metric(regime[0], "class", M,
                                                             f"20-day / 1-year volatility = {regime[1]:.2f}")
        state.derived = derived
        for policy, key in RATIO_KEYS.items():
            metrics[f"hedge_ratio.{policy.value}"] = Metric(float(ctx.resolve(key)), "fraction", A, key)
        if state.memo_native:
            metrics["memo_positions_native"] = Metric({c.value: v for c, v in state.memo_native.items()}, "native",
                                                      D, "USD position / T0 rate; never on the path (D19)")
        if all(c in rates for c in ("EUR", "CNY", "GBP")):
            down, up = sign_check(rates, Decimal("-0.1")), sign_check(rates, Decimal("0.1"))
            metrics["sign_check"] = Metric(down.passed and up.passed, "flag", D,
                                           "implied positions through to_usd reproduce AR p. 216 (+4.0, -8.5, +2.4)")
        return metrics

    def alert_rules(self) -> list[AlertRule]:
        return rules.alert_rules()

    def playbook(self) -> list[PlaybookEntry]:
        return rules.playbook()

    def hedge_targets(self) -> list[HedgeTarget]:
        return [HedgeTarget(p.value, (HedgeInstrument.FORWARD.value,), d) for p, d in TARGETS.items()]


def volatility_regime(history: list[tuple]) -> tuple[str, float] | None:
    """Calm / elevated / stressed from 20-day vs 1-year volatility of daily log changes (4.5)."""
    values = [v for _, v in history]
    if len(values) < 61:
        return None
    changes = [math.log(b / a) for a, b in zip(values, values[1:])]

    def std(xs):
        mean = sum(xs) / len(xs)
        return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))

    long_ = std(changes[-260:])
    if long_ == 0:
        return None
    ratio = std(changes[-20:]) / long_
    label = "stressed" if ratio > rules.VOL_REGIME["stressed"] else "elevated" if ratio >= rules.VOL_REGIME["elevated"] else "calm"
    return label, ratio

