"""Freight module (spec s7): dSCFI = (latest - previous)/previous; dF = k x dSCFI; dCF = F0 x V x dF x c."""
from __future__ import annotations

from decimal import Decimal

from ..models import D, ImpactType, InputSpec, Provenance, Quantity, TreasuryAction, ValidationProblem, combine_provenance
from .common import EngineContext, ScenarioRequest, build_result, horizon_scale, make_row, resolve_inputs, table_shocks

FORMULA = ("dSCFI = (SCFI_latest - SCFI_previous)/SCFI_previous. dF = k x dSCFI. dCF = F0 x V x dF x c "
           "(USD m: F0 in USD/TEU x V in million TEU). Positive = favourable (higher revenue-to-cash).")


def freight_cash_proxy(f0: Decimal, v_m_teu: Decimal, k: Decimal, c: Decimal, scfi_change_pct: Decimal) -> Decimal:
    return f0 * v_m_teu * (k * scfi_change_pct / 100) * c


def compute(ctx: EngineContext, req: ScenarioRequest):
    cfg = ctx.defaults["freight"]
    market = ctx.blocks[cfg["series"]]
    lo, hi = (D(x) for x in cfg["shock_bounds_pct"])
    observed = None
    if market.latest_value is not None and market.previous_value not in (None, 0):
        observed = (D(market.latest_value) - D(market.previous_value)) / D(market.previous_value) * 100
    use_obs = observed not in (None, 0)
    if req.headline_shock is not None and "scfi_change_pct" in req.overrides and D(req.headline_shock) != D(req.overrides["scfi_change_pct"]):
        raise ValidationProblem("headline_shock and scfi_change_pct disagree; send one of them")
    overrides = dict(req.overrides)
    if req.headline_shock is not None:
        overrides["scfi_change_pct"] = req.headline_shock
    scale, scale_note = horizon_scale(overrides, cfg["horizon_days"])
    specs = [
        InputSpec(key="scfi_change_pct", label="SCFI change", unit="%", default=float(observed if use_obs else D(cfg["fallback_shock_pct"])),
                  minimum=float(lo), maximum=float(hi), provenance=market.provenance if use_obs else Provenance.ILLUSTRATIVE,
                  note="Observed change since the prior comparable publication" if use_obs else "No observed change: reference stress"),
        InputSpec(key="transmission_k", label="Transmission k (SCFI to realised rate)", unit="fraction", default=cfg["transmission_k"],
                  minimum=0, maximum=1, provenance=Provenance.ILLUSTRATIVE),
        InputSpec(key="cash_collection_c", label="Cash-collection fraction c", unit="fraction", default=cfg["cash_collection_c"],
                  minimum=0, maximum=1, provenance=Provenance.ILLUSTRATIVE),
        InputSpec(key="volume_m_teu", label="Horizon-matched volume V", unit="million TEU", default=str(D(cfg["volume_m_teu"]) * scale),
                  minimum=0, maximum=100, provenance=Provenance.ILLUSTRATIVE,
                  note="H1 historical volume is not a forecast; match it to the horizon."),
        InputSpec(key="realised_rate_usd_teu", label="Realised rate F0", unit="USD/TEU", default=cfg["realised_rate_usd_teu"],
                  minimum=0, maximum=100000, provenance=Provenance.ILLUSTRATIVE, note="Not the SCFI index level."),
        InputSpec(key="horizon_days", label="Horizon", unit="days", default=cfg["horizon_days"], minimum=1, maximum=730,
                  provenance=Provenance.ILLUSTRATIVE),
    ]
    values, shown = resolve_inputs(specs, overrides)
    base, k, c = values["scfi_change_pct"], values["transmission_k"], values["cash_collection_c"]
    v, f0, horizon = values["volume_m_teu"], values["realised_rate_usd_teu"], int(values["horizon_days"])
    prov = combine_provenance(*(i.quantity.provenance for i in shown if i.key != "horizon_days"))

    def check(s: Decimal) -> Decimal:
        if not lo <= s <= hi:
            raise ValidationProblem(f"SCFI change {s}% is outside the allowed range {lo}% to {hi}%")
        return s

    def row(label, s):
        return make_row(ctx, req, label=label, shock=s, shock_unit="%", impact=freight_cash_proxy(f0, v, k, c, s),
                        impact_unit="USD m", currency="USD", places=1, comparable_to_liquidity=True, prov=prov)

    derived, omitted = table_shocks(req, check(base), cfg["table_multipliers"], cfg["table_labels"], (lo, hi))
    grid = [(f"{l} ({float(check(s)):+g}%)" if req.shocks is None else l, s) for l, s in derived]
    if req.shocks is None:
        grid += [(f"Downside stress ({float(D(p)):+g}%)", check(D(p))) for p in cfg["downside_pct"]]
    rows = [row(l, s) for l, s in grid]
    headline = row(f"Selected ({float(check(base)):+g}%)", base)
    disclosed = [Quantity.of(i["label"], D(i["value"]), i["unit"], Provenance.DISCLOSED_HISTORICAL, places=1,
                             note=cfg["disclosed_source"]) for i in cfg["disclosed_reference"]]
    actions = []
    if base < 0:
        actions.append(TreasuryAction(rule_id="FR-A1", condition="If freight rates fall",
                                      action="Review collection and cash forecasts, funding headroom and liquidity buffers."))
    return build_result(
        ctx, req, module="freight", title="Container freight (SCFI)", impact_type=ImpactType.FREIGHT_CASH, horizon_days=horizon,
        market_key=cfg["series"], inputs=shown,
        intermediate=[Quantity.of("Observed dSCFI", observed, "%", market.provenance, places=3),
                      Quantity.of("Assumed realised-rate move dF = k x dSCFI", k * base, "%", prov, places=3),
                      Quantity.of("Revenue base F0 x V", f0 * v, "USD m", prov, places=1)],
        formula=FORMULA,
        methodology=["1. dSCFI = (latest - previous)/previous, or the selected stress.", "2. dF = k x dSCFI.",
                     "3. dCF = F0 x V x dF x c.",
                     "4. Downside rows are an engine addition: the specification's table is upside-only, but falling rates are the adverse case."],
        headline=headline, rows=rows, disclosed_reference=disclosed, actions=actions,
        warnings=[*(["Stress rows outside the allowed range were omitted: " + ", ".join(omitted)] if omitted else []),
                  *([scale_note] if scale_note and "volume_m_teu" not in overrides else []),
                  "SCFI is an index, not the company realised freight rate; do not mix it with FBX",
                  "Reported H1 volume is historical and not automatically a forecast for the horizon",
                  "Bunker surcharge recovery may already sit inside realised rates; do not count it twice in compound scenarios"],
        labels={"impact": "ILLUSTRATIVE revenue-to-cash proxy (USD m); positive = higher cash"},
        reliability_extra=["Transmission, volume and collection are assumptions, not a revenue forecast"])
