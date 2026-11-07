# Placeholders checklist (build step 2)

Every profile value that is not final. A test (`tests/company_profile/test_profile.py`) checks that
every placeholder in `hlag_engine/company_profile/company_profile.yaml` is listed here.

- **to_enter**: a disclosed figure not yet entered. The value is `null`; anything that needs it is
  skipped and listed in `ExposureRegister.missing`, which step 5 will turn into INCOMPLETE.
- **to_verify**: a working default is in use and must be checked against the source.
- **pending_history**: needs loaded market history (step 3).

Report abbreviations: AR = Annual Report 2025; IR = Investor Report H1 2026.

## To enter (12)

| Key | Spec | What it is | Where to find it / how to derive |
| :--- | :--- | :--- | :--- |
| `fx.usd_per_eur_close_2025_12_31` | 3.1, D12 | USD/EUR closing rate, 31 Dec 2025 | AR (page to find). Converts the AR's EUR disclosures: floating debt, fixed debt, lease interest. Blocks USD floating tranches, USD fixed debt and lease interest. |
| `fx.usd_per_eur_close_2026_06_30` | 3.1, D12 | USD/EUR closing rate, 30 Jun 2026 | IR p. 16. The spec cites both 1.1756 and 1.1396 to this page; verify which is the 30 Jun 2026 closing rate. Converts the EUR share of the 30 Jun 2026 maturities. |
| `liquidity.annual_lease_repayments_usd` | 3.7, 13.2 | Annual repayments of lease liabilities | AR cash flow statement (financing activities). Monthly lease payment = this / 12. Also the working default for current lease liabilities. |
| `debt.current_lease_liabilities_usd` | 3.7, D1, 13.2 | Current lease liabilities inside USD 1,852m at 30 Jun 2026 | IR balance sheet notes (page to find). If not disclosed, the generator uses annual lease repayments (ASSUMPTION). 12-month maturities = 1,852m − this. |
| `liquidity.capex_monthly_usd` | 3.7, 13.2 | Monthly capex for the baseline path | Working default is the H1 run-rate: H1 2026 capex from the IR cash flow statement ÷ 6. Use disclosed FY guidance ÷ 12 instead if given. Exclude FX_TRANSACTION amounts (D34). |
| `freight.group_volume_teu.2025Q3` | 6.3, P16 | Group transport volume, Q3 2025 | 9M 2025 volume (Q3 2025 quarterly statement) − H1 2025 volume (H1 2025 half-year report), on the 2026 basis if restated. |
| `freight.group_volume_teu.2025Q4` | 6.3, P16 | Group transport volume, Q4 2025 | FY2025 volume (AR) − 9M 2025 volume (Q3 2025 statement). |
| `freight.group_volume_teu.2026Q1` | 6.3, P16 | Group transport volume, Q1 2026 | Q1 2026 quarterly statement. |
| `freight.group_volume_teu.2026Q2` | 6.3, P16 | Group transport volume, Q2 2026 | H1 2026 volume 6,684,000 TEU (IR pp. 2, 10) − Q1 2026 volume. |
| `fx.dividend_reference_eur` | 9B.3, 13.2 | Latest disclosed total dividend (EUR) and any stated dividend policy | AR (dividend proposal or appropriation of profit). The FY2026 dividend paid on 10 May 2027 equals it unless a policy gives an estimate. |
| `rates.fomc_meeting_dates` | 8.3, 13.4 | FOMC meeting dates, Nov 2026 – Oct 2027 | Official calendar, federalreserve.gov. |
| `rates.ecb_meeting_dates` | 8.3, 13.4 | ECB monetary policy meeting dates, Nov 2026 – Oct 2027 | Official calendar, ecb.europa.eu. |

Quarters not yet published (Q3 2026, Q4 2026) have no entry. The generator uses the latest
same quarter in the profile × (1 + g) per missing year, labelled ASSUMPTION (P16). Add an entry
when the report is published.

## To verify (20)

