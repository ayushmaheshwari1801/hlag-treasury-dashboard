# Treasury sensitivity engine

Deterministic backend for the dashboard's market-risk modules: **FX (CAD payable)**, **EUR interest rates**,
**Rotterdam VLSFO 0.5%** and **container freight (SCFI)**. The dashboard uses the separate [v8 news engine](../../news-engine-v8/README.md),
which this engine never imports or modifies. It supports decision review only: there is no endpoint that trades, pays,
borrows or hedges.

Built from the *Treasury Market Risk Dashboard* specification v1.0 (25 Oct 2026).

## What it does

- Computes each module's sensitivity table with exact `Decimal` arithmetic and the specification's formulas.
- Gives every number one provenance tag (`OBSERVED`, `DISCLOSED HISTORICAL`, `DERIVED`, `ILLUSTRATIVE`, `UNAVAILABLE`).
  A missing input produces `UNAVAILABLE` and `NOT ASSESSABLE`, never zero and never `LOW`.
- Assigns `CRITICAL / HIGH / LOW / NOT ASSESSABLE` with a deterministic rules engine and a rule ID on every result.
- Serves history (1M / 3M / 6M / 1Y / All) from actual observations only. Gaps are reported, never interpolated.
- Aggregates a compound stress that excludes incompatible horizons and earnings proxies and removes bunker-surcharge double counting.
- Stores snapshots, formula/threshold versions, reviewer comments and assumption overrides in an append-only, hash-chained SQLite log.
- Links news to modules as context only. News never enters a formula.

## Layout

```
sensitivity-engine/
  src/treasury_sensitivity/
    defaults.yaml        versioned defaults, bounds and thresholds (the one place to edit numbers)
    models.py            provenance, Quantity, ModuleResult contracts
    market.py            series validation, previous-comparable logic, freshness, fixture + live adapters
    engines/             fx.py  interest.py  bunker.py  freight.py  common.py
    materiality.py       status rules (spec s8)
    compound.py          compound stress (spec s9)
    audit.py             append-only audit store
    explain.py           deterministic explanation + guard for any LLM-written text
    news_context.py      read-only bridge to the news engine's article exports
    service.py           orchestration
    api.py               FastAPI router and standalone app
    fixtures/            SYNTHETIC market series, exposures and hedges
  scripts/build_fixtures.py   regenerates the fixtures deterministically
  tests/                      224 tests
  docs/                       API contract, decisions and open items
```

## Run it

```sh
cd sensitivity-engine
pip install -e ".[test]"            # or: uv pip install -e ".[test]"
python -m pytest                    # 224 tests, no network

SENSITIVITY_AS_OF=2026-10-31 python -m treasury_sensitivity.api --port 8100
curl localhost:8100/api/v1/sensitivity/overview
```

Open `http://localhost:8100/` for a one-file demo page (module cards, editable assumptions, sensitivity table, history, news context, compound stress, review and audit log). It only calls the API, so it shows exactly what a real frontend would receive. OpenAPI is at `/openapi.json`. See [docs/api.md](docs/api.md).

| Variable | Purpose | Default |
| --- | --- | --- |
| `SENSITIVITY_AS_OF` | Fix the reference date (the fixtures end on 2026-10-30, so they go stale after about 4 days) | now, Asia/Singapore |
| `SENSITIVITY_MARKET_MODE` | `fixture` or `live` (ECB and Frankfurter only; see limits below) | `fixture` |
| `SENSITIVITY_CACHE_DIR` | Where live observations accumulate | `data/market_cache` |
| `SENSITIVITY_AUDIT_DB` | Audit database path | `data/sensitivity_audit.sqlite` |
| `SENSITIVITY_NEWS_DIR` | Folder searched for the news engine's `articles_*.json` exports | none |
| `SENSITIVITY_API_TOKEN` | Bearer token; required to listen on a non-loopback address | none |

To serve it beside the news API, mount the router in the unified app:

```python
from treasury_sensitivity.api import create_router
from treasury_sensitivity.service import SensitivityService
app.include_router(create_router(SensitivityService(news_dir=...)))
```

## Read this before relying on a number

- **All market data in fixture mode is synthetic.** The last two points of each series reproduce the specification's
  illustrative tables (0.73; 2.00% to 2.25%; 650 to 700; 1,600 to 1,728). They are tagged `ILLUSTRATIVE`.
- **Liquidity is a placeholder: USD 5,000m**, invented so the status engine can run. Every `HIGH`/`CRITICAL`/`LOW`
  badge depends on it. Replace it in `defaults.yaml` with validated payment-date liquidity.
- **Thresholds (1% / 3% / 80%) are demonstration values, not company policy.**
- **Exposures, hedges, consumption, volumes, recovery and collection fractions are assumptions.** Company figures from
  the specification (H1 2026, FY2025 sensitivities) are shown as `DISCLOSED HISTORICAL` and flagged for page-level verification.
- All four modules report reliability `LOW`, as the specification requires for the prototype.

More detail, including interpretation choices and open items, is in [docs/decisions-and-open-items.md](docs/decisions-and-open-items.md).
