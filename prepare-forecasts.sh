#!/usr/bin/env bash
# Create saved model forecasts for the current Events (as startup does) without collecting news; keys come from news-engine-v8/.env.
set -euo pipefail
cd "$(dirname "$0")"
CASH_ENGINE=spec \
TREASURY_WORKSPACE="$PWD" \
NEWS_V8_EXPORT_DIR="$PWD/data/news-v8-exports" \
NEWS_V8_DATABASE="$PWD/data/news-current.sqlite" \
TREASURY_STATE_DB="$PWD/data/treasury.sqlite" \
exec .venv/bin/python -c "from treasury_gateway.app import create_app; create_app().state.prepare_impacts()"