| Key | Spec | What it is | How to check |
| :--- | :--- | :--- | :--- |
| `anchor_snapshot.FX_USD_PER_EUR` | 3.7, 13.2, D12 | H1 2026 average USD/EUR, 1.1667 | Check against IR p. 16. Don't confuse it with the closing rates 1.1756 and 1.1396, also cited to IR p. 16 earlier in the spec's history. D12 governs which closing rate converts which EUR disclosure; this average is used only in the anchor snapshot. |
| `freight.h1_volume_teu.ASIA_EUROPE` | 6.2, 13.2 | H1 2026 volume, 1,866 TTEU | Trade order and labels against IR p. 10; then relabel DISCLOSED. |
| `freight.h1_volume_teu.ASIA_AMERICA` | 6.2, 13.2 | H1 2026 volume, 2,472 TTEU | As above. |
| `freight.h1_volume_teu.EUROPE_AMERICA` | 6.2, 13.2 | H1 2026 volume, 1,341 TTEU | As above. |
| `freight.h1_volume_teu.AFRICA_INTRA` | 6.2, 13.2 | H1 2026 volume, 1,004 TTEU | As above. |
| `freight.p0.ASIA_EUROPE` | 6.1, 6.2, 13.2 | Base rate P0, USD 1,367/TEU | Trade order against IR p. 10, and that the disclosed rate is all-in. Replace with the newest quarterly rate together with the anchor snapshot (D70). |
| `freight.p0.ASIA_AMERICA` | 6.1, 6.2, 13.2 | P0, USD 1,407/TEU | As above. |
| `freight.p0.EUROPE_AMERICA` | 6.1, 6.2, 13.2 | P0, USD 1,467/TEU | As above. |
| `freight.p0.AFRICA_INTRA` | 6.1, 6.2, 13.2 | P0, USD 1,391/TEU | As above. |
| `freight.collection_lag_days` | 3.1, 6.3 | DSO, 49 days | Recompute: trade receivables USD 2,998m (IR p. 13) ÷ (Q2 2026 group revenue, Liner plus Terminal & Infrastructure, from the IR ÷ 91). Expected 48–50 days. |
| `liquidity.interest_in_ocf` | 2.2, 13.2 | Whether interest paid sits inside operating cash flow (default false) | IR/AR cash flow statement. |
| `liquidity.lease_repayments_include_interest` | 3.7, 8.3, D51 | Whether lease repayments include interest (default false) | AR cash flow statement. True makes the LEASE interest line DELTA_ONLY. |
| `liquidity.capex_includes_fx_transaction` | 3.7, D34 | Whether the capex source includes the INR/BRL obligations (default false) | Depends on what the obligations are for (AR p. 215). |
| `bunker.scrubber_share` | 7.3, 13.2 | Scrubber (HSFO) share, 15% | Search AR/IR for a scrubber fleet figure. |
| `rates.bond_coupon_month_day` | 8.3, 13.2 | Bond coupon date, 15 Oct | AR p. 211 or the bond terms. |
| `rates.bond_step_up_applies` | 8.3, 13.2 | Whether the 0.25 pp step-up from 15 Oct 2025 applies (default no) | AR p. 211, sustainability report or bond terms. |
| `rates.bond_maturity_date` | 8.2, 8.3 | Maturity of the EUR 300m bullet bond, default 15 Oct 2028 (7 years from 2021, ASSUMPTION); kept out of the maturity slices (P49) | AR p. 211 or the bond terms. |
| `rates.zim_spread_bp` | 8.3, 8.16, 13.2 | ZIM tranche spread, SOFR + 225 bp | ZIM financing terms (IR p. 3 and later announcements). |
| `rates.zim_draw_date` | 8.3, 8.16, 13.2 | ZIM tranche draw date, 1 Jan 2027 | As above. Also whether drawn, committed or proposed. |
| `fx.inr_brl_obligations_on` | 9B.3, 13.2, D34 | INR/BRL obligations switch (default on) | AR p. 215: what the obligations are for, whether already settled, payment dates. |

The regulatory register (`hlag_engine/company_profile/registers/regulatory.yaml`) was checked against AR 2025
pp. 23-24 on 2 Nov 2026: the regimes the AR identifies are DISCLOSED with their page; the rest stay
ASSUMPTION. Registers are not versioned, so this applies at once.

## Filled in profile version 2026H1-v2 (active 2–3 Nov 2026)

`hlag_engine/company_profile/company_profile_2026H1-v2.yaml` fills the items above from
`docs/handoff/placeholders_filled.xlsx` and, with P63, splits the ZIM purchase consideration (USD 4.2bn)
from its financing (up to USD 2.5bn). It was activated with `hlag_engine.data.profile_admin.activate_profile_version`
on 2 Nov 2026 (P32; audit entry in the local store). The tables above describe version 2026H1-v1. Still open in v2: `debt.current_lease_liabilities_usd`
(to enter), `bunker.scrubber_share`, `rates.zim_spread_bp`, `rates.zim_draw_date` (now 31 Dec 2026,
ASSUMPTION), `fx.inr_brl_obligations_on`, `liquidity.capex_includes_fx_transaction` (to verify) and the
20 pending-history items.

## Filled in profile version 2026H1-v3 (active 3 Nov 2026, replaced by v4)

