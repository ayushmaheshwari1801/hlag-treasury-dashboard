"""Freight methodology (descriptive only; spec 6.5). Not read by the calculation."""

from hlag_engine.modules.methodology import Component, Formula, MethodologySpec, Symbol
from hlag_engine.modules.methodology import SymbolRef as R

P, S, A, N = R.PARAM, R.SERIES, R.ANCHOR, R.NONE

SPEC = MethodologySpec(
    driver="freight",
    title="Freight collections",
    summary=(
        "Freight collections are the revenue from shipments made after T0, received after the collection lag.",
        "Each trade's volume is split into spot, index-linked and contract business: spot and index-linked rates "
        "follow the trade's WCI route index with a lag and a pass-through factor, while contract cohorts change "
        "price only on their reset date.",
        "If the spot rate moves far enough away from a cohort's contract rate, part of that cohort moves to the "
        "spot rate (and part may be lost) and stays there to the end of the horizon.",
        "The figure is the scenario minus the baseline, where the baseline holds every route index flat at its T0 "
        "level; it is measured at T0 exchange rates, and the currency effect on EUR and CNY invoices is shown in "
        "the FX rows.",
        "Volumes and contract cohorts come from the engine's synthetic exposure register, sized on the disclosed "
        "H1 2026 trade volumes and average rates.",
    ),
    formulas=(
        Formula("Index move since the anchor period", "ρ_k(d) = I_k(d) / I_k(anchor) − 1", (
            Symbol("ρ_k(d)", "relative move of trade k's route index on day d"),
            Symbol("I_k(d)", "route index on day d (T0 value shown; the scenario moves it from there)", S, "{index}"),
            Symbol("I_k(anchor)", "route index average over the anchor period (H1 2026)", A, "{index}"),
        )),
        Formula("Spot rate", "P_spot,k(d) = P0_k × (1 + β × ρ_k(d − L_p))", (
            Symbol("P0_k", "base rate per TEU for trade k in the anchor period", P, "freight.p0.{trade}"),
            Symbol("β", "spot pass-through of the index move", P, "freight.beta"),
            Symbol("L_p", "pricing lag", P, "freight.pricing_lag_days"),
        )),
        Formula("Index-linked rate", "P_index,k(d) = P0_k × (1 + κ × ρ_k(d − L_i))", (
            Symbol("κ", "index-linked clause pass-through", P, "freight.kappa"),
            Symbol("L_i", "index clause lag", P, "freight.clause_lag_days"),
        )),
        Formula("Contract reset", "P_reset,c = P0_k × (1 + γ × ρ_k(reset date of cohort c))", (
            Symbol("γ", "share of the index move passed into the new contract rate at reset", P, "freight.gamma"),
        )),
        Formula("Leakage", "if P_spot / P_contract − 1 < −g_down: s_down of the cohort moves to spot and λ is lost; "
                           "if P_spot / P_contract − 1 > g_up: s_up moves to spot", (
            Symbol("g_down", "gap that triggers downward leakage", P, "freight.leakage_gap_down"),
            Symbol("g_up", "gap that triggers upward leakage", P, "freight.leakage_gap_up"),
            Symbol("s_down", "share of the cohort that moves to spot when spot falls", P, "freight.leakage_share_down"),
            Symbol("s_up", "share of the cohort that moves to spot when spot rises", P, "freight.leakage_share_up"),
            Symbol("λ", "share of the cohort's volume lost", P, "freight.leakage_volume_loss"),
        )),
        Formula("Revenue and cash", "Revenue_k(d) = V_k(d) × Σ_class share × rate;  cash on d + DSO (+ transit delay on "
                                    "the share δ);  a share n_k is invoiced in the trade's currency at the T0 rate", (
            Symbol("V_k(d)", "TEU shipped on day d (synthetic register)"),
            Symbol("contract share", "share of volume on contract cohorts", P, "freight.contract_share"),
            Symbol("index-linked share", "share of volume on index-linked terms", P, "freight.index_linked_share"),
            Symbol("DSO", "collection lag", P, "freight.collection_lag_days"),
            Symbol("δ", "share of shipments affected by a transit delay", P, "freight.delay_affected_share"),
            Symbol("n_k", "share invoiced in the trade's own currency", P, "freight.non_usd_share.{trade}"),
        )),
    ),
    approximation=Formula(
        "Rough approximation",
        "ΔCash ≈ revenue collected in the window × (spot share × β + index-linked share × κ + share resetting × γ) "
        "× index move", (
            Symbol("β", "spot pass-through", P, "freight.beta"),
            Symbol("κ", "index-linked pass-through", P, "freight.kappa"),
            Symbol("γ", "reset pass-through", P, "freight.gamma"),
        )),
    components=(
        Component("spot", "Spot business", "Spot share of each trade's volume at the lagged spot rate.", "trade"),
        Component("index", "Index-linked business", "Index-linked share at the lagged index clause rate.", "trade"),
        Component("contract", "Contract cohorts (resets, net of leakage)",
                  "Contract cohorts: the new rate from each reset date on, less the volume that left the cohort.",
                  "trade"),
        Component("leaked", "Leaked volume now at spot",
                  "Contract volume that moved to the spot rate after a leakage trigger.", "trade"),
    ),
    used_for={
        "freight.beta": ("Spot pass-through (β)", "How much of the route index move reaches the spot rate."),
        "freight.pricing_lag_days": ("Pricing lag", "Days between an index move and the spot rate charged."),
        "freight.kappa": ("Index clause pass-through (κ)", "How much of the index move reaches index-linked contracts."),
        "freight.clause_lag_days": ("Index clause lag", "Days between an index move and the index-linked rate."),
        "freight.gamma": ("Reset pass-through (γ)", "Share of the index move built into a contract rate on its reset date."),
        "freight.leakage_gap_down": ("Leakage trigger (spot below contract)", "Gap at which customers move contract volume to spot."),
        "freight.leakage_gap_up": ("Leakage trigger (spot above contract)", "Gap at which contract volume is moved to spot."),
        "freight.leakage_share_down": ("Leakage share (spot falls)", "Share of a cohort that moves to spot when spot falls."),
        "freight.leakage_share_up": ("Leakage share (spot rises)", "Share of a cohort that moves to spot when spot rises."),
        "freight.leakage_volume_loss": ("Volume lost on leakage", "Share of a cohort's volume lost when spot falls."),
        "freight.contract_share": ("Contract share", "Splits each trade's volume into contract cohorts."),
        "freight.index_linked_share": ("Index-linked share", "Splits each trade's volume into index-linked business."),
        "freight.delay_affected_share": ("Delay-affected share", "Share of shipments whose collection waits for a transit delay."),
        "freight.collection_lag_days": ("Collection lag (DSO)", "Days from shipment to cash received."),
        "freight.growth_g": ("Volume growth", "Grows prior-year quarterly volumes into the horizon."),
        **{f"freight.non_usd_share.{t}": (f"Non-USD invoicing share, {t.replace('_', ' ').title()}",
                                          "Share of the trade's revenue invoiced in its own currency.")
           for t in ("ASIA_EUROPE", "ASIA_AMERICA", "EUROPE_AMERICA", "AFRICA_INTRA")},
        **{f"freight.p0.{t}": (f"Base rate, {t.replace('_', ' ').title()}", "Starting rate per TEU before any index move.")
           for t in ("ASIA_EUROPE", "ASIA_AMERICA", "EUROPE_AMERICA", "AFRICA_INTRA")},
        **{f"freight.h1_volume_teu.{t}": (f"H1 2026 volume, {t.replace('_', ' ').title()}",
                                          "Sizes the trade's daily TEU and weights the composite index.")
           for t in ("ASIA_EUROPE", "ASIA_AMERICA", "EUROPE_AMERICA", "AFRICA_INTRA")},
    },
    market_series={
        "WCI_SHA_RTM": "Asia–Europe route index",
        "WCI_SHA_LAX": "Asia–America route index",
        "WCI_RTM_NYC": "Europe–America route index",
        "WCI_COMPOSITE": "Africa & Intraregional route index (proxy: Drewry composite)",
    },
    limitations=(
        "Volumes, contract cohorts and their reset dates are synthetic register items, not the live booking book.",
        "Africa & Intraregional has no route index of its own; the Drewry WCI composite is used as a proxy.",
    ),
)
