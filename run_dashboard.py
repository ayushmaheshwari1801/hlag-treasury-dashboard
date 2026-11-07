"""Start the local Treasury dashboard with the v8 news service mounted in-process."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import secrets
import sys
import webbrowser

import uvicorn

ROOT = Path(__file__).resolve().parent
for source in ('treasury-dashboard/gateway/src', 'treasury-dashboard/sensitivity-engine/src', 'news-engine-v8/src'):
    sys.path.insert(0, str(ROOT / source))


def prepare_news(reader):
    """Finish the same collection used by Refresh news before exposing the dashboard."""
    from news_engine.api import RunRequest
    from news_engine.clients import ExaClient, LLMClient
    from news_engine.config import load_credentials
    from news_engine.pipeline import run_pipeline
    from news_engine.storage import Store

    print("Preparing today's news and events. The dashboard will open when ready...", flush=True)
    credentials = load_credentials(ROOT / 'news-engine-v8/.env', require_exa=True)
    with ExitStack() as stack:
        llm = LLMClient(credentials.llm_api_key, credentials.llm_model, credentials.llm_base_url)
        stack.callback(llm.close)
        exa = ExaClient(credentials.exa_api_key)
        stack.callback(exa.close)
        store = stack.enter_context(Store(Path(os.environ['NEWS_V8_DATABASE'])))
        result = run_pipeline(store, llm, exa=exa, **RunRequest().model_dump())
    # Preserve partial/failed outcomes for diagnosis without presenting them as ready.
    reader.import_result(result.model_dump(mode='json'))
    if result.status != 'completed':
        raise RuntimeError('News preparation did not finish successfully. Saved work is preserved; the dashboard has not opened.')
    print(f"News ready: {len(result.events)} events. Preparing cash impacts...", flush=True)


class DashboardServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            webbrowser.open(f'http://127.0.0.1:{self.config.port}/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    os.environ.setdefault('TREASURY_WORKSPACE', str(ROOT))
    os.environ.setdefault('MARKET_DATA_DB', str(ROOT / 'market_data.sqlite'))
    os.environ.setdefault('TREASURY_STATE_DB', str(ROOT / 'data/treasury.sqlite'))
    os.environ.setdefault('NEWS_V8_EXPORT_DIR', str(ROOT / 'data/news-v8-exports'))
    os.environ.setdefault('FRONTEND_DIST', str(ROOT / 'treasury-dashboard/frontend/dist'))
    os.environ.setdefault('NEWS_V8_DATABASE', str(ROOT / 'data/news-current.sqlite'))
    os.environ.setdefault('NEWS_API_TOKEN', secrets.token_urlsafe(32))
    os.environ.setdefault('NEWS_V8_URL', f'http://127.0.0.1:{args.port}/v8')
    from treasury_gateway.app import create_app
    from treasury_gateway.news_v8 import NewsV8Reader
    from news_engine.api import create_app as create_news_app
    reader = NewsV8Reader(Path(os.environ['NEWS_V8_EXPORT_DIR']))
    try:
        prepare_news(reader)
        app = create_app(token=os.environ.get('DASHBOARD_API_TOKEN'))
        app.state.prepare_impacts()
    except Exception:
        print('Startup stopped: news or cash-impact preparation did not complete. Check service availability and restart. Completed work is preserved.', file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    app.mount('/v8', create_news_app(database=Path(os.environ['NEWS_V8_DATABASE']),
                                    env_file=ROOT / 'news-engine-v8/.env', token=os.environ['NEWS_API_TOKEN']))
    DashboardServer(uvicorn.Config(app, host='127.0.0.1', port=args.port)).run()


if __name__ == '__main__':
    main()
