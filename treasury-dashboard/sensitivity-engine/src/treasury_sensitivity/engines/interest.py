"""EUR interest-rate module (spec s5). Two separately labelled modes; Mode A is never scaled by horizon."""
from __future__ import annotations

from decimal import Decimal

from ..market import historical_volatility
from ..models import D, ImpactType, InputSpec, Provenance, Quantity, TreasuryAction, ValidationProblem
from .common import EngineContext, ScenarioRequest, build_result, make_row, resolve_inputs, table_shocks

FORMULA_A = ("Mode A (disclosed earnings-sensitivity proxy): bps >= 0: dE = -20.8 x bps/100; bps < 0: dE = +21.5 x |bps|/100 "
             "(EUR m, annualised pre-tax EARNINGS proxy, not cash, not scaled by horizon).")
FORMULA_B = "Mode B (ILLUSTRATIVE cash interest): dI = P x (1 - hedged) x dr x days/365; cash effect = -dI for floating debt, +dI for floating deposits."


def earnings_proxy(bps: Decimal, up_per_100: Decimal, down_per_100: Decimal) -> Decimal:
    """Mode A. Asymmetric: the disclosed +100 and -100 bps sensitivities differ."""
    return up_per_100 * bps / 100 if bps >= 0 else down_per_100 * abs(bps) / 100


def cash_interest(principal: Decimal, hedged_fraction: Decimal, bps: Decimal, days: Decimal, position_type: str) -> Decimal:
    """Mode B, EUR m. Positive = favourable cash effect."""
    delta_i = principal * (1 - hedged_fraction) * (bps / 10000) * days / 365
    return -delta_i if position_type == "floating_debt" else delta_i


