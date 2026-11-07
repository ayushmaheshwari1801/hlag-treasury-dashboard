# Combined Treasury integration

## Architecture and design authority

The supplied combined prototype specification and architecture image define the Treasury flow. The user selected `news-engine-v8` to replace the older workflow and retained the original webpage as the sole interface. News and predicted parameters are adapted into the existing News Tracker, what-if card and result cards; there is no separate cash-engine page. The obsolete `treasury-dashboard/news-engine` runtime, its unused gateway adapter and its dedicated CI workflows have been removed. The active v8 engine, saved data and independent instruction-only Treasury intelligence skill remain in place.

Market feeds and v8 news are independent inputs. The forecast agent reads original Articles and Events and predicts scenario parameters. Freight, bunker, rates and FX emit deterministic native-currency cash lines. The shared conversion layer produces USD driver attribution and baseline/scenario liquidity paths. Recommendation generation reads the saved calculation and source Events. The frontend presents the same results.

Treasurer acceptance fixes (7 November 2026): weekly changes compare the latest
quote with the observation one week before that quote, consistently with Data
connections; cards display the quote's week-ending date. Edited forecast takeaways
use the effective values while retaining the original estimate beside each input.
Inactive forecasts are hidden when another scenario is selected. Hedge comparison
columns explicitly describe changes in minimum headroom, not headroom balances.
Recommendation diagnosis nets policy-forward settlements against their protected
exposure before ranking adverse cash lines. Full coverage must not be described as
an uncovered currency loss.

Forecast requests for the same news snapshot, selected events, date, horizon and
mode reuse a saved result and share concurrent work. Failed work is not cached or
automatically retried. Superseded page calculations are cancelled and obsolete
event processing stops before starting another calculation. Audit identifiers and
technical assumption references remain internal; business assumptions, uncertainty
and missing-data notices remain visible. Long historical assessments are available
as expandable supporting detail. Recommendation date fields accept explicit
YYYY-MM-DD input and reject incomplete or impossible dates before submission.

Forecasting is a normal what-if workflow: generated parameters can be run and edited immediately. Human approval applies only when a proposal becomes a persistent baseline adjustment. News text is untrusted evidence and is never executable instructions. R5 is event priority, not predictive confidence.

Provider responses are validated before indexing completion/message fields. Text answers
require string content; recommendation tool calls require string IDs, names and JSON
argument strings. Invalid envelopes become explicit validation failures, using the existing
offline/rule fallback for assistants and explicit forecast failure responses. Citation fields,
when present, must be arrays of nonempty strings. Invalid answer schemas retain the existing
single repair attempt; provider/auth/transport failures do not acquire automatic retries.

The issue-identity-v2 news policy compares the specific instrument and operative scope using
both clusters' original Articles. It keeps distinct instruments separate and joins announcement
and activation of the same instrument. A classification validation failure has one bounded
repair; exhausted work remains pending. The default database is now
`data/news-mvp-v8-issue-identity-v2.sqlite`. Older databases and saved exports are preserved;
restart the launcher to load the new code. Explicitly configured incompatible databases still
fail the policy guard rather than being migrated or silently rewritten.

The original what-if selector presents two highest-scored Events per category (regulatory, macroeconomics, industry), for six independent news scenarios. Event score ties use latest publication time and then Event ID. Only a selected Event is forecast; other Events' parameters are not merged into it. User edits remain separate overrides. The initial six-Event pool is selected from genuine supplied v8 saved outputs, with original Articles and scores retained; see `../inputs/news-scenario-selection.md`. It is a saved-data selection, not a new discovery/model execution. A future collection that lacks sufficient scored Events exposes its actual category gaps.

## API surface

All routes below share optional gateway bearer authentication. `/healthz` is public. The launcher mounts the separately authenticated v8 service at `/v8`.

