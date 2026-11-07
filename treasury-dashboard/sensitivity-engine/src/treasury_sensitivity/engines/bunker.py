"""Rotterdam VLSFO 0.5% module (spec s6): dCF = -dP x Q x (1 - r) x t."""
from __future__ import annotations

from decimal import Decimal

from ..models import D, ImpactType, InputSpec, Provenance, Quantity, TreasuryAction, ValidationProblem, combine_provenance
from .common import EngineContext, ScenarioRequest, build_result, horizon_scale, make_row, resolve_inputs, table_shocks

FORMULA = ("dP = P_latest - P_previous (USD/t). dCF = -dP x Q x (1 - r) x t (USD m; Q in million tonnes). "
           "Q = assumed consumption over the horizon, r = customer recovery fraction, t = share of the net cost paid in the horizon. "
           "Assumes the price change persists across Q. Positive = favourable.")


def bunker_cash_effect(dp: Decimal, q_mt: Decimal, r: Decimal, t: Decimal) -> Decimal:
    return -dp * q_mt * (1 - r) * t


def compute(ctx: EngineContext, req: ScenarioRequest):
    cfg = ctx.defaults["bunker"]
    market = ctx.blocks[cfg["series"]]
    lo, hi = (D(x) for x in cfg["shock_bounds_usd_t"])
    observed = None
    if market.latest_value is not None and market.previous_value is not None:
        observed = D(market.latest_value) - D(market.previous_value)
    use_obs = observed not in (None, 0)
    if req.headline_shock is not None and "price_shock" in req.overrides and D(req.headline_shock) != D(req.overrides["price_shock"]):
        raise ValidationProblem("headline_shock and price_shock disagree; send one of them")
    overrides = dict(req.overrides)
    if req.headline_shock is not None:
        overrides["price_shock"] = req.headline_shock
    scale, scale_note = horizon_scale(overrides, cfg["horizon_days"])
    specs = [
        InputSpec(key="price_shock", label="Price change dP", unit="USD/t", default=float(observed if use_obs else D(cfg["fallback_shock_usd_t"])),
                  minimum=float(lo), maximum=float(hi), provenance=market.provenance if use_obs else Provenance.ILLUSTRATIVE,
                  note="Observed change vs previous comparable observation" if use_obs else "No observed change: reference stress"),
        InputSpec(key="consumption_mt", label="Consumption Q over horizon", unit="million tonnes", default=str(D(cfg["consumption_mt"]) * scale),
                  minimum=0, maximum=1000, provenance=Provenance.ILLUSTRATIVE, note="Not a verified procurement forecast."),
        InputSpec(key="recovery_fraction", label="Customer recovery fraction r", unit="fraction", default=cfg["recovery_fraction"],
                  minimum=0, maximum=1, provenance=Provenance.ILLUSTRATIVE),
        InputSpec(key="cash_factor", label="Cash-payment factor t", unit="fraction", default=cfg["cash_factor"],
                  minimum=0, maximum=1, provenance=Provenance.ILLUSTRATIVE),
        InputSpec(key="horizon_days", label="Horizon", unit="days", default=cfg["horizon_days"], minimum=1, maximum=730,
                  provenance=Provenance.ILLUSTRATIVE, note="Q must be the consumption over this same horizon."),
    ]
    values, shown = resolve_inputs(specs, overrides)
    base, q, r, t = values["price_shock"], values["consumption_mt"], values["recovery_fraction"], values["cash_factor"]
    horizon = int(values["horizon_days"])
    prov = combine_provenance(*(i.quantity.provenance for i in shown if i.key != "horizon_days"))

    def check(s: Decimal) -> Decimal:
        if not lo <= s <= hi:
            raise ValidationProblem(f"Price shock {s} USD/t is outside the allowed range {lo} to {hi}")
        return s

    def impact(dp: Decimal) -> Decimal:
        return bunker_cash_effect(dp, q, r, t)

    def row(label, s):
        return make_row(ctx, req, label=label, shock=s, shock_unit="USD/t", impact=impact(s), impact_unit="USD m", currency="USD",
                        places=3, comparable_to_liquidity=True, prov=prov)

    derived, omitted = table_shocks(req, check(base), cfg["table_multipliers"], cfg["table_labels"], (lo, hi))
    rows = [row(f"{l} ({float(s):+g} USD/t)" if req.shocks is None else l, check(s)) for l, s in derived]
    headline = row(f"Selected ({float(check(base)):+g} USD/t)", base)
    disclosed = [Quantity.of(i["label"], D(i["value"]), i["unit"], Provenance.DISCLOSED_HISTORICAL, places=1,
                             note=cfg["disclosed_source"] + ". To verify; never added to scenario output.") for i in cfg["disclosed_reference"]]
    actions = []
    if base > 0:
        actions.append(TreasuryAction(rule_id="BK-A1", condition="If the price rise is material",
                       action="Review procurement, surcharge recovery, inventory funding and the cash forecast; run a combined liquidity stress.",
                       assumptions=["No automatic bunker derivative recommendation is made"]))
    return build_result(
        ctx, req, module="bunker", title="Rotterdam VLSFO 0.5%", impact_type=ImpactType.BUNKER_CASH, horizon_days=horizon,
        market_key=cfg["series"], inputs=shown,
        intermediate=[Quantity.of("Observed dP", observed, "USD/t", market.provenance, places=2),
                      Quantity.of("Net cost-bearing volume Q x (1 - r) x t", q * (1 - r) * t, "million tonnes", prov, places=4),
                      Quantity.of("Selected dP", base, "USD/t", prov, places=2)],
        formula=FORMULA,
        methodology=["1. dP = latest - previous comparable Rotterdam VLSFO observation, or the selected shock.",
                     "2. Net volume = Q x (1 - r) x t.", "3. dCF = -dP x net volume (USD m).",
                     "4. Disclosed H1 2026 costs are shown separately and are never added to the scenario."],
        headline=headline, rows=rows, disclosed_reference=disclosed, actions=actions,
        warnings=["Rotterdam VLSFO is a market benchmark, not the company procurement price",
                  "Assumes the price change persists across the whole assumed consumption Q",
                  *(["Stress rows outside the allowed range were omitted: " + ", ".join(omitted)] if omitted else []),
                  *([scale_note] if scale_note and "consumption_mt" not in overrides else [])],
        labels={"impact": "ILLUSTRATIVE bunker cash cost (USD m); negative = cost increase"},
        reliability_extra=["Consumption, recovery and payment timing are assumptions, not a procurement forecast"])
