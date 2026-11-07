"""Registry of connection points. Each entry says what it is, how it is configured and whether it is live.

``status`` is computed from configuration, never assumed: nothing reports "connected" unless it was configured here.
"""
from __future__ import annotations

import os


def registry(*, news_available: bool, news_dir: str | None, market_mode: str, data_sources: dict, assistant_mode: str = "offline", assistant_model: str | None = None) -> list[dict]:
    return [
        {"id": "news_engine", "name": "News engine v8", "kind": "contract-8 Article/Event workflow",
         "status": "connected" if news_available else "not configured",
         "configure": "NEWS_V8_EXPORT_DIR for saved RunResult JSON; NEWS_V8_URL and NEWS_API_TOKEN for explicit collection",
         "contract": "../news-engine-v8/docs/dashboard-integration.md", "endpoints": ["/api/v1/intelligence/events", "/api/v1/intelligence/collect"]},
        {"id": "cash_market", "name": "Cash engine market data", "kind": "shared SQLite store",
         "status": "configured; see per-series availability", "configure": "MARKET_DATA_DB; populate with market_feed.py",
         "contract": "docs/combined-integration.md", "endpoints": ["/api/v1/market/snapshot", "/api/v1/risk/dashboard"]},
        {"id": "market_data", "name": "Market data (ECB, Frankfurter, fixtures)", "kind": "provider",
         "status": "live (unverified against real services)" if market_mode == "live" else "demo",
         "configure": "SENSITIVITY_MARKET_MODE=live (ECB SDMX, Frankfurter); bunker and SCFI series need a licensed feed",
         "contract": "sensitivity-engine/docs/api.md", "endpoints": ["/api/v1/sensitivity/modules/{module}/history"]},
        *({"id": f"data_{k}", "name": {"liquidity": "Liquidity (TMS / bank balances)", "debt_maturity": "Debt maturity profile",
                                       "limits": "Approved limits", "exposures": "FX exposures and hedges (ERP / TMS)"}[k],
           "kind": "validated override", "status": v["source"],
           "configure": f"PUT /api/v1/sensitivity/data-sources/{k} (actor + data); DELETE restores the demo default",
           "contract": "sensitivity-engine/docs/api.md", "endpoints": [f"/api/v1/sensitivity/data-sources/{k}"]}
          for k, v in data_sources.items()),
        {"id": "assistant", "name": "AI assistant (briefing and questions)", "kind": "LLM, OpenAI-compatible",
         "status": f"connected ({assistant_model})" if assistant_mode == "model" else "offline (no AI key: answers from dashboard insights)",
         "configure": "Set OPENROUTER_API_KEY (or ASSISTANT_API_KEY) and LLM_MODEL, in the environment or a .env file (news-engine-v8/.env is read too)",
         "contract": "gateway/docs/api.md", "endpoints": ["/api/v1/assistant/query"]},
        {"id": "execution", "name": "Trading / payments / hedging execution", "kind": "intentionally absent", "status": "none by design",
         "configure": "Not provided. The dashboard produces review prompts only.", "contract": None, "endpoints": []},
    ]