| Endpoint | Purpose |
|---|---|
| GET /api/v1/market/snapshot | All 20 series with availability, source and stale flags |
| GET /api/v1/market/history/{series_id} | Actual historical observations, no interpolation |
| POST /api/v1/market/import-csv | Validated weekly CSV text in `{csv: ...}` |
| GET /api/v1/intelligence/events | v8 Events and full original source Articles |
| POST /api/v1/intelligence/import-result | Validate and save a contract-8 RunResult |
| POST /api/v1/intelligence/collect | Explicit v8 collection/analysis request |
| POST /api/v1/intelligence/forecast | Predict engine inputs from selected Events |
| GET /api/v1/intelligence/forecasts | Saved predictions with source IDs and assumptions |
| GET/POST /api/v1/intelligence/signals | Strict factual/quantitative signal boundary |
| GET/POST /api/v1/intelligence/adjustments | Read/propose allowlisted persistent changes |
| POST /api/v1/intelligence/adjustments/{id}/decision | Named human approve/reject/revoke action |
| GET /api/v1/risk/dashboard | Calculate a zero-shock base run |
| POST /api/v1/risk/run | `{as_of, scenario, demo}`; calculate and save a run |
| POST /api/v1/risk/from-forecast/{id} | Apply an immutable news forecast directly as a what-if |
| POST /api/v1/risk/pair | Compare two state settings with the same market-shock scenario |
| GET /api/v1/risk/presets | Supported executable scenario library |
| GET /api/v1/risk/runs | Recent saved run summaries |
| GET /api/v1/risk/runs/{id} | Original saved calculation and input snapshot |
| POST /api/v1/risk/runs/{id}/replay | Exact immutable result retrieval, not current-data recomputation |
| POST /api/v1/risk/from-signal/{event_id} | Build a mapped signal scenario; deduplicate by version |
| GET /api/v1/risk/build-queue | Deferred builds after the daily cap |
| GET/PUT /api/v1/risk/profile | Read or version administrator-provided profile values |
| POST /api/v1/risk/recommendations | Saved-run brief/question via configured model or labelled playbook |

Forecast request fields are `event_ids` (optional list), `as_of`, `horizon_days`, and `mode` (`auto`, `model`, `rule_based`). All predicted changes carry ASSUMPTION, confidence, rationale, effective date, duration, Event IDs and evidence links. A 15 pct prediction compiles to engine fraction 0.15. Generated forecast magnitude need not be literally quoted in an Article: it is explicitly an inference, subject to allowlisted parameter, unit, range, date and source validation. Model failure never becomes a fabricated successful model forecast.

Scenario blocks follow the supplied specification: freight trade maps, bunker price/consumption shocks, rates dated USD/EUR bp steps and credit spread changes, and FX currency move fractions. Additional parameters use bounded profile overrides. Unknown fields, nonfinite numbers and incompatible units are rejected.

## Profiles and run provenance

`gateway/src/treasury_gateway/risk_profile.json` records specification-supplied facts, derived values, assumptions and missing disclosures. No missing dividend, lease, capex or historical conversion is invented in real mode. PUT profile takes `{actor, values:{param_key:{value,label,source,formula}}}` and appends a version rather than overwriting previous run inputs. Only known profile keys are accepted. Supply a source or formula for disclosed/derived changes. Local actor names record responsibility; they are not user authentication.

Run records preserve profile, resolved scenario, approved adjustments, market/anchor snapshots and result lines. Exact identical input snapshots reuse a deterministic run ID. Changed data creates a different result ID, including revisions to historical hedge inputs. The replay endpoint retrieves the original immutable calculation; it does not promise full independent recomputation of every historical input. Forecasts remain separate from observed prices and approved profile adjustments. Passing `forecast_id` with a run stores the original forecast. Edited predictions retain that reference and record a `forecast_overrides` diff and `forecast_modified` flag; a different as-of date requires a new forecast.

The native line ledger uses currency millions and the result path USD millions. A zero shock produces identical paths and zero paired deltas. The first 91 days are daily, followed by month-end points. WCI composite is a proxy for Africa/intraregional trade. Missing/stale material data and missing source fields remain visible even when partial calculations can run. Demonstration substitutions only occur with demo=true.

## Operational limits

The implementation is a working integration, not an assertion that every acceptance item in the long specification is complete. Company disclosures still need independent verification; synthetic exposure registers do not represent a live company position. The historical opening-cash roll-forward omits unavailable financing flows and is flagged provisional. Verified future meeting calendars, some history-derived presets, full counterparty availability modelling and scheduled weekly baseline-change attribution require further data/work. See the engine limitations returned in every run.

