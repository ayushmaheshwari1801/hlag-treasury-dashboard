"""Bunker methodology (descriptive only; spec 7.5). Not read by the calculation."""

from hlag_engine.modules.methodology import Component, Formula, MethodologySpec, Symbol
from hlag_engine.modules.methodology import SymbolRef as R

P, S, A, C, N = R.PARAM, R.SERIES, R.ANCHOR, R.SCENARIO, R.NONE

SPEC = MethodologySpec(
    driver="bunker",
    title="Bunker fuel and MFR",
    summary=(
        "Fuel purchases are each month's consumption priced at that month's average hub price, paid after the "
        "supplier lag.",
        "The price is a blend of Singapore and Rotterdam, with a scrubber share of the fleet burning HSFO instead of "
        "VLSFO.",
        "The marine fuel recovery (MFR) charge passes a share of the fuel price change on to customers with a lag, "
        "per TEU shipped, and is collected like freight.",
        "The figure is the scenario minus the baseline, where the baseline holds the four fuel prices flat at T0; "
        "all fuel lines are in USD, so there is no currency effect.",
        "Hedge settlements appear only when a hypothetical hedge is tested in the scenario; no fuel hedges are "
        "disclosed.",
    ),
    formulas=(
        Formula("Blended price", "P(t) = Σ_h w_h × ((1 − s) × VLSFO_h(t) + s × HSFO_h(t))", (
            Symbol("w_SIN", "Singapore hub weight (Rotterdam = 1 − w_SIN)", P, "bunker.hub_weight_sin"),
            Symbol("s", "scrubber share (burns HSFO)", P, "bunker.scrubber_share"),
            Symbol("VLSFO_h, HSFO_h", "hub price at T0 (the scenario moves it from there)", S, "{fuel}"),
        )),
        Formula("Fuel purchases", "Purchase_m = −C_m × P̄_m, paid L_s days after delivery;  C_m = C0 × (1 + u × a)", (
            Symbol("C0", "base monthly consumption", P, "bunker.monthly_consumption_t"),
            Symbol("P̄_m", "average blended price over month m"),
            Symbol("L_s", "supplier payment lag", P, "bunker.supplier_lag_days"),
            Symbol("u", "consumption uplift in the scenario", C, "bunker.consumption_uplift_pct"),
            Symbol("a", "share of the fleet the uplift applies to", C, "bunker.uplift_affected_share"),
        )),
        Formula("MFR recovery", "MFR_m = σ × (Ref_m − P_base) × f × TEU_m,  Ref_m = P̄ of month m − L", (
            Symbol("σ", "share of the fuel price change recovered through MFR", P, "bunker.mfr_share"),
            Symbol("L", "MFR reference lag", P, "bunker.mfr_lag_months"),
            Symbol("f", "fuel per TEU", P, "bunker.fuel_per_teu_t"),
            Symbol("P_base", "blended anchor price (H1 2026 hub averages)", A, "{fuel}"),
            Symbol("TEU_m", "TEU shipped in month m, read from the freight module"),
        )),
        Formula("Price and consumption effects",
                "price effect = scenario line × C_base / C_scenario − base line;  consumption effect = scenario line × "
                "(1 − C_base / C_scenario);  the two add up to the change in each purchase line", (
            Symbol("C_base, C_scenario", "the month's consumption in the baseline and in the scenario"),
        )),
    ),
    approximation=Formula(
        "Rough approximation",
        "ΔCash ≈ −C0 × ΔP × months paid in the window + σ × f × TEU per month × ΔP × months collected after the "
        "MFR lag", (
            Symbol("C0", "base monthly consumption", P, "bunker.monthly_consumption_t"),
            Symbol("σ", "MFR share", P, "bunker.mfr_share"),
            Symbol("f", "fuel per TEU", P, "bunker.fuel_per_teu_t"),
        )),
    components=(
        Component("price_effect", "Price effect", "Change in fuel purchases from the price move, at base tonnes."),
        Component("consumption_effect", "Consumption effect",
                  "Change in fuel purchases from extra or lower burn, at scenario prices."),
        Component("mfr", "MFR recovery", "Marine fuel recovery collected from customers.", "trade"),
        Component("hedge_settlement", "Hedge settlements", "Hypothetical fuel hedge settlements (hedge tool only)."),
        Component("margin", "Hedge margin", "Margin posted or returned on a hypothetical swap (hedge tool only)."),
    ),
    used_for={
        "bunker.monthly_consumption_t": ("Monthly consumption", "Tonnes bought each month in the baseline."),
        "bunker.fuel_per_teu_t": ("Fuel per TEU", "Converts the fuel price change into the MFR charge per TEU."),
        "bunker.hub_weight_sin": ("Singapore hub weight", "Weights Singapore against Rotterdam in the blended price."),
        "bunker.scrubber_share": ("Scrubber share", "Share of fuel bought as HSFO instead of VLSFO."),
        "bunker.supplier_lag_days": ("Supplier payment lag", "Days from delivery to paying the supplier."),
        "bunker.mfr_share": ("MFR recovery share (σ)", "Share of the fuel price change charged on to customers."),
        "bunker.mfr_lag_months": ("MFR reference lag", "Months between a fuel price and the MFR charge based on it."),
        "bunker.beta_fallback": ("Brent fallback beta", "Fills a stale fuel print from Brent when no beta can be estimated."),
        "bunker.margin_factor": ("Hedge margin factor", "Scales margin on a hypothetical swap (hedge tool only)."),
        "bunker.average_price_paid_h1_usd_per_t": ("Average price paid, H1 2026",
                                                   "Context: compared with the blended anchor price (delivered premium)."),
        "freight.collection_lag_days": ("Collection lag (DSO)", "Days from shipment to MFR cash received."),
        "freight.delay_affected_share": ("Delay-affected share", "Share of MFR collections that wait for a transit delay."),
    },
    market_series={
        "VLSFO_SIN": "VLSFO price, Singapore", "VLSFO_RTM": "VLSFO price, Rotterdam",
        "HSFO_SIN": "HSFO price, Singapore", "HSFO_RTM": "HSFO price, Rotterdam",
        "BRENT": "Fills a stale fuel print (PROXY)",
    },
    limitations=(
        "Monthly consumption and the hub and grade split are register assumptions, not purchase records.",
    ),
)
