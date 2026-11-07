"""Interest rate methodology (descriptive only; spec 8.5). Not read by the calculation."""

from hlag_engine.modules.methodology import Component, Formula, MethodologySpec, Symbol
from hlag_engine.modules.methodology import SymbolRef as R

P, S, A, C, N = R.PARAM, R.SERIES, R.ANCHOR, R.SCENARIO, R.NONE

SPEC = MethodologySpec(
    driver="rates",
    title="Interest and refinancing",
    summary=(
        "Interest cash is calculated on both sides of the balance sheet: floating and fixed debt, the EUR bond and "
        "leases on the debt side, and money-market deposits on the cash side.",
        "Floating debt and deposits reset to their benchmark (SOFR for USD, €STR for EUR) on their reset dates and "
        "pay at the end of each period on actual days / 360.",
        "A rate step changes only the periods that fix after it; fixed debt, the bond and lease interest do not move.",
        "Maturing debt is rolled over on its maturity date at SOFR plus a new-issue spread, so repayment and new "
        "drawdown cancel and only the interest on the new debt changes.",
        "The figure is the scenario minus the baseline, where the baseline holds benchmarks flat at T0; it is "
        "measured at T0 exchange rates, and the currency effect on EUR interest is shown in the FX rows.",
    ),
    formulas=(
        Formula("Floating debt", "Interest = N × (max(b(r_n), 0) + spread) × days / 360, fixed at reset r_n and paid "
                                 "at r_n+1", (
            Symbol("b(r_n)", "benchmark on the reset date (T0 value shown)", S, "SOFR"),
            Symbol("b_EUR(r_n)", "EUR benchmark on the reset date (T0 value shown)", S, "ESTR"),
            Symbol("spread_USD", "margin over SOFR on USD floating debt", P, "rates.spread_usd_bp"),
            Symbol("spread_EUR", "margin over €STR on EUR floating debt", P, "rates.spread_eur_bp"),
            Symbol("floating share USD", "USD share of floating debt", P, "rates.floating_split_usd"),
        )),
        Formula("Deposits", "Income = B × max(max(b, 0) − d, 0) × days / 360", (
            Symbol("B", "interest-bearing share of cash", P, "rates.interest_bearing_cash_share"),
            Symbol("USD deposit share", "share of deposits in USD", P, "rates.deposit_split_usd"),
            Symbol("d", "deposit rate below the benchmark", P, "rates.deposit_spread_bp"),
        )),
        Formula("Refinancing", "New debt rate = max(SOFR, 0) + BB OAS(T0) + premium + Δspread × f(t)", (
            Symbol("BB OAS(T0)", "BB high-yield spread at T0", S, "BB_OAS"),
            Symbol("premium", "new-issue premium", P, "rates.new_issue_premium_bp"),
            Symbol("Δspread", "credit spread change in the scenario", C, "rates.credit_spread_change_bp"),
            Symbol("mode", "rollover or unavailable refinancing", P, "rates.refinancing_mode"),
        )),
        Formula("Fixed debt", "Interest = rate / 12 × the month's average notional", (
            Symbol("rate", "fixed rate on USD fixed debt", P, "rates.fixed_rate_pct"),
        )),
    ),
    approximation=Formula(
        "Rough approximation",
        "ΔCash ≈ −(floating debt − interest-bearing cash) × Δrate × years remaining in the window, after the next "
        "reset", (
            Symbol("B", "interest-bearing share of cash", P, "rates.interest_bearing_cash_share"),
        )),
    components=(
        Component("debt_side", "Debt side", "Interest on floating and fixed debt, the EUR bond, leases and the ZIM "
                                            "tranche.", "line type"),
        Component("cash_side", "Cash side", "Interest income on money-market deposits.", "currency"),
        Component("refinancing", "Refinancing", "Interest on rolled-over debt, plus any change in principal, drawdown "
                                                "or acquisition cash.", "line type"),
        Component("hedge", "Swap settlements", "Hypothetical interest rate swap settlements and margin (swap tool "
                                               "only).", "line type"),
    ),
    used_for={
        "rates.floating_split_usd": ("USD floating share", "Splits floating debt between USD and EUR."),
        "rates.spread_usd_bp": ("USD floating spread", "Margin over SOFR on USD floating debt."),
        "rates.spread_eur_bp": ("EUR floating spread", "Margin over €STR on EUR floating debt."),
        "rates.fixed_rate_pct": ("Fixed rate", "Interest on USD fixed-rate debt."),
        "rates.interest_bearing_cash_share": ("Interest-bearing cash share", "Share of cash placed in deposits."),
        "rates.deposit_split_usd": ("USD deposit share", "Splits deposits between USD and EUR."),
        "rates.deposit_spread_bp": ("Deposit spread", "How far below the benchmark deposits earn."),
        "rates.new_issue_premium_bp": ("New-issue premium", "Added to BB OAS for debt refinanced in the horizon."),
        "rates.swap_spread_bp": ("Swap spread", "Prices a hypothetical swap (swap tool only)."),
        "rates.refinancing_mode": ("Refinancing mode", "Rollover (default) or no refinancing for maturing debt."),
        "rates.bond_notional_eur": ("EUR bond notional", "Sizes the EUR bond coupon."),
        "rates.bond_coupon_pct": ("EUR bond coupon", "Fixed coupon on the EUR bond."),
        "rates.bond_maturity_date": ("EUR bond maturity", "Date the bond stops paying its coupon."),
        "rates.financial_debt_total_eur": ("Financial debt", "Total financial debt behind the maturity schedule."),
        "rates.variable_rate_debt_eur": ("Variable-rate debt", "Sizes the floating tranches."),
        "fx.usd_per_eur_close_2025_12_31": ("USD/EUR at 31 Dec 2025", "Converts EUR disclosures to USD."),
        "rates.zim_tranche_on": ("ZIM tranche", "Switches the ZIM acquisition financing on or off."),
        "rates.zim_amount_usd": ("ZIM tranche amount", "Debt drawn for the ZIM acquisition when switched on."),
        "rates.zim_spread_bp": ("ZIM tranche spread", "Margin on the ZIM tranche when switched on."),
    },
    market_series={
        "SOFR": "USD benchmark for floating debt, deposits and refinancing",
        "ESTR": "EUR benchmark for floating debt and deposits",
        "BB_OAS": "Credit spread for refinancing",
    },
    limitations=(
        "Debt tranches, deposit cohorts and maturities are synthetic register items sized on disclosures.",
    ),
)
