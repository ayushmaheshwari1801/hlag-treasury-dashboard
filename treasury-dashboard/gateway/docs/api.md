# Gateway API

One app (`python -m treasury_gateway.app --port 8080`) serving the built frontend (`FRONTEND_DIST=frontend/dist`) and:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/dashboard?horizon_months=3\|6\|12` | Composite: `finance` (market metrics, EUR cash impact, liquidity, debt, actions), `news`, `brief`, `sources` |
| `POST /api/v1/dashboard` | What-if: `{horizon_months, shocks: {slider_id: value}}`. Slider ids, bounds and presets are in `finance.scenario`. Nothing is stored |
| `GET /api/v1/news/articles`, `/articles/{id}`, `/brief` | Read-only view of saved v8 contract-8 Articles, Events and briefs |
| `/api/v1/sensitivity/...` | Sensitivity router (modules, scenario, history, reviews, audit, compound) |
| `GET/PUT/DELETE /api/v1/sensitivity/data-sources/{liquidity,debt_maturity,limits,exposures}` | Replace demo data with connected data (validated, audited, persisted if `SENSITIVITY_DATA_DIR` set). PUT body `{actor, data}`; DELETE `?actor=` restores the demo default |
| `GET /api/v1/integrations` | Status of every connection point |
| `POST /api/v1/assistant/query` | Assistant. Body `{question, mode: "question"\|"briefing", horizon_months, shocks}`. Returns `{answer, source: "model"\|"offline", model, cited_rule_ids, cited_article_ids, guard}` |
| `GET /api/v1/assistant/status` | Whether a model is connected |

Environment: `NEWS_V8_EXPORT_DIR` (default: workspace `data/news-v8-exports`), `NEWS_V8_DATABASE` (the active cumulative Event pool),
`NEWS_V8_URL` and `NEWS_API_TOKEN` (explicit v8 collection), `SENSITIVITY_MARKET_MODE=live|fixture`, `SENSITIVITY_AUDIT_DB`,
`SENSITIVITY_DATA_DIR`, `DASHBOARD_API_TOKEN` (required off loopback), `FRONTEND_DIST`.
Frontend: `VITE_API_BASE`, `VITE_API_TOKEN` (or `localStorage.dashboard_token`); `npm run dev` proxies `/api` to `VITE_API_TARGET`.
No endpoint trades, pays, borrows or hedges.

Data kinds: liquidity `{reported_total_usd_m, balance_date, source, [balance_date_eurusd, minimum_liquidity_eur_m, provenance]}`;
debt_maturity `{categories, years, values{year:[per category]}, source}`; limits `{module:{name, amount, currency}}`;
exposures `{positions:[...], hedges:[...]}` (shape: `sensitivity-engine/src/treasury_sensitivity/fixtures/exposures.json`).

## Insights (in `finance.insights` of every dashboard response)
- `what_changed`: 3–4 sentences built from the payload (most unusual move, largest adverse row, headroom change, nearest breakeven).
- `waterfall`: reported reserve -> each adverse cash row -> scenario reserve; favourable rows listed, not counted.
- `breakevens`: shock at which one row alone uses up the remaining headroom (impacts are linear in the shock; one probe run per row).
- `debt`: refinancing peak year and next-12-month coverage (assumption stated in the payload).
- `market_metrics[].unusual`: latest 1-week move vs the past year of non-overlapping 1-week moves (z-score, count of larger moves).
- News items carry `scenario_tests` (slider + value) for confirmed numeric moves; the UI runs them through `POST /api/v1/dashboard`.

## Assistant

Uses any OpenAI-compatible chat endpoint (OpenRouter by default), configured with the same variables as the news engine:
`OPENROUTER_API_KEY` (or `ASSISTANT_API_KEY` / `LLM_API_KEY`), `LLM_MODEL` (or `ASSISTANT_MODEL`), optional `LLM_BASE_URL`.
Environment variables take priority. File settings come from `ASSISTANT_ENV_FILE` if specified; otherwise the first existing file among `./.env` and workspace `news-engine-v8/.env` is used. See `gateway/.env.example`.

Safeguards: the model sees only a compact JSON of the dashboard (figures, insights, actions, news); news text is marked untrusted;
every number in the answer must match a dashboard figure (rounding allowed) or the answer is repaired once and otherwise withheld;
cited rule and article IDs are filtered to ones that exist. Without a key, or if the provider fails, answers are assembled from
the dashboard insights and labelled `offline`. The assistant is read-only.
