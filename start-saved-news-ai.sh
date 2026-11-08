#!/usr/bin/env bash
# Start the dashboard on saved news (no news collection) with the AI on; model keys are read from news-engine-v8/.env.
set -euo pipefail
cd "$(dirname "$0")"
CASH_ENGINE=spec \
TREASURY_WORKSPACE="$PWD" \
FRONTEND_DIST="$PWD/treasury-dashboard/frontend/dist" \
NEWS_V8_EXPORT_DIR="$PWD/data/news-v8-exports" \
NEWS_V8_DATABASE="$PWD/data/news-current.sqlite" \
TREASURY_STATE_DB="$PWD/data/treasury.sqlite" \
exec .venv/bin/python -m treasury_gateway.app --port "${PORT:-8080}"
