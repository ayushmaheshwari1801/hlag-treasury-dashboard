# Treasury risk workspace

This workspace combines public market feeds, the **v8 Article/Event news engine**, predictive what-if inputs, a deterministic four-driver cash-flow engine, and the supplied React dashboard. All project documentation and interface text are in English.

## Run locally

On Windows, run `setup.cmd` once, then `start.cmd`. Both the dashboard backend
and mounted news v8 service use the single workspace-root `.venv`; do not run
`uv sync` in `news-engine-v8` for this combined application. Node/npm installs
frontend build dependencies, not a second Python environment. The older local
`news-engine-v8/.venv` is not used by `start.cmd`.

If another copy is already running on port 8080, use `start.cmd --port 8081`
and open http://127.0.0.1:8081/.

On this computer, `start.cmd` and `setup.cmd` prefer the working root
`.venv-local` when present; otherwise they use `.venv`. Virtual environments
copied from another computer are not portable.
The launcher uses `data/news-current.sqlite` for the revised
classification policy unless `NEWS_V8_DATABASE` is explicitly set. Prior databases
and saved exports are preserved; incompatible explicitly selected databases fail
the policy guard rather than being rewritten.

For a source ZIP, follow [source-package setup](SOURCE_PACKAGE.md) to restore the supplied CSV market snapshot, company inputs and saved news before starting. The private handoff archive includes the requested .env credentials and current market/news snapshots; installed dependencies and historical calculation runs are excluded.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
cd treasury-dashboard/frontend
npm ci
npm run build
cd ../..
.venv/Scripts/python.exe run_dashboard.py
```

Open http://127.0.0.1:8080. The launcher mounts the active `news-engine-v8` service at `/v8`, using `data/news-current.sqlite` by default. Normal startup collects news and completes cash-impact preparation before opening the page. The retired pre-v8 engine and its obsolete CI jobs have been removed. Existing databases and saved results are preserved. The service listens on loopback only. Set `DASHBOARD_API_TOKEN` to protect gateway endpoints; the v8 service has a separate internal token, generated in memory if unset.

The original four-column dashboard is the sole webpage. Its News Tracker, what-if controls, cash-impact cards, liquidity cards and drawers use the v8/cash-engine integration. There is no second dashboard or workspace navigation. The backend reads `market_data.sqlite` and versioned company inputs.

Missing inputs can be populated through `inputs/manual_market.csv` and `inputs/manual_profile.csv` with `.venv/Scripts/python.exe import_manual_inputs.py`. Their values, public sources and assumptions are documented in [manual input notes](inputs/manual-assumptions.md), rather than extra webpage badges, as requested. The official profile remains available as the underlying source version.

## Cash engine: hlag_engine (`CASH_ENGINE`)

The cash calculations run on **hlag_engine** from the separate `treasury-calculation-engine` repository by default
(`CASH_ENGINE=spec`). `CASH_ENGINE=mock` switches back to the built-in prototype (`gateway/.../risk_engine.py`).

With `spec`, market data comes from hlag_engine's store (`hlag_engine/data/market.sqlite` in the engine checkout,
override with `ENGINE_MARKET_DB`) and the company profile from its active profile version (`active.yaml`). Both are
read-only from the dashboard: the store is opened read-only, runs are not written to it, `PUT /api/v1/risk/profile`
and `POST /api/v1/market/import-csv` return 409. `MARKET_DATA_DB` is not used in this mode. Saved runs still go to
the dashboard's own `data/treasury.sqlite`.

Install hlag_engine into the workspace `.venv` as an **editable** install (needs Python 3.12 or later; the engine
locates its store and profile inside its own checkout, so a regular install would not find them):

```bash
# macOS / Linux
.venv/bin/python -m pip install -e /path/to/treasury-calculation-engine
CASH_ENGINE=spec .venv/bin/python run_dashboard.py
```

```powershell
# Windows
.venv/Scripts/python.exe -m pip install -e C:/path/to/treasury-calculation-engine
$env:CASH_ENGINE = 'spec'; .venv/Scripts/python.exe run_dashboard.py
```

If hlag_engine is not installed, the gateway refuses to start with `CASH_ENGINE=spec` rather than falling back to
the prototype. Engine endpoints for the frontend: `GET /api/v1/engine/market`,
`GET /api/v1/engine/runs/{run_id}/cashflows` and `GET /api/v1/engine/assumptions`. `POST /api/v1/engine/market/refresh` (`{"actor": name}`) is
the gateway's only write to the engine store: an explicit, named action that runs hlag_engine's API data load (ECB,
NY Fed, FRED) up to today and is recorded in the engine's audit log with that person as the actor. WCI and fuel are
not fetched live; they come from the weekly CSV loaded in the engine repository. `GET /api/v1/engine/hedge-test/options` and `POST
/api/v1/engine/hedge-test` (`{"hedge": {target, instrument, coverage_pct, tenor_months, start_date}, ...}` plus the
page's scenario fields) run a hypothetical bunker hedge with hlag_engine's `test_hedge` steps (with/without the hedge,
under the scenario and its mirror) using `run_scenario(write=False)`, so nothing is written to the engine store and
nothing is executed. Saving and cost are assigned by fuel direction (saving = fuel up, cost = fuel down). Swap margin
dated on T0 itself is outside the liquidity path (it starts the day after T0) and is flagged in the response. `POST /api/v1/risk/run` and
`POST /api/v1/dashboard` accept `"overrides": {key: value}` with the keys and units listed by
`/api/v1/engine/assumptions`. Demo mode is not available with `spec`; a demo request runs the real engine and logs a
warning.

Row Methodology view (`spec` only, read-only): clicking a row of Estimated Cash-Flow Impact opens how that figure was
built. `POST /api/v1/engine/methodology` (`{row_id, run_id, horizon_months}`) returns the engine's own text, formulas
with current values, the row's components (inside the selected horizon, after it, after the engine horizon),
assumptions with override status, market and disclosed sources, playbook entries and limitations, all from
`hlag_engine.engine.methodology`. `POST /api/v1/engine/sensitivity` (`{row_id: freight|bunker, ...page scenario}`) makes
five real engine runs with only that driver's shock set to −20/−10/0/+10/+20% (bunker: that % of VLSFO Rotterdam as
USD/t on every grade, as the slider does); results are cached. The view's header reads the engine store's T0 snapshot
(real prints only, each with its published date and source name); the page tiles keep their own market reading.
Direct cash flows are not part of any driver row and are left out of the view's breakdown.

## News to forecast to cash

1. Normal startup searches news, generates Events and prepares their cash impacts before opening the dashboard. **Refresh news** updates the collection later. File import is not available in the webpage.
2. News is grouped Regulatory, Macro and Industry, ordered by descending priority within each category. Each Event cash estimate is independent of the main What-if scenario.
3. What-if offers observed moves, manual sliders and the retained standard scenarios. News Events are not selectable inputs to What-if. Cash estimates are assumptions, not observed prices.
4. Inspect liquidity, driver attribution, cash detail and sources. Agent outputs use short business-focused bullets, with reasons, missing evidence and follow-up when an effect is unquantified.

Configure `OPENROUTER_API_KEY` (or `ASSISTANT_API_KEY`), `LLM_MODEL`, and optionally `LLM_BASE_URL` for model forecasting/recommendations. For v8 discovery also configure `EXA_API_KEY`. The launcher reads the news service's credentials from `news-engine-v8/.env`; environment variables take priority. No keys are needed to read saved results or run deterministic calculations. Without a model, forecasting uses explicitly labelled fixed-rule assumptions for recognized news themes; unmatched events produce no forecast. Provider failures are reported rather than disguised as model results.

## Market feeds

```powershell
python market_feed.py init
python market_feed.py fetch --provider ecb
python market_feed.py fetch --provider nyfed
$env:FRED_API_KEY = 'your-key'
python market_feed.py fetch --provider fred
python market_feed.py import-csv prices.csv
python market_feed.py snapshot --as-of 2026-11-02
python market_feed.py queue
python market_feed.py confirm 1 --user treasury-reviewer
```

The first fetch loads 731 days; subsequent fetches overlap the oldest latest date by 14 days to capture recent revisions. Override with `--start` and `--end`. The three providers run serially; failed requests are not retried. Missing FRED credentials do not prevent ECB/NY Fed updates. Keys are never stored in source references.

The 20 series are six FX rates, SOFR, ESTR, UST_2Y, EA_2Y, BB_OAS, BRENT, four WCI indices, and four bunker port/grade prices. ECB FX crosses use matching observation dates and USD per foreign unit. Rates allow negative values. EA_2Y uses the AAA government-bond spot curve, not a swap quote. BB OAS is percent (2 means 200bp).

CSV columns: `series_id,date,value,unit,source,source_ref`, with optional `label` and `confirmed`. All twenty known series can be imported; units are checked against the catalogue. `label` accepts MARKET, PROXY or ASSUMPTION and is retained internally. See `weekly_template.csv` and the populated `inputs/manual_market.csv`. WCI IDs: WCI_SHA_RTM, WCI_SHA_LAX, WCI_RTM_NYC, WCI_COMPOSITE, unit USD/FEU. Fuel IDs: VLSFO_SIN, VLSFO_RTM, HSFO_SIN, HSFO_RTM, unit USD/t. Keep port, delivery basis, 0.5% VLSFO and HSFO 380cSt consistent. Manual assumptions and publicly transcribed values are distinguished in the input documentation.

CSV imports validate the entire file and use a transaction. Fuel changes above 40% and WCI above 80% against a recent accepted observation enter the confirmation queue. Normal imported prices are flagged unconfirmed. Every changed observation preserves its old record in revisions. Daily observations become stale after five calendar days, weekly after ten. Snapshots never look ahead or treat carry-forward as a new print. Load at least 13 months of WCI and one year of bunker history, covering the profile anchor period.

## Configuration and validation

`MARKET_DATA_DB`, `TREASURY_STATE_DB`, `NEWS_V8_DATABASE`, `NEWS_V8_EXPORT_DIR` and `FRONTEND_DIST` override launcher defaults. The database is local and should be backed up. Optional model calls occur only from explicit workflow/forecast/recommendation requests. There is no trade or payment execution.

```powershell
$env:PYTHONPATH = "$PWD/treasury-dashboard/gateway/src;$PWD/treasury-dashboard/sensitivity-engine/src;$PWD"
.venv/Scripts/python.exe -m pytest treasury-dashboard/gateway/tests treasury-dashboard/sensitivity-engine/tests -q
python -m unittest test_market_feed -v
```

See [integration details](treasury-dashboard/docs/combined-integration.md) for contracts, provenance, profile updates and implementation limits. Public visibility is not permission to redistribute third-party data; ICE, Drewry and bunker providers' terms still apply.

## Current dashboard consistency

Market detail uses the same cash-engine calculation and market store as the main cards; the old fixture sensitivity dialog is retired. History requests ignore stale responses. Data connections contains the current All market series panel and its audited refresh action. Saved-calculation reviews are recorded against immutable cash-engine run IDs.

The assistant receives per-run assumption overrides. Recommendation sessions can include the complete selected preset or edited Event forecast, as well as sliders and overrides. An explicit preset is not replaced by a newly inferred news scenario. News execution failures persist their stage and error type while preserving original Articles and pending work; refresh is always explicit, with no automatic provider retry.

The current launcher uses `data/news-current.sqlite` for news collection. Treasurer-facing screens use business labels and hide internal identifiers. Historical company and assessment data are retained.
## Startup preparation

`run_dashboard.py` now searches news and generates Events automatically on every
start, using the same collection settings as Refresh news. It then forecasts and
calculates each current Event's cash impact for the default six-month horizon,
reusing saved model forecasts only when the Event, news snapshot, date and horizon
match. Both quantified impacts and unquantified monitoring assessments are retained.
Completed work is saved before starting the dashboard service, which opens the browser only when
the service is ready. Startup therefore includes real news/model calls and may
take several minutes. If collection fails or is partial, startup exits with an
error and preserves saved work; it does not open a dashboard with stale results
or automatically retry provider failures.
No Estimate impact click is required. Changes to the news selection or horizon
also prepare missing forecasts automatically; successful forecasts are reused.