Signal builds are explicitly requested, limited to one per Event version and 30 per UTC day; overflow is queued. A scheduler to drain that queue and scheduled daily/weekly runs is not installed. News collection and model forecasting require provider credentials; offline tests do not establish model prediction accuracy. WCI and fuel historical imports still require actual data and appropriate usage rights. The default UI exposes no trading, borrowing or payment execution.

## News collection progress
The dashboard polls the authenticated `/api/v1/intelligence/collection-status` endpoint every two seconds, including after page reload. Status reads the local v8 database and atomic `.progress.json` execution sidecar; it never starts collection or calls a provider. The existing writer lock distinguishes running work from interrupted records. New runs persist stage, UTC start/update time and stage completed/total counts. Older executions without sidecars show processing status and explicitly labelled database totals. Stage counts are not an overall workflow percentage. Refresh is disabled while a task is running; final and interrupted states remain visible after reload.

## Homepage Event recommendations

### Hedge recommendation agent

With `CASH_ENGINE=spec`, the homepage also provides a **Hedge recommendation agent** panel. Choose a news publication window, optionally include the current what-if shocks and overrides, and select **Run agent**. Results include tested candidates, headroom effects, reversal costs, timing, margin checks and evidence; saved sessions can be reopened from the panel.

`POST /api/v1/recommendations/agent` creates a session; `GET` on the same path lists sessions, and `GET /api/v1/recommendations/agent/{session_id}` reopens one. The agent tests hypothetical bunker and rates hedges and FX hedge-ratio changes, using at most 20 deterministic engine runs with `write=False`. The model chooses tests and explains engine results. Grades use illustrative thresholds. Without a configured model, or when model output fails validation, a labelled rule planner provides the result and records the fallback reason. News forecasts default to fixed-rule assumptions (`forecast_mode=rule_based`), independently of the recommendation model.

A request using `forecast_id` reads the forecast's original saved news snapshot, never the current Event pool. Missing snapshots return 409; an analysis date before the saved forecast returns 422. Sessions retain the source snapshot ID. Model recommendations must cite tested candidates and supply structured text; malformed output is rejected for repair or labelled fallback. Nothing is executed or approved.

Recommended Actions retains v8 Event `what_to_do` advice verbatim. With a selected news forecast, the gateway resolves the immutable source news run and uses only the selected Event recommendations, attaching the displayed horizon net cash delta and scenario minimum headroom/date. Manually changed forecasts are labelled current edited scenarios. These are full-scenario results, not a separate attribution to each recommendation. Without a selected Event, advice comes from the current top-two-per-category pool, ordered by Event R5, with an explicit not-quantified message; unrelated observed-move cash amounts are not attached. Identical advice is deduplicated and all source Event/Article IDs are retained. Missing-input warnings remain first. The homepage shows three items, See all shows the complete set and links to Event evidence. No new model calls or baseline changes are needed.

## ID validation and parallel W3 update

The v8 classifier now returns exactly one assignment per supplied Article, with runtime enums for issue handles and candidate Event IDs. Python constructs article membership and SQLite preserves immutable ownership. Consolidation and historical matching use bounded parallel model decisions with caller-only merges/writes; default W3 concurrency is 4 and W4 remains 2 batches of 3 Events. Completed and repaired ID failures retain concrete ID lists in run errors/metrics. News Tracker can expand terminal collection errors. Existing page design and English text are retained.

The launcher and status API share `NEWS_V8_DATABASE`, defaulting to a separate `data/news-mvp-v8-id-constrained.sqlite`. This satisfies the classification-policy guard without altering `data/news-mvp-v8.sqlite` or old exports. Startup does not initiate collection; previously saved news/forecasts remain available. Real provider semantic accuracy and wall-clock speedup are not established by offline concurrency tests.

## Default collection batch size

Refresh news sends `max_articles: 10`; both gateway Collect and v8 RunRequest default to ten when the field is omitted. A batch counts new and resumed unique Articles together, and selected proposals are atomic. Deferred originals remain saved; global consolidation cannot bring unselected proposals back into the run. Historical Event source context stays complete and is counted separately. Defaults do not change the seven-query discovery budget, so discovery may persist more than ten source candidates. Saved run metrics disclose selected/deferred counts and units requiring a larger budget. No paid re-run is launched by this configuration change.