`hlag_engine/company_profile/company_profile_2026H1-v3.yaml` is v2 with the 20 pending-history items filled by
`hlag_engine.data.anchor.write_anchor_profile_version` (P31): the 18 H1 2026 averages and the INR and BRL lock rates
of 31 Dec 2025. The WCI and fuel averages come from the Bloomberg Terminal history (P89; WCI 25 of 26 weeks, the week
of 2 Jan 2026 being a holiday gap), the rest from the ECB, NY Fed and FRED APIs. Activated with
`hlag_engine.data.profile_admin.activate_profile_version` on 3 Nov 2026. Before the repository goes public, see P90
(licence for derived averages). Still open as in v2, apart from the pending-history items.

## Profile version 2026H1-v4 (active since 3 Nov 2026)

`hlag_engine/company_profile/company_profile_2026H1-v4.yaml` is v3 with interim freight pass-through values for the
demo, pending calibration (P93): `freight.contract_share` 0.70, `freight.beta` 0.5, `freight.kappa` 0.4,
`freight.gamma` 0.25 (v3: 0.50, 1.0, 0.8, 0.5), all ASSUMPTION. Activated with
`hlag_engine.data.profile_admin.activate_profile_version` on 3 Nov 2026.

## Team decisions

| Key | Decision needed |
| :--- | :--- |
| `rates.zim_tranche_on` | Default on or off for the ZIM acquisition (currently off). The transaction is expected to close by end-2026. When on: USD 2.5bn financing drawn and USD 4.2bn consideration paid on the draw date (31 Dec 2026, ASSUMPTION), so about USD 1.7bn of cash leaves (P63). Results are always shown both ways through run_pair. |

## WCI anchors and P0 must cover the same period (D70, P87)

The WCI anchor values (`anchor_snapshot.WCI_*`) must be averages over exactly the same period as the freight base
rates P0 (`freight.p0.*`, currently H1 2026). P0 is tied to the anchor-period index level, so the anchor sets the
dollar scale of all freight results: a WCI move ΔI is worth (ΔI ÷ anchor) × P0 per TEU. An anchor from a different
period than P0 would mis-scale every freight delta, the anchor adjustment and the freight part of the baseline
change. Replace both together whenever a newer report updates P0.

## Open items

- **Freight pass-through calibration (P93):** v4's contract share, β, κ and γ are interim demo values. Calibrate them
  against disclosed quarterly realised rates per trade and a longer WCI history before relying on freight results.

- **Fuel blend above the price paid (P92):** the H1 2026 hub blend from the v3 anchors is about USD 648.5/t with the
  current hub weights and scrubber share, about 9.5% above the USD 592/t paid (IR pp. 10–11), so fuel cost moves may
  be overstated by about 9%. Possible later fix: a calibration factor (paid ÷ blend) on fuel purchase amounts, a team
  decision. No change now.

## Known basis differences

- **H1 2025 transport volume:** the quarterly volumes in 2026H1-v2 imply H1 2025 = 9M 2025 10,170 − Q3 2025 3,425 = 6,745 TTEU, while the H1 2025 "adjusted" trade rows in spec 6.2 (used by the 6.13 bridge) sum to 6,588 TTEU. The two figures are on different bases (reported vs adjusted); no value is changed. Check the basis before comparing H1 2025 with H1 2026 at trade level.

## Pending history (20; all filled in 2026H1-v3)

All of these are H1 2026 (1 Jan – 30 Jun 2026) averages from history loaded in step 3, labelled
DERIVED, except the two lock rates, which are single values (MARKET).

They are filled by the administrator action `hlag_engine.data.anchor.write_anchor_profile_version`, which
writes a new profile version; `hlag_engine.data.profile_admin.activate_profile_version` then makes it active
(decisions P31, P32). Preview with `python -m hlag_engine.data.cli anchor-preview --db <file>`.

