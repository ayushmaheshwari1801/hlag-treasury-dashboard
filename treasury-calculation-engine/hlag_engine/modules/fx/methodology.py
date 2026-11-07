"""FX methodology (descriptive only; spec 9A and 9B). Not read by the calculation."""

from hlag_engine.modules.methodology import Component, Formula, MethodologySpec, Symbol
from hlag_engine.modules.methodology import SymbolRef as R

P, S, A, C, N = R.PARAM, R.SERIES, R.ANCHOR, R.SCENARIO, R.NONE

SPEC = MethodologySpec(
    driver="fx",
    title="FX",
    summary=(
        "The FX rows show what currency moves do to cash, measured in USD.",
        "Every non-USD line from freight and interest is converted at the scenario exchange rate instead of the T0 "
        "rate; that difference is its conversion effect, shown here by currency rather than in the driver's row.",
        "FX's own exposures (CAD and EUR operating costs, the EUR dividend, the EUR share of debt principal, INR and "
        "BRL obligations) move entirely with the exchange rate.",
        "Policy hedges (the CAD 13-week programme, the EUR debt principal hedge, INR and BRL locked at the "
        "31 Dec 2025 rate) settle against that move and offset part of it.",
        "CNY and GBP positions are balance-sheet translation memos, never cash and never on the liquidity path.",
    ),
    formulas=(
        Formula("Conversion effect", "Conversion effect = native amount × (S_scenario(d) − S_base)", (
            Symbol("S_base", "USD per unit at T0 (held flat in the baseline)", S, "FX_USD_PER_{ccy}"),
            Symbol("S_scenario(d)", "USD per unit on day d in the scenario"),
        )),
        Formula("Scenario rate", "S_scenario(d) = S(T0) × (1 + move × f(d))", (
            Symbol("move", "scenario move of the currency against USD", C, "fx.move_pct"),
            Symbol("f(d)", "scenario shape on day d (step, ramp or revert)"),
        )),
        Formula("Policy hedge settlement", "Settlement = h × native amount × (S(value date) − K)", (
            Symbol("h_CAD", "CAD programme hedge ratio (capped at 80%)", P, "fx.hedge_ratio.cad"),
            Symbol("h_EUR debt", "EUR debt principal hedge ratio", P, "fx.hedge_ratio.eur_debt"),
            Symbol("h_INR/BRL", "INR and BRL obligation hedge ratio", P, "fx.hedge_ratio.inr_brl"),
            Symbol("h_EUR opex", "EUR operating cost hedge ratio", P, "fx.hedge_ratio.eur_operating_costs"),
            Symbol("h_EUR dividend", "EUR dividend hedge ratio", P, "fx.hedge_ratio.eur_dividend"),
            Symbol("K_INR", "INR lock rate (31 Dec 2025)", P, "fx.lock_rate_usd_per_unit.INR"),
            Symbol("K_BRL", "BRL lock rate (31 Dec 2025)", P, "fx.lock_rate_usd_per_unit.BRL"),
        )),
        Formula("Translation memo (not cash)", "Memo = position × (S_scenario(horizon end) / S_base − 1)", (
            Symbol("CNY position", "long CNY balance-sheet position", P, "fx.memo_position_usd.CNY"),
            Symbol("GBP position", "short GBP balance-sheet position", P, "fx.memo_position_usd.GBP"),
        )),
    ),
    approximation=Formula(
        "Rough approximation",
        "ΔCash ≈ Σ (unhedged native flows in the window) × S(T0) × move", (
            Symbol("h_CAD", "CAD programme hedge ratio", P, "fx.hedge_ratio.cad"),
        )),
    components=(
        Component("conversion", "Conversion effect on other modules' lines",
                  "Currency effect on freight and interest lines in this currency.", "module"),
        Component("own_exposure", "FX's own exposures",
                  "Operating costs, dividend, EUR debt principal and INR/BRL obligations.", "line type"),
        Component("policy_hedge", "Policy hedges", "Settlements of the policy forwards on those exposures.", "policy"),
    ),
    used_for={
        "fx.hedge_ratio.cad": ("CAD hedge ratio", "Share of CAD operating costs covered by the 13-week programme."),
        "fx.hedge_ratio.eur_debt": ("EUR debt hedge ratio", "Share of EUR debt principal hedged."),
        "fx.hedge_ratio.inr_brl": ("INR/BRL hedge ratio", "Share of INR and BRL obligations locked."),
        "fx.hedge_ratio.eur_operating_costs": ("EUR operating cost hedge ratio", "Share of EUR operating costs hedged."),
        "fx.hedge_ratio.eur_dividend": ("EUR dividend hedge ratio", "Share of the EUR dividend hedged."),
        "fx.inr_brl_obligations_on": ("INR/BRL obligations", "Switches the INR and BRL obligation lines on or off."),
        "fx.lock_rate_usd_per_unit.INR": ("INR lock rate", "Strike of the INR obligation hedge."),
        "fx.lock_rate_usd_per_unit.BRL": ("BRL lock rate", "Strike of the BRL obligation hedge."),
        "fx.cad_forward_notional_cad": ("CAD forward notional", "Disclosed size of the CAD hedge programme."),
        "fx.cad_weekly_cost_cad": ("CAD weekly cost", "Sizes the weekly CAD operating cost lines."),
        "fx.eur_operating_cost_per_quarter_eur": ("EUR operating costs", "Sizes the EUR operating cost lines."),
        "fx.dividend_reference_eur": ("EUR dividend", "Sizes the dividend line."),
        "fx.dividend_date": ("Dividend date", "Date of the dividend payment."),
        "fx.inr_obligation_inr": ("INR obligations", "Sizes the INR obligation lines."),
        "fx.brl_obligation_brl": ("BRL obligations", "Sizes the BRL obligation lines."),
        "fx.memo_position_usd.CNY": ("CNY position", "Translation memo only; never cash."),
        "fx.memo_position_usd.GBP": ("GBP position", "Translation memo only; never cash."),
        "freight.non_usd_share.ASIA_EUROPE": ("Non-USD invoicing share, Asia Europe", "EUR freight lines converted here."),
        "freight.non_usd_share.ASIA_AMERICA": ("Non-USD invoicing share, Asia America", "CNY freight lines converted here."),
        "freight.non_usd_share.EUROPE_AMERICA": ("Non-USD invoicing share, Europe America", "EUR freight lines converted here."),
        "freight.non_usd_share.AFRICA_INTRA": ("Non-USD invoicing share, Africa Intra", "EUR freight lines converted here."),
        "rates.floating_split_usd": ("USD floating share", "The EUR remainder is converted here."),
        "rates.deposit_split_usd": ("USD deposit share", "The EUR remainder is converted here."),
    },
    market_series={f"FX_USD_PER_{c}": f"USD per {c}" for c in ("EUR", "CAD", "GBP", "CNY", "INR", "BRL")},
    limitations=(
        "Own exposures are synthetic register items sized on disclosures; forward points are ignored.",
    ),
)
