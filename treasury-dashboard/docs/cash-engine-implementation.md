# Combined cash engine implementation

The new cash engine is independent of the retained legacy sensitivity dashboard.
It does not execute payments, trades or hedges and contains no model or network calls.
Its inputs are a versioned profile, approved adjustments, explicit scenario parameters
and the existing SQLite market store. Missing observations are never replaced with
demo data unless demonstration mode is explicitly selected.

## Implemented behavior

- Two gross native-currency passes; base and scenario amounts paired with a full outer join.
- Daily cash dates for 91 days, then month-end buckets through the month containing T0 + 12 months.
- Freight spot, index-linked and staggered contract cohorts, reset pass-through, directional leakage,
  volume losses, non-USD invoices, collection lag and transit-dependent collection dates.
- Shared freight TEU drives bunker MFR. Four port/grade prices drive monthly purchases;
  MFR uses a lagged reference price and the anchor blend, then the freight collection dates.
- Quarterly floating debt/deposit resets, current-debt maturity schedule, rollover versus
  unavailable refinancing, fixed debt, leases, refinancing interest and optional ZIM state.
- Currency cash costs, dividend when supplied, INR/BRL transactions and policy forward settlements.
  CNY/GBP translation positions are memo items only.
- Base-amount versus delta-only baseline treatment, fixed-order anchor swaps, liquidity paths,
  source flags, status precedence, exact additive attribution and gross desk exceptions.
- Bunker swap/fixed-price coverage as a state applied in both passes; swap collateral is posted
  one-sided and released as the remaining hedged tonnage expires. A separate margin test is returned.
- State-pair comparison. The parent API persists full results and all input snapshots immutably.

All percent scenario inputs use fractions (0.15 = 15%). Rate steps use basis points.
The convenience `T0` date in sensitivity preset templates is resolved into an explicit ISO date
before calculation and persistence. All output summary amounts are USD millions; native line
amounts use millions of the line currency. Stored paired cash amounts are rounded to USD cents.

## Required inputs and current limitations

Official Annual 2025 and H1 2026 reports now supply lease/concession principal, cash capex,
financial debt excluding leases, current lease/concession liabilities and precise closing FX.
The latest paid dividend is the reference for an explicitly assumed future dividend.
Trade mapping and H1 rates/volumes are verified. Source URLs, pages and formulas are preserved
in the profile and [source audit](financial-profile-source-audit.md).

Quarterly group seasonality uses Q1/Q2 2026 and Q3/Q4 2025 figures times H1 2026 trade shares.
Q3/Q4 are labelled PROXY because the 2025 measurement basis differs and newer comparable
figures are not yet available. Historical financing and interest movements from June to T0
remain missing, so opening cash is provisional. Missing material series or incomplete anchor
coverage are listed in the result. Partial computed figures remain visible, but LOW/WATCH
become INCOMPLETE; HIGH/CRITICAL retain their status with incomplete-data flags.
These limitations prevent claiming the real-profile acceptance suite passed.

Rates swaps, official future meeting-calendar presets, market-alert volatility rules,
historically constructed reversal/replay presets, counterparty-specific availability shocks
and independently recomputed run replay are not implemented in this component. The API's replay
returns the original immutable saved result, rather than calling that a fresh recomputation.

Demonstration mode is wholly illustrative, including market values and missing financial inputs.
It is clearly flagged and does not mix illustrative values into a connected run. It is suitable
for checking orchestration and identities, not validating company exposure estimates.

## Verification

`gateway/tests/test_risk_engine.py` covers zero shocks and anchor identity, full outer joins,
additive attribution, state changes, reset lag, missing-data behavior, trust-boundary validation,
dated adjustments, unavailable refinancing and swap collateral release. These are implementation
contract checks, not a substitute for the specification's real-profile and hand-computed joint fixtures.