## Scenario monitoring and presentation

The existing How close to trouble? card now reports the selected scenario's risk status, minimum projected liquid reserve, headroom above the operating floor, minimum date, headroom consumed/gained versus baseline and first projected reserve breach. It reads the same displayed horizon's saved cash paths. Daily/monthly path points are not misrepresented as a count of days below reserve. Incomplete inputs/path coverage cannot imply a safe result or no breach; known high/critical status remains visible. This replaces the empty cash-engine break-even placeholder without inventing a linear break-even shock.

Event, forecast, cash-run and recommendation identifiers remain stored for traceability but are hidden from normal forecast/details/assistant citation presentation. Human-readable Event titles, effective dates, duration and initial forecast comparison remain visible. The expressly requested technical collection-error details still retain IDs to diagnose failures. All UI/documentation remains English.

## Nonfatal unused-draft warnings

Valid classification assignments can retain their Articles even if extra issue definitions are unreferenced. Only those unused definitions are discarded. Their IDs are recorded as a classification warning in metrics.warnings and persisted classification metadata. Fresh and cached warning-only runs remain completed with errors empty. Article ownership/coverage and invalid references remain hard constraints. Status and imported-export fallback expose warnings separately from errors to the original News Tracker card. The matching/classification policy is isolated in the fresh default `data/news-mvp-v8-issue-identity-v2.sqlite`; prior saved results are not rewritten.

## Cumulative active Event pool

The homepage News Tracker, See all, What-if options, forecasting input and unselected-Event recommendations use the cumulative Event pool from the active `NEWS_V8_DATABASE`, not only the latest run export. Each Event uses its latest stored version; a null score is excluded from the per-category top-two selection rather than reviving an older score. Ranking remains R5 total, latest source publication, then stable Event ID; missing categories are never cross-filled. No new expiry cutoff or collection/model call is introduced.

Read the active contract-8 database in one read-only SQLite transaction. Resolve full original Article evidence, validate the public contract and save immutable pool snapshots under `NEWS_V8_EXPORT_DIR/pools`. Snapshot identity depends on the active database namespace and current Event/Article content, so cached reruns with unchanged knowledge keep the same forecast cache identity. Forecasts retain the pool snapshot ID and saved recommendations resolve that exact snapshot even if an Event is subsequently updated. Old namespace exports are not mixed into the active monitoring pool; explicit old evidence links still resolve saved originals.

Top-level collected run exports and `latest()` remain unchanged for actual collection status/errors. Pool snapshots are execution/view metadata, not new model runs. With no active database or no stored Events, the previously saved export remains the display fallback. All prior databases, sources and saved results remain untouched.

## Cash-flow impact colours

Market Metrics, What-if input labels and Estimated Cash-Flow Impact use the corresponding driver's calculated cash-flow impact from the current scenario and horizon: positive is green, negative is red and exactly zero is grey. Colour does not follow the sign of a market price move; the same rise or fall can therefore have different colours for different exposures. Market text and arrows still indicate the observed price direction, rounded to the displayed precision without signed zero. Small nonzero cash impacts retain their actual sign even if the quote change rounds to zero. Missing or non-finite cash impacts have no assigned impact tone and are never treated as zero. This display rule does not change calculation inputs or results.

## Working-capital timing (roadmap phase 3)

Migrated from the unified source package. The "Working Capital" card sits in step 2 directly below the market metrics and cash-flow impact card. It has the collections-timing lever; its collapsed "Working capital timing" part shows the engine's collection lag (DSO), fuel supplier payment lag (DPO) and delay-affected revenue share from `GET /api/v1/engine/assumptions`, and the gross freight and fuel cash lines by month (cut at the selected horizon) from `by_driver_month` in `GET /api/v1/engine/runs/{run_id}/cashflows`. Their baseline amounts stay inside the OCF run-rate (spec 2.2); only the scenario change moves the path.

It has a collections-timing lever: "collections slower by N days" (0-60) is sent as the what-if shock `collection_delay`, which the gateway (`dashboard_adapter.WC_SHOCKS`) maps to the spec's scenario field `freight.transit_delay_days` on every trade (6.5, D40). It is a scenario delta applied on top of the selected scenario, so receipts move to later periods and receipts pushed past the horizon end leave it. Faster collections are not modelled, because the spec field allows delays only. The fuel supplier lag (DPO) is shown but not offered as a lever: the spec has no scenario field for it, and as a per-run override it would move only the fuel cost difference from H1 2026 prices, because the normal fuel bill is paid inside the OCF run-rate.

