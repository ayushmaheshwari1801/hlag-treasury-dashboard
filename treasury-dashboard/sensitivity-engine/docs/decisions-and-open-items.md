# Decisions, limits and open items

## Interpretation choices (where the specification was silent or ambiguous)

| Topic | What the engine does | Why |
| --- | --- | --- |
| Which scenario drives the badge | `status` is for the selected scenario (default: the latest observed move). `worst_case_status` is the worst row in the table | Freight's selected +8% is favourable, so its badge alone would read `LOW` while the -8% row is `CRITICAL` |
| Freight downside rows | -4%, -8%, -12% added to the table. The specification's three upside rows are unchanged | The specification's table is upside-only; falling rates are the adverse case |
| Interest Mode A status | `NOT ASSESSABLE` unless an approved limit is supplied | An annualised earnings proxy is not cash, so comparing it with liquidity would break the specification's own rule |
| Interest Mode B | No default principal; `UNAVAILABLE` until a debt schedule is given | None was supplied, and inventing one would create an exposure |
| Limit at or above 100% | `CRITICAL` (`MAT-C3`), noted as computed rather than confirmed | The specification defines `HIGH` as below 100% and `CRITICAL` as a confirmed breach, which leaves 100% and above undefined |
| Stale liquidity | Older than 31 days is treated as unavailable | A stale denominator should not produce a `LOW` |
| Compound horizon | Includes only components whose horizon equals the compound horizon (default 182 days). The CAD 13-week case is excluded by default | "Aggregate only compatible cash-flow horizons" |
| Surcharge overlap | If bunker rises and freight gains, the freight gain is reduced by `min(freight gain, dP x Q x r x t)` | Bunker is already net of recovery `r`; the same recovery should not reappear as freight revenue. Switch off with `bunker_surcharge_in_freight_rate: false` |
| Table multipliers | Interest uses 0, 0.5x, 1x, 2x; bunker and freight use 0, 0.5x, 1x, 1.5x | These reproduce the specification's illustrative tables. They are configuration |
| FX selected shock | +10% unless changed | The specification gives a grid but no selected point |
| Interest benchmark | ECB deposit facility rate; "previous comparable" is the last different level. EUR STR is selectable and compares consecutive observations | A policy rate changes in steps, so the previous day is not informative |
| EUR to USD | Uses the dated EUR/USD series. A stale rate is used but flagged | The specification requires a sourced, dated rate |
| Reliability | `LOW` while any input is illustrative, historical or the data is not fresh | The specification sets `LOW` for all four modules |
| Rounding | Half away from zero, applied only for display. Totals use unrounded values | Reproduces 1.2775 as 1.278 |

## Not verified

- **Live adapters were not run against the real services.** The build environment blocked those sites. The ECB series keys come from a third-party description, and the Frankfurter v2 response shape comes from its documentation page. Parsing and fallback are covered by tests with canned responses. Check both with one real call before using `SENSITIVITY_MARKET_MODE=live`.
- Frankfurter v2 was documented for the latest rate only, so live FX history builds up in the local cache over time. It is not back-filled.
- Bunker and freight have no live feed: the specification says API entitlement and licence are unconfirmed. They are served from fixtures.
- Company figures (FX hedge 57.5m, FY2025 interest sensitivities, H1 2026 bunker and freight references) are used as supplied. Their page references are not checked.
- The news bridge was tested against the news engine's offline demo export, not a live export.
- There is no LLM provider. `explain.py` has the interface and a guard that rejects any text containing a number not in the result or a status the rules engine did not assign.

## Needed from you

1. **Comparable liquidity** (USD, with date). Without it every status badge rests on the USD 5,000m placeholder.
2. **Approved limits**, if any, especially an earnings-at-risk limit so Mode A can have a status.
3. **Floating-rate debt schedule** (principal, reset dates, swaps) for Mode B.
4. **FY2025 +/-10% FX earnings sensitivities** for EUR, CNY and GBP. They were not in the attachments, so the block shows `UNAVAILABLE`.
5. **Bunker and freight feeds** (Ship & Bunker or BunkerEx; SSE SCFI or Freightos FBX) and whether licences are in place.

## Connecting to the rest of the dashboard

- **News engine:** point `SENSITIVITY_NEWS_DIR` at its output folder. The newest `articles_*.json` is read. A confirmed article with an unambiguous numeric move can appear as a candidate assumption (`applied: false`). A person must submit it as an override for it to be used. Pending articles are marked `unverified` and never produce candidates; rejected ones are hidden.
- **Frontend:** `/defaults` gives control definitions and bounds, `/scenario` recalculates on every change, `/reset` restores defaults, and `/reviews` writes the audit entry. The frontend never has to compute a number.