| Key | Spec | What it is |
| :--- | :--- | :--- |
| `anchor_snapshot.WCI_SHA_RTM` | 3.7, 6.4 | Average weekly WCI Shanghai–Rotterdam (USD/FEU) |
| `anchor_snapshot.WCI_SHA_LAX` | 3.7, 6.4 | Average weekly WCI Shanghai–Los Angeles |
| `anchor_snapshot.WCI_RTM_NYC` | 3.7, 6.4 | Average weekly WCI Rotterdam–New York |
| `anchor_snapshot.WCI_COMPOSITE` | 3.7, 6.4 | Average weekly WCI composite |
| `anchor_snapshot.VLSFO_SIN` | 3.7, 7.4 | Average weekly VLSFO Singapore (USD/t); compare the blend with USD 592/t paid (IR pp. 10–11) |
| `anchor_snapshot.VLSFO_RTM` | 3.7, 7.4 | Average weekly VLSFO Rotterdam |
| `anchor_snapshot.HSFO_SIN` | 3.7, 7.4 | Average weekly HSFO 380 cst Singapore |
| `anchor_snapshot.HSFO_RTM` | 3.7, 7.4 | Average weekly HSFO 380 cst Rotterdam |
| `anchor_snapshot.SOFR` | 3.7, 8.4 | Average daily SOFR (NY Fed) |
| `anchor_snapshot.ESTR` | 3.7, 8.4 | Average daily ESTR (ECB) |
| `anchor_snapshot.BB_OAS` | 3.7, 8.4 | Average daily BB OAS (FRED, series ID to confirm) |
| `anchor_snapshot.UST_2Y` | 3.7, 8.4 | Average daily FRED DGS2 |
| `anchor_snapshot.EA_2Y` | 3.7, 8.4 | Average daily ECB 2-year spot rate (series ID to confirm) |
| `anchor_snapshot.FX_USD_PER_CAD` | 3.7, 9B.5 | Average ECB daily rate, as USD per CAD |
| `anchor_snapshot.FX_USD_PER_GBP` | 3.7, 9B.5 | Average ECB daily rate, as USD per GBP |
| `anchor_snapshot.FX_USD_PER_CNY` | 3.7, 9B.5 | Average ECB daily rate, as USD per CNY |
| `anchor_snapshot.FX_USD_PER_INR` | 3.7, 9B.5 | Average ECB daily rate, as USD per INR |
| `anchor_snapshot.FX_USD_PER_BRL` | 3.7, 9B.5 | Average ECB daily rate, as USD per BRL |
| `fx.lock_rate_usd_per_unit.INR` | 9B.4 | USD per INR on 31 Dec 2025 (ECB); the INR policy hedge is locked at it |
| `fx.lock_rate_usd_per_unit.BRL` | 9B.4 | USD per BRL on 31 Dec 2025 (ECB); the BRL policy hedge is locked at it |

## For the team to confirm (not placeholders)

**Allowed ranges the spec does not give.** Section 3.1 requires an allowed_range on every
ASSUMPTION; these are proposed and marked "Range proposed, not in spec" in the profile:

| Key | Default | Proposed range |
| :--- | :--- | :--- |
| `liquidity.minimum_operational_reserve_usd` | 750,000,000 | 0 – 2,000,000,000 |
| `freight.index_linked_share` | 0.10 | 0 – 0.30 |
| `freight.beta` | 1.0 | 0 – 1.5 |
| `freight.kappa` | 0.8 | 0 – 1 |
| `freight.clause_lag_days` | 30 | 0 – 90 |
| `freight.leakage_volume_loss` | 0 | 0 – 0.20 |
| `bunker.asia_europe_consumption_share` | 0.35 | 0 – 1 |
| `bunker.beta_fallback` | 1.0 | 0 – 2 |
| `bunker.margin_factor` | 1.0 | 0 – 2 |
| `rates.floating_tranches_per_currency` | 3 | 1 – 12 |
| `rates.deposit_cohorts_per_currency` | 3 | 1 – 12 |
| `rates.spread_usd_bp`, `rates.spread_eur_bp` | 200, 225 | 0 – 600 |
| `rates.fixed_rate_pct` | 4.8 | 0 – 15 |
| `rates.deposit_spread_bp` | 10 | 0 – 100 |
| `rates.swap_spread_bp` | 0 | −50 – 100 |
| `rates.zim_spread_bp` | 225 | 0 – 600 |
| `fx.eur_operating_cost_per_quarter_eur` | 350,000,000 | 0 – 1,000,000,000 |
| `adjustments.max_review_days` | 180 | 1 – 365 |

Non-numeric ASSUMPTION values (switches, dates, schemes, currencies) carry `allowed_values`
instead of `allowed_range`, so they can't be overridden by a slider (P21).

**Value modes the spec does not state.** Freight keys are step_or_direct (6.10) and
`bunker.scrubber_share` is step_or_direct (3.3 example). The other bunker keys, the rates step
keys and the fx step keys also use step_or_direct. Availability keys are step_only.

**Dates the spec leaves open.**
- Floating resets and deposit rolls fall on the first day of the month.
- Monthly fixed interest, lease interest and INR/BRL payments fall on the last day of the month.
- Weekly CAD and EUR operating costs fall on Fridays (9B.3 gives Fridays for CAD only).

**Currency conversions.**
- The EUR share of maturities is converted at the 30 Jun 2026 closing rate (the date of the USD 1,852m disclosure, D12).
- EUR deposit balances stay as USD equivalents, which the module converts at T0, like the memo positions.