## Direct cash flows from News forecasts

The forecast agent (`gateway/.../forecast.py`) may propose, besides market-driver changes, `direct.cash_flow`: a dated, signed USD-million amount for an effect that no market driver carries, such as a fine, settlement, compensation or one-off payment that the company itself pays or receives. Each needs `effect_id`, `overlap_key` and `cash_label`; it must be dated after as-of and within the horizon (−2,000..+2,000 per entry, ≤ 5,000 absolute in total). The prompt allows it only when the sources make the company's applicability explicit and never to restate a driver effect. Fixed rules never produce it. `compile_scenario` writes it to `scenario.direct.cash_flows`; the amount can be edited in the What-if panel like other forecast inputs.

`hlag_engine` carries it as `EVENT_CASH_FLOW` lines of owner `direct` in the scenario pass only (authorised spec deviation P94, `treasury-calculation-engine/docs/decisions_pending.md`), so it reaches the scenario liquidity path, headroom, status and the `direct` attribution bar. The dashboard shows it as `cash_impact.direct` (a separate "Direct cash · model estimate" line in the cash-flow impact card) and includes it in the net and adverse totals; the seven market-driver rows exclude it. The mock engine (CASH_ENGINE other than spec) refuses scenarios with direct cash flows. These amounts are model estimates, not reported figures: news-engine numeric grounding is disabled, so a stated amount is not checked against the source text.

The forecast model receives the run's T0 FX rates (`t0_fx_usd_per_unit`, USD per one foreign unit from the shared market store) and must convert a non-USD direct amount only at that rate, stating the original amount and rate in its rationale; without a rate for that currency it omits the direct cash flow. (Found in a live check: without the rate, the model converted EUR 48m at a guessed 1.08.)

## Page layout (7 Nov 2026)

The page has two steps: 1. Liquidity forecast with the What-if panel, and 2. Market · News · Impact · Actions, where Working Capital sits under the market card. The former "Reference & admin" section (assumption overrides, cash flows by driver, debt maturity, advanced market tools, assumption reviews, risk assistant and News analysis) is removed from the page; the cash-flow detail stays available from the liquidity chart's "Cash-flow detail" drawer, and the assistant from "Ask". The component files are kept but no longer rendered.


## Treasury recommendation agent

The existing `/api/v1/recommendations/agent` endpoint and single Run agent action
produce one ranked list. `kind` is `hedge`, `working_capital`, `funding`,
`operations` or `monitoring`. The same model considers news, the saved forecast
and engine diagnosis; it may submit review actions without testing a hedge.
Hedges retain the existing candidate tests, grades and engine evidence. Other
actions require valid Event IDs, rationale, timing, conditions and alternatives;
they have `grade=unquantified` and `effect=null`. They must not claim an engine-tested
benefit. No new agent, execution tool, database table or approval workflow is added.
Saved v1 hedge sessions remain readable. Provider fallback remains explicitly
rule-based and includes a news-assumption review when news is available.

## Treasurer controls and assessment separation (7 Nov 2026 update)

The What-if selector now contains only Observed moves, Manual scenario and the
existing standard engine presets. News Events and all Apply/Use as scenario
buttons are removed from the dashboard. Individual news forecasts remain available
in the News Tracker and chart, calculated independently of manual assumption
overrides. All other parameter defaults, slider bounds and standard scenarios are
unchanged. This supersedes the earlier descriptions of editing news inputs in
What-if above.

Recommendation results are explicitly separate news-plus-included-What-if
assessments, with their news assumptions and manual inputs inspectable. Results
are marked as previous assessments when current inputs, horizon or news-window
selection differs; loaded historical sessions are always labelled historical.
Structured recommendation amounts are displayed in EUR using the current dashboard
exchange rate and its date, without altering saved USD calculations. Written source
amounts retain their stated currency, as explained next to the results.

Assistant evidence buttons resolve saved article titles and are labelled Supporting
sources, separate from proposed actions. Missing source titles are explicit.
