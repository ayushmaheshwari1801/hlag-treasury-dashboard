# Financial profile source audit

Verified 2 November 2026 against official company PDFs. The profile retains the H1 2026
cash/OCF/P0 anchor together. Q3 2026 was not listed on the official
[publication page](https://www.hapag-lloyd.com/en/company/ir/publications/financial-report.html).

| Profile input | Entered value | Primary evidence and treatment |
|---|---:|---|
| 2025 closing USD/EUR | 1.17565 | [Annual 2025](https://www.hapag-lloyd.com/content/dam/website/downloads/ir/HLAG_FY_2025_EN.pdf), p172; more precise than rounded investor reports |
| Financial debt excluding leases | USD3233.155065m | Annual p210 EUR2750.1m times its reporting-date FX |
| Monthly lease/concession principal | USD108.786813m | Annual p164: (EUR1108.1m + EUR2.3m) times 1.17565 / 12; excludes interest |
| Lease/concession interest | EUR207.1m/year | Annual p188; separately emitted |
| Current lease/concession liabilities | USD1273.38904m | [H1 Financial Report](https://www.hapag-lloyd.com/content/dam/website/downloads/ir/HLAG_H1_2026_EN.pdf), p20: EUR1117.4m times 1.1396 |
| Monthly cash capex | USD42.5m | [H1 Investor Report](https://www.hapag-lloyd.com/content/dam/website/downloads/ir/HLAG_Investor_Report_H1_2026.pdf), p14: USD255m / 6 |
| Next dividend reference | EUR527.3m | H1 Financial Report p15 actual prior dividend; prospective reuse and payment date remain ASSUMPTION |
| Collection lag | 47 days | H1 Investor Report pp2,13: round(2998 / (5840/91)); replaces illustrative 49 |
| Trade mapping/P0 | Verified | H1 Investor Report p10; original four volume/rate rows confirmed |
| Q1 seasonality | 3203000 TEU | [Q1 Investor Report](https://www.hapag-lloyd.com/content/dam/website/downloads/ir/HLAG_Investor_Report_Q1_2026.pdf), p2; actual Q1 2026 |
| Q2 seasonality | 3481000 TEU | H1 Investor Report p2; actual Q2 2026 |
| Q3 group seasonality PROXY | 3425000 TEU | [9M 2025 Investor Report](https://www.hapag-lloyd.com/content/dam/website/downloads/ir/HLAG_Investor_Report_9M_2025.pdf), p2: 3425 TTEU Q3 2025 |
| Q4 group seasonality PROXY | 3316000 TEU | [FY 2025 Investor Report](https://www.hapag-lloyd.com/content/dam/website/downloads/ir/HLAG_Investor_Report_FY_2025.pdf), pp2,10: FY13486 minus 9M10170 TTEU; agrees with Q4 table |

H1 Financial Report p21 places interest paid in financing and interest received in investing,
so `interest_in_ocf=false` is verified. Annual p164 separately presents principal and interest,
so `lease_includes_interest=false` is verified. Principal includes service concessions consistently
with the current-debt-plus-leases total; the Annual cash-flow statement sum is used, rather than
the slightly differently rounded financing reconciliation.

H1 Investor Report p16 resolves the original FX ambiguity: closing June 2026 is 1.1396;
H1 average is 1.1667. These reference values are recorded in profile metadata. For the verified
January–June 2026 anchor, the real engine uses this disclosed USD/EUR average; other currencies
use loaded market observations. A different anchor period does not reuse the H1 disclosure.

Q3/Q4 seasonality uses prior-year group totals times H1 2026 trade shares, the fallback permitted
by specification 6.3. Older 2025 reports use the finished-voyage basis; the profile records that
measurement difference and does not claim these are restated 2026-basis trade volumes.
Reusing 2025 Q3/Q4 group figures in 2027 pending newer disclosures is an explicit model approximation.
Net financing/interest
movements from 30 June to the current T0 also remain missing: H1 flows cannot supply future
cash movements. Market histories, contractual spreads, future dividend and other model assumptions
retain their own labels. This verification does not establish the live company cash position.

Downloaded PDFs and rendered pages were research intermediates used to verify table columns.
Their temporary local copies were removed during source-package cleanup; the official source links above remain available.
They are not application inputs or required distribution artifacts.