def compute(ctx: EngineContext, req: ScenarioRequest):
    cfg = ctx.defaults["interest"]
    mode = (req.mode or cfg["default_mode"]).upper()
    if mode not in ("A", "B"):
        raise ValidationProblem("Interest mode must be 'A' or 'B'")
    bench = req.benchmark or cfg["series"]
    if bench not in cfg["alt_series"]:
        raise ValidationProblem(f"benchmark must be one of {', '.join(cfg['alt_series'])}")
    market = ctx.blocks[bench]
    position_type = req.options.get("position_type", cfg["mode_b"]["position_type"])
    if position_type not in ("floating_debt", "floating_deposit"):
        raise ValidationProblem("position_type must be floating_debt or floating_deposit")
    lo, hi = (D(x) for x in cfg["shock_bounds_bps"])
    observed = None if market.change is None or market.change_unit != "bps" else D(market.change)
    fallback = D(cfg["fallback_shock_bps"])
    months = int(req.overrides.get("horizon_months", cfg["default_horizon_months"]))
    if str(months) not in cfg["horizon_months_days"]:
        raise ValidationProblem("horizon_months must be 3, 6 or 12")
    horizon_days = cfg["horizon_months_days"][str(months)]

    if req.headline_shock is not None and "shock_bps" in req.overrides and D(req.headline_shock) != D(req.overrides["shock_bps"]):
        raise ValidationProblem("headline_shock and shock_bps disagree; send one of them")
    overrides = {k: v for k, v in req.overrides.items() if k != "horizon_months"}
    if req.headline_shock is not None:
        overrides["shock_bps"] = req.headline_shock
    specs = [
        InputSpec(key="shock_bps", label="Rate change", unit="bps", default=float(observed if observed not in (None, 0) else fallback),
                  minimum=float(lo), maximum=float(hi), provenance=market.provenance if observed not in (None, 0) else Provenance.ILLUSTRATIVE,
                  note="Observed change vs previous comparable observation" if observed not in (None, 0) else "No observed change: reference stress"),
        InputSpec(key="disclosed_up_per_100bps", label="Disclosed sensitivity, +100 bps", unit="EUR m", default=cfg["disclosed_up_per_100bps"],
                  editable=False, provenance=Provenance.DISCLOSED_HISTORICAL, note=cfg["disclosed_source"]),
        InputSpec(key="disclosed_down_per_100bps", label="Disclosed sensitivity, -100 bps", unit="EUR m", default=cfg["disclosed_down_per_100bps"],
                  editable=False, provenance=Provenance.DISCLOSED_HISTORICAL, note=cfg["disclosed_source"]),
        InputSpec(key="principal_eur_m", label="Floating-rate principal (Mode B)", unit="EUR m", default=cfg["mode_b"]["principal_eur_m"],
                  minimum=0, maximum=1_000_000, provenance=Provenance.ILLUSTRATIVE,
                  note="No default: debt schedule not supplied. Mode B is UNAVAILABLE until given."),
        InputSpec(key="hedged_fraction", label="Share fixed by swaps (Mode B)", unit="fraction", default=cfg["mode_b"]["hedged_fraction"],
                  minimum=0, maximum=1, provenance=Provenance.ILLUSTRATIVE),
        InputSpec(key="days_affected", label="Days affected (Mode B)", unit="days", default=horizon_days, minimum=1, maximum=3650,
                  provenance=Provenance.ILLUSTRATIVE, note="From the selected horizon unless overridden; adjust for reset dates."),
    ]
    values, shown = resolve_inputs(specs, overrides)
    base = values["shock_bps"]
    up, down = values["disclosed_up_per_100bps"], values["disclosed_down_per_100bps"]
    principal, hedged, days = values["principal_eur_m"], values["hedged_fraction"], values["days_affected"]

    def check(s: Decimal) -> Decimal:
        if not lo <= s <= hi:
            raise ValidationProblem(f"Rate shock {s} bps is outside the allowed range {lo} to {hi} bps")
        return s

    missing: list[str] = []
    if mode == "B" and principal is None:
        missing.append("floating-rate principal / debt schedule")
    mode_prov = Provenance.ILLUSTRATIVE if mode == "B" else (
        Provenance.ILLUSTRATIVE if market.provenance == Provenance.ILLUSTRATIVE else Provenance.DERIVED)
    prov = Provenance.UNAVAILABLE if missing else mode_prov

    def impact(bps: Decimal) -> Decimal | None:
        if missing:
            return None
        if mode == "A":
            return earnings_proxy(bps, up, down)
        return cash_interest(principal, hedged, bps, days, position_type)

    comparable = mode == "B"
    unit = "EUR m (annualised earnings proxy)" if mode == "A" else "EUR m (cash)"
    derived, omitted = table_shocks(req, check(base), cfg["table_multipliers"], cfg["table_labels"], (lo, hi))
    shocks = [(f"{lbl} ({float(s):+g} bps)" if req.shocks is None else lbl, check(s)) for lbl, s in derived]

    def row(label, s):
        return make_row(ctx, req, label=label, shock=s, shock_unit="bps", impact=impact(s), impact_unit=unit, currency="EUR",
                        places=2, comparable_to_liquidity=comparable, prov=prov, extra_missing=missing)

    rows = [row(l, s) for l, s in shocks]
    headline = row(f"Selected ({float(base):+g} bps)", check(base))

    warnings = []
    if omitted:
        warnings.append("Stress rows outside the allowed range were omitted: " + ", ".join(omitted))
    if mode == "A" and abs(base) > 100:
        warnings.append("Shock beyond +/-100 bps is a linear extrapolation of the disclosed +/-100 bps sensitivities")
    if mode == "A":
        warnings.append("Mode A is an annualised EARNINGS proxy, not cash, and is deliberately not scaled by the 3/6/12-month horizon")
    if comparable and ctx.eurusd is None:
        warnings.append("No sourced, dated EUR/USD rate: EUR impact cannot be compared with USD liquidity")
    if bench == "ECB_DFR":
        warnings.append("ECB deposit facility rate is a policy benchmark: not Euribor and not the company borrowing rate")

    vol = historical_volatility(ctx.series[bench], cfg["volatility_window"])
    suggested = {"stdev_bps": None if vol is None else float(round(vol, 3)), "window_observations": cfg["volatility_window"],
                 "multiples": [1, 2, 3], "applied": False,
                 "note": "Standard deviation of consecutive-observation changes; a suggestion only, enter it as a shock to use it."}
    intermediate = [
        Quantity.of("Observed change", observed, "bps", market.provenance, places=1),
        Quantity.of("Selected change", base, "bps", Provenance.ILLUSTRATIVE if "shock_bps" in overrides else prov, places=1),
        Quantity.of("Mode A earnings proxy at selected change", None if missing and mode == "B" else earnings_proxy(base, up, down),
                    "EUR m", Provenance.DERIVED, places=2, note="Annualised earnings proxy, not cash"),
        Quantity.of("Mode B effective principal", None if principal is None else principal * (1 - hedged), "EUR m", Provenance.ILLUSTRATIVE, places=2),
        Quantity.of("Rate change as decimal", base / 10000, "decimal", Provenance.DERIVED, places=6),
        Quantity.of("Day-count fraction", None if days is None else days / 365, "years", Provenance.DERIVED, places=4),
    ]
    actions: list[TreasuryAction] = []
    if base > 0:
        actions.append(TreasuryAction(rule_id="IR-A1", condition="If the rate increase is material",
                       action="Review floating-rate debt, swaps and maturities."))
        actions.append(TreasuryAction(rule_id="IR-A2", condition="If material floating-rate exposure is unhedged",
                       action="Evaluate pay-fixed/receive-floating swaps, subject to cost and policy.",
                       assumptions=["Hedge ratio unknown in Mode A; supply the debt schedule to test"]))
    elif base < 0:
        actions.append(TreasuryAction(rule_id="IR-A3", condition="If rates fall", action="Assess refinancing economics."))
    if principal is None:
        actions.append(TreasuryAction(rule_id="IR-A4", condition="Debt schedule is missing",
                       action="Request validation of the floating-rate debt schedule; this is a data request, not transaction advice."))
    return build_result(
        ctx, req, module="interest", title="EUR interest rates", impact_type=ImpactType.INTEREST_EARNINGS if mode == "A" else ImpactType.INTEREST_CASH,
        horizon_days=int(days) if mode == "B" and days is not None else horizon_days, market_key=bench, inputs=shown, intermediate=intermediate,
        formula=FORMULA_A if mode == "A" else FORMULA_B,
        methodology=["1. bps = (latest % - previous comparable %) x 100, or the user-selected shock.",
                     "2. Mode A applies the disclosed asymmetric sensitivities (-20.8 per +100 bps; +21.5 per -100 bps), linearly.",
                     "3. Mode B applies principal x rate change x days/365, adjusted for the swap-fixed share; the sign follows the position type.",
                     "4. Mode A has no cash denominator: status needs an approved earnings limit, otherwise NOT ASSESSABLE. Mode B compares adverse USD with liquidity."],
        headline=headline, rows=rows,
        disclosed_reference=[Quantity.of("Disclosed +100 bps earnings sensitivity", up, "EUR m", Provenance.DISCLOSED_HISTORICAL, places=1, note=cfg["disclosed_source"]),
                             Quantity.of("Disclosed -100 bps earnings sensitivity", down, "EUR m", Provenance.DISCLOSED_HISTORICAL, places=1, note=cfg["disclosed_source"])],
        actions=actions, warnings=warnings, mode=mode,
        labels={"impact": "Annualised pre-tax EARNINGS proxy, not cash" if mode == "A" else "ILLUSTRATIVE cash interest", "benchmark": bench},
        extra={"suggested_stress": suggested, "position_type": position_type, "horizon_months": months},
        reliability_extra=["Benchmark movement is not a direct debt-coupon or payment forecast"])
