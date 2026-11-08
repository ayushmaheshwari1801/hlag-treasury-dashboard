"""Treasury API: shared market store, v8 news, signal routing and deterministic cash engine."""
import argparse
import hmac
import os
import threading
from concurrent.futures import Future
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from treasury_sensitivity.api import create_router
from treasury_sensitivity.audit import AuditStore
from treasury_sensitivity.models import StrictModel, ValidationProblem
from treasury_sensitivity.service import SensitivityService

from .assistant import Assistant, build_context, cost_stage, provider_config
from .integrations import registry
from .news_v8 import NewsV8Reader
from .market_data import create_router as market_router, read_snapshot
from .risk_api import create_router as risk_router, RunRequest
from .signals import IntelligenceStore, create_router as intelligence_router

DEMO_NEWS = Path(__file__).resolve().parents[2] / "demo_news"


class WhatIf(StrictModel):
    horizon_months: int | None = None
    shocks: dict[str, float] = Field(default_factory=dict)
    news_limit: int = Field(default=6, ge=1, le=50)
    forecast_id: str | None = None
    scenario: dict | None = None
    as_of: date | None = None
    overrides: dict[str, float | bool | str] = Field(default_factory=dict)  # per-run profile overrides (engine keys)


class HedgeInput(StrictModel):
    target: Literal["fuel_all", "vlsfo", "hsfo"] = "fuel_all"
    instrument: Literal["swap", "fixed_price_supply"] = "swap"
    coverage_pct: float = Field(ge=0, le=100)          # whole percent; the engine checks its own policy range
    tenor_months: int = Field(ge=1, le=36)
    start_date: date | None = None                     # default T0


class HedgeTestRequest(StrictModel):
    hedge: HedgeInput
    shocks: dict[str, float] = Field(default_factory=dict)
    forecast_id: str | None = None
    scenario: dict | None = None
    as_of: date | None = None
    overrides: dict[str, float | bool | str] = Field(default_factory=dict)
    horizon_months: int | None = None                  # accepted for symmetry with the dashboard; not used


class RecommendationAgentRequest(StrictModel):
    """Treasury recommendation agent session. News is chosen by publication window (and optionally by Event ID), or by a
    saved forecast. as_of defaults to news_to, so neither news nor market data after it is used."""
    news_from: date | None = None
    news_to: date | None = None
    event_ids: list[str] | None = None
    forecast_id: str | None = None
    as_of: date | None = None
    horizon_days: int = Field(default=90, ge=1, le=365)
    forecast_mode: Literal["rule_based", "model", "auto"] = "rule_based"
    mode: Literal["auto", "model", "rule_based"] = "auto"
    shocks: dict[str, float] = Field(default_factory=dict)
    overrides: dict[str, float | bool | str] = Field(default_factory=dict)
    scenario: dict | None = None
    max_runs: int = Field(default=20, ge=4, le=20)


class RunReview(StrictModel):
    reviewer: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=2000)


class AssistantQuery(StrictModel):
    overrides: dict[str, float | bool | str] = Field(default_factory=dict)
    question: str = Field(default="", max_length=2000)
    mode: Literal["question", "briefing"] = "question"
    horizon_months: int | None = None
    shocks: dict[str, float] = Field(default_factory=dict)      # answer about the what-if view the user is looking at
    forecast_id: str | None = None
    scenario: dict | None = None
    as_of: date | None = None


def create_app(*, service: SensitivityService | None = None, news_dir: Path | str | None = None, token: str | None = None,
               allowed_origins=(), frontend_dir: Path | str | None = None, assistant: Assistant | None = None,
               market_db: Path | str | None = None, state_db: Path | str | None = None,
               cash_engine: str | None = None, market_sources=None) -> FastAPI:
    if "*" in allowed_origins:
        raise ValueError("Configure explicit frontend origins, not a wildcard")
    workspace = Path(os.environ.get('TREASURY_WORKSPACE', str(Path(__file__).resolve().parents[4])))
    news_path = Path(news_dir) if news_dir is not None else Path(os.environ.get('NEWS_V8_EXPORT_DIR', str(workspace / 'data/news-v8-exports')))
    news = NewsV8Reader(news_path)
    cash_engine = (cash_engine or os.environ.get('CASH_ENGINE') or 'spec').strip().lower()
    if cash_engine not in {'spec', 'mock'}:
        raise ValueError('CASH_ENGINE must be spec or mock')
    engine_store = None
    try:
        from .engine_adapter import default_store_path
        engine_store = default_store_path()
    except ImportError as exc:
        if cash_engine == 'spec':
            raise RuntimeError('CASH_ENGINE=spec needs hlag_engine installed in this environment '
                               '(see README: pip install -e <treasury-calculation-engine>), or set CASH_ENGINE=mock') from exc
    if cash_engine == 'spec':
        # Market data and the profile come from hlag_engine's store and active profile, read-only.
        market_path = Path(market_db or engine_store)
        if not market_path.is_file():
            raise RuntimeError(f'CASH_ENGINE=spec: hlag_engine market store not found at {market_path} (set ENGINE_MARKET_DB)')
    else:
        market_path = Path(market_db or os.environ.get('MARKET_DATA_DB', str(workspace / 'market_data.sqlite')))
    state_path = Path(state_db or os.environ.get('TREASURY_STATE_DB', str(workspace / 'data/treasury.sqlite')))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    intelligence = IntelligenceStore(state_path)
    saved_news, _ = news.latest()
    if saved_news:
        intelligence.ingest_v8(saved_news)
    service = service or SensitivityService(
        audit=AuditStore(os.environ.get("SENSITIVITY_AUDIT_DB", "data/sensitivity_audit.sqlite")), news_dir=news_path)
    assistant = assistant or Assistant(provider_config())
    app = FastAPI(title="Treasury decision dashboard API", version="1.0.0")
    app.state.service, app.state.news, app.state.intelligence = service, news, intelligence
    if allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins), allow_methods=["GET", "POST", "PUT", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"])
    bearer = HTTPBearer(auto_error=False)

    def authorize(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if token and (creds is None or not hmac.compare_digest(creds.credentials, token)):
            raise HTTPException(401, "A valid bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    protected = [Depends(authorize)]

    @app.exception_handler(ValidationProblem)
    def problem(_, exc: ValidationProblem):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    app.include_router(create_router(service, dependencies=protected))
    app.include_router(market_router(market_path, dependencies=protected, read_only=cash_engine == 'spec'))
    def t0_fx(day):
        """USD per one foreign unit at T0 from the shared market store, for direct cash conversions in forecasts."""
        return {r['series_id'][len('FX_USD_PER_'):]: r['value'] for r in read_snapshot(market_path, day)['series']
                if r['series_id'].startswith('FX_USD_PER_') and r.get('value') is not None}
    app.include_router(intelligence_router(intelligence, news, dependencies=protected, fx_rates=t0_fx))
    cash_routes = risk_router(market_path, state_path, intelligence, news, dependencies=protected, assistant=assistant,
                              cash_engine=cash_engine)
    app.include_router(cash_routes)
    app.state.cash_engine = cash_engine
    if engine_store is not None:
        from .engine_api import create_router as engine_router
        app.include_router(engine_router(cash_routes.run_store, market_path if cash_engine == 'spec' else engine_store,
                                         dependencies=protected, writable=cash_engine == 'spec',
                                         sources_factory=market_sources, today=lambda: service.clock().astimezone(service.tz).date()))

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    # ------------------------------------------------------------------ news (read-only)
    @app.get("/api/v1/news/articles", dependencies=protected, tags=["news"])
    def articles(limit: Annotated[int, Query(ge=1, le=100)] = 20, module: str | None = None):
        return news.tracker(limit, module)

    @app.get("/api/v1/news/articles/{article_id}", dependencies=protected, tags=["news"])
    def article(article_id: str):
        a = news.article(article_id)
        if a is None:
            raise HTTPException(404, "Unknown article in the latest export")
        return a

    @app.get("/api/v1/news/brief", dependencies=protected, tags=["news"])
    def brief():
        return news.brief()

    # ------------------------------------------------------------------ composite
    def page_scenario(day, shocks=None, scenario=None, forecast_id=None, overrides=None):
        """The scenario the page shows: moved sliders on top of the preset or saved news forecast (last week's moves
        repeated when neither is selected), plus per-run overrides. Used by the dashboard and the hedge tool."""
        import sqlite3
        from .dashboard_adapter import slider_scenario
        from .forecast import ForecastAgent
        market = read_snapshot(market_path, day)
        market['history'] = {}
        if market_path.is_file():
            with sqlite3.connect(market_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
                for sid, observed, value in db.execute('SELECT series_id,date,value FROM market_data WHERE date<=? ORDER BY date', (day.isoformat(),)):
                    market['history'].setdefault(sid, []).append({'date': observed, 'value': value})
        if forecast_id and scenario is None:
            forecast = ForecastAgent(intelligence, news).get(forecast_id)
            if forecast is None:
                raise HTTPException(404, 'Unknown saved forecast')
            scenario = forecast['scenario']
        try:
            params = slider_scenario(shocks or {}, day, market, scenario)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if overrides:
            params['overrides'] = {**params.get('overrides', {}), **overrides}
        return params, market, scenario

    def composite(horizon_months, as_of, news_limit, shocks=None, scenario=None, forecast_id=None, overrides=None):
        from .dashboard_adapter import compose_cash_dashboard
        day = as_of or service.clock().astimezone(service.tz).date()
        months = horizon_months or 6
        if months not in {3, 6, 12}:
            raise HTTPException(422, 'horizon_months must be 3, 6 or 12')
        params, market, scenario = page_scenario(day, shocks, scenario, forecast_id, overrides)
        run = cash_routes.run_scenario(RunRequest(as_of=day, scenario=params, forecast_id=forecast_id))
        fin = compose_cash_dashboard({}, run, market, months, shocks or {}, day)
        fin['scenario']['custom'] = bool(shocks or forecast_id or scenario or overrides)
        fin['scenario']['full_scenario'] = run['scenario']
        fin['forecast'] = run.get('forecast')
        n = news.tracker(news_limit)
        pool = news.scenario_events()
        n['scenario_events'] = pool['events']
        n['scenario_category_counts'] = pool.get('category_counts', {})
        n['scenario_category_gaps'] = pool.get('gaps', [])
        n['run_id'] = pool.get('run_id')
        from .event_actions import event_actions
        forecast_context = run.get('forecast') or {}
        selected_ids = forecast_context.get('selected_event_ids', forecast_context.get('event_ids', []))
        action_events = pool['events']
        if selected_ids:
            source = news.saved_run(forecast_context.get('source_run_id', ''))
            action_events = source['events'] if source else []
        actions = event_actions(fin, action_events, selected_ids, run.get('forecast_modified', False))
        fin['all_actions'] = actions
        fin['recommended_actions'] = actions[:3]
        b = news.brief()
        return {"finance": fin, "news": {**n, "source": "v8" if n['available'] else "none"},
                "brief": {"available": b.get("available"), "week_start": b.get("week_start"), "week_end": b.get("week_end"),
                          "executive_summary": b.get("executive_summary"), "status": b.get("status")},
                "sources": {**fin["data_sources"], "news": "v8" if n['available'] else "none",
                            "market": "local"}}

    def prepare_impacts():
        from .forecast import ForecastAgent
        from .startup import prepare_event_impacts
        def calculate(forecast=None):
            return composite(6, date.fromisoformat(forecast['as_of']) if forecast else None, 6,
                             scenario=forecast['scenario'] if forecast else None,
                             forecast_id=forecast['forecast_id'] if forecast else None)
        prepare_event_impacts(ForecastAgent(intelligence, news, fx_rates=t0_fx), calculate)
    app.state.prepare_impacts = prepare_impacts

    @app.get("/api/v1/dashboard", dependencies=protected, tags=["dashboard"])
    def dashboard(horizon_months: Annotated[int | None, Query()] = None, as_of: date | None = None,
                  news_limit: Annotated[int, Query(ge=1, le=50)] = 6):
        return composite(horizon_months, as_of, news_limit)

    @app.post("/api/v1/dashboard", dependencies=protected, tags=["dashboard"])
    def dashboard_whatif(body: WhatIf, as_of: date | None = None):
        """Original page contract, using the same saved cash calculation and news forecast inputs."""
        return composite(body.horizon_months, body.as_of or as_of, body.news_limit, body.shocks, body.scenario, body.forecast_id,
                         body.overrides)

    @app.get("/api/v1/market/history", dependencies=protected, tags=["market"])
    def market_history(series_id: str, as_of: date, range: Literal["1M", "3M", "6M", "1Y", "All"] = "3M"):
        import sqlite3
        from datetime import timedelta
        days = {"1M": 30, "3M": 92, "6M": 183, "1Y": 366}.get(range)
        start = (as_of - timedelta(days=days)).isoformat() if days else "0001-01-01"
        with sqlite3.connect(market_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            points = [{"date": d, "value": v} for d, v in db.execute(
                "SELECT date,value FROM market_data WHERE series_id=? AND date>=? AND date<=? ORDER BY date",
                (series_id, start, as_of.isoformat()))]
        return {"series_id": series_id, "as_of": as_of.isoformat(), "range": range, "points": points}

    @app.post("/api/v1/risk/runs/{run_id}/reviews", dependencies=protected, tags=["review"])
    def review_run(run_id: str, body: RunReview):
        run = cash_routes.run_store.get(run_id)
        if not body.reviewer.strip():
            raise HTTPException(422, "A reviewer name is required")
        with intelligence.lock, intelligence.db:
            intelligence._audit("run_review", run_id, body.reviewer.strip(),
                                {"comment": body.comment, "as_of": run["as_of"]})
            seq = intelligence.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"audit_id": seq, "run_id": run_id}

    @app.get("/api/v1/risk/runs/{run_id}/reviews", dependencies=protected, tags=["review"])
    def run_reviews(run_id: str):
        cash_routes.run_store.get(run_id)
        with intelligence.lock:
            rows = intelligence.db.execute("SELECT id,actor,at,details FROM intelligence_audit WHERE action='run_review' AND subject=? ORDER BY id DESC", (run_id,)).fetchall()
        import json
        return {"reviews": [{"id": r["id"], "reviewer": r["actor"], "at": r["at"], **json.loads(r["details"])} for r in rows]}

    # ------------------------------------------------------------------ hypothetical fuel hedge test (read-only)
    def spec_only():
        if cash_engine != 'spec':
            raise HTTPException(409, 'The hedge tool needs CASH_ENGINE=spec (hlag_engine)')
        from .engine_adapter import EngineAdapter
        return EngineAdapter(market_path)

    @app.get("/api/v1/engine/hedge-test/options", dependencies=protected, tags=["hlag engine"])
    def hedge_options():
        from .engine_adapter import hedge_options as options
        return options(spec_only().shared)

    @app.post("/api/v1/engine/hedge-test", dependencies=protected, tags=["hlag engine"])
    def hedge_test(body: HedgeTestRequest):
        """Test a hypothetical bunker hedge against the page's current scenario: with and without the hedge, under
        the scenario and its mirror (hlag_engine test_hedge steps, run without writing). Nothing is executed."""
        adapter = spec_only()
        day = body.as_of or service.clock().astimezone(service.tz).date()
        params, _, _ = page_scenario(day, body.shocks, body.scenario, body.forecast_id, body.overrides)
        adjustments = [a for a in intelligence.adjustments() if a['status'] == 'approved' and a['review_date'] > day.isoformat()]
        try:
            return adapter.hedge_test(day, params, body.hedge.model_dump(mode='json'), adjustments)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from None

    # ------------------------------------------------------------------ hedge recommendation agent (read-only)
    def recommendation_store():
        # Imported here: the module needs hlag_engine, which the mock cash engine does not.
        from .recommendation_agent import RecommendationSessions
        if not hasattr(app.state, "recommendation_sessions"):
            app.state.recommendation_sessions = RecommendationSessions(intelligence)
        return app.state.recommendation_sessions

    inflight: dict[str, Future] = {}
    inflight_lock = threading.Lock()

    @app.post("/api/v1/recommendations/agent", dependencies=protected, tags=["recommendations"])
    def recommendation_agent(body: RecommendationAgentRequest):
        """Run the Treasury recommendation agent: pick news by publication window, build the news scenario, let the model
        test hypothetical hedges with the engine (write=False) and judge their effect. Nothing is executed.
        Identical requests that arrive while one is running (several open pages preparing advice) share that run."""
        key = body.model_dump_json()
        with inflight_lock:
            running = inflight.get(key)
            if running is None:
                inflight[key] = mine = Future()
        if running is not None:
            return running.result()
        try:
            result = run_recommendation_agent(body)
            mine.set_result(result)
            return result
        except BaseException as exc:
            mine.set_exception(exc)
            raise
        finally:
            with inflight_lock:
                inflight.pop(key, None)

    def run_recommendation_agent(body: RecommendationAgentRequest):
        from .forecast import ForecastAgent, ForecastRequest
        adapter = spec_only()
        from .recommendation_agent import HedgeLab, RecommendationAgent, event_digest, select_events
        sessions = recommendation_store()
        if body.news_from and body.news_to and body.news_from > body.news_to:
            raise HTTPException(422, "news_from must not be after news_to")
        day = body.as_of or body.news_to or service.clock().astimezone(service.tz).date()
        if body.news_to and body.news_to > day:
            raise HTTPException(422, "news_to must not be after as_of (no look-ahead)")
        forecaster = ForecastAgent(intelligence, news)
        notes = []
        news_run_id = None  # the news pool this advice is based on; a newer pool makes the saved advice outdated
        if body.forecast_id:
            forecast = forecaster.get(body.forecast_id)
            if forecast is None:
                raise HTTPException(404, "Unknown saved forecast")
            day = body.as_of or date.fromisoformat(forecast["as_of"])
            if day < date.fromisoformat(forecast["as_of"]):
                raise HTTPException(422, "as_of must not precede the saved forecast (no look-ahead)")
            if body.news_to and body.news_to > day:
                raise HTTPException(422, "news_to must not be after as_of (no look-ahead)")
            payload = news.saved_run(forecast.get("source_run_id", ""))
            news_run_id = (payload or {}).get("run_id")
            if payload is None:
                raise HTTPException(409, "The saved forecast's news snapshot is unavailable")
            events, notes = select_events(payload, None, None,
                                          forecast.get("selected_event_ids") or forecast.get("event_ids") or [])
        else:
            payload, _ = news.cumulative()
            news_run_id = (payload or {}).get("run_id")  # the same ID the dashboard reports as news.run_id
            events, notes = select_events(payload, body.news_from, body.news_to, body.event_ids)
            if not events:
                raise HTTPException(422, "No v8 Events were published in the selected window. " + " ".join(notes))
            if body.scenario is not None:
                forecast = {"forecast_id": None, "mode": "manual", "predictions": [],
                            "label": "Selected dashboard scenario", "scenario": body.scenario}
            else:
                try:
                    with cost_stage("advice"):
                        forecast = forecaster.predict(ForecastRequest(event_ids=[e["event_id"] for e in events], as_of=day,
                                                                  horizon_days=body.horizon_days, mode=body.forecast_mode))
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
                except RuntimeError as exc:
                    raise HTTPException(409, str(exc)) from None
        # Advice on news: the baseline (today's prices) plus the news forecast and any included slider moves.
        params, _, _ = page_scenario(day, body.shocks, body.scenario, forecast["forecast_id"], body.overrides)
        adjustments = [a for a in intelligence.adjustments() if a['status'] == 'approved' and a['review_date'] > day.isoformat()]
        try:
            lab = HedgeLab(adapter, day, params, adjustments, max_runs=body.max_runs)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        window = {"from": body.news_from.isoformat() if body.news_from else None,
                  "to": body.news_to.isoformat() if body.news_to else None}
        context = {"as_of": day.isoformat(), "news_window": window,
                   "events": event_digest(events, payload or {}),
                   "forecast": {k: forecast.get(k) for k in ("forecast_id", "source_run_id", "mode", "predictions", "outlook", "label")},
                   # dashboard slider moves on top of the news forecast: user assumptions, not news facts
                   "page_shocks": dict(body.shocks), "page_overrides": dict(body.overrides),
                   "scenario_inputs": {k: params.get(k) for k in ("name", "start_date", "duration_days",
                                                                   "freight", "bunker", "rates", "fx", "direct")}}
        agent = RecommendationAgent(assistant.config, transport=assistant.transport)
        result = agent.run(lab, context, body.mode)
        return sessions.save({**result, "as_of": day.isoformat(), "news_window": window, "horizon_days": body.horizon_days, "news_run_id": news_run_id,
                              "custom_inputs": bool(body.shocks or body.overrides or body.scenario is not None or body.forecast_id),
                              "event_ids": [e["event_id"] for e in events], "events": context["events"],
                              "forecast_id": forecast["forecast_id"], "forecast": context["forecast"],
                              "page_shocks": context["page_shocks"], "page_overrides": context["page_overrides"],
                              "selection_notes": notes})

    @app.get("/api/v1/recommendations/agent", dependencies=protected, tags=["recommendations"])
    def recommendation_sessions(limit: Annotated[int, Query(ge=1, le=100)] = 20):
        spec_only()
        return {"sessions": recommendation_store().list(limit)}

    @app.get("/api/v1/recommendations/agent/{session_id}", dependencies=protected, tags=["recommendations"])
    def recommendation_session(session_id: str):
        spec_only()
        s = recommendation_store().get(session_id)
        if s is None:
            raise HTTPException(404, "Unknown recommendation session")
        return s

    @app.get("/api/v1/integrations", dependencies=protected, tags=["integrations"])
    def integrations():
        n = news.tracker(1)
        entries = registry(news_available=n["available"], news_dir=str(news.dir) if news.dir else None,
                                         market_mode=os.environ.get("SENSITIVITY_MARKET_MODE", "fixture"),
                                         data_sources={}, assistant_mode=assistant.mode,
                                         assistant_model=assistant.config["model"] if assistant.config else None)
        entries = [entry for entry in entries if entry['id'] != 'market_data']
        snapshot = read_snapshot(market_path, service.clock().astimezone(service.tz).date())
        for entry in entries:
            if entry['id'] == 'cash_market':
                entry['name'] = 'Market data'
                entry['status'] = f"{snapshot['available']} / {snapshot['total']} series available"
        entries.insert(2, {'id': 'company_profile', 'name': 'Company financial profile', 'kind': 'versioned profile',
                           'status': 'available', 'configure': 'GET/PUT /api/v1/risk/profile; CSV inputs and source audit are documented in the workspace.',
                           'contract': 'docs/financial-profile-source-audit.md', 'endpoints': ['/api/v1/risk/profile']})
        if cash_engine == 'spec':
            # These entries describe the old mock's database and profile; with hlag_engine they are read-only views.
            for entry in entries:
                if entry['id'] == 'cash_market':
                    entry.update(name='Market data (hlag_engine store)', kind='hlag_engine market store, read-only here',
                                 configure='Read-only. ECB, NY Fed and FRED are reloaded only by "Refresh market data" in '
                                           'the All market series card (recorded in the engine audit log). WCI and fuel '
                                           'come from the weekly CSV loaded in the engine repository.',
                                 contract='treasury-calculation-engine docs/spec.md 3.6',
                                 endpoints=['/api/v1/engine/market', '/api/v1/engine/market/refresh'])
                elif entry['id'] == 'company_profile':
                    entry.update(name='Company financial profile (hlag_engine)', kind='active profile version, read-only here',
                                 status=f"read-only 路 {cash_routes.current_profile().get('profile_version')}",
                                 configure='Read-only in the dashboard. Profile versions are written and activated in the '
                                           'engine repository; per-run overrides use the Assumptions panel.',
                                 endpoints=['/api/v1/risk/profile', '/api/v1/engine/assumptions'])
        return {"integrations": entries}

    @app.get("/api/v1/assistant/status", dependencies=protected, tags=["assistant"])
    def assistant_status():
        return {"mode": assistant.mode, "model": assistant.config["model"] if assistant.config else None,
                "note": "Set OPENROUTER_API_KEY (or ASSISTANT_API_KEY) and LLM_MODEL to connect a model; otherwise answers are assembled from the dashboard insights."}

    @app.post("/api/v1/assistant/query", dependencies=protected, tags=["assistant"])
    def assistant_query(body: AssistantQuery):
        """Answer a question (or write the morning briefing) using only the dashboard's own figures. Read-only."""
        if body.mode == "question" and not body.question.strip():
            raise HTTPException(422, "question is required unless mode is 'briefing'")
        comp = composite(body.horizon_months, body.as_of, 20, body.shocks or None, body.scenario, body.forecast_id, body.overrides)
        return assistant.answer(body.question, build_context(comp), briefing=body.mode == "briefing")

    # ------------------------------------------------------------------ frontend
    fe = Path(frontend_dir) if frontend_dir else (Path(os.environ["FRONTEND_DIST"]) if os.environ.get("FRONTEND_DIST") else None)
    if fe and (fe / "index.html").exists():
        if (fe / "assets").exists():
            app.mount("/assets", StaticFiles(directory=fe / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index():
            # Revalidate the page on every load: asset names change with each build, so a cached page would keep
            # loading the previous build's styles and scripts.
            return FileResponse(fe / "index.html", headers={"Cache-Control": "no-cache"})
    return app


def main():
    p = argparse.ArgumentParser(description="Treasury decision dashboard API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--allow-origin", action="append", default=[])
    a = p.parse_args()
    token = os.environ.get("DASHBOARD_API_TOKEN")
    if a.host not in {"127.0.0.1", "::1", "localhost"} and not token:
        p.error("Set DASHBOARD_API_TOKEN before listening on a non-loopback address")
    import uvicorn
    uvicorn.run(create_app(token=token, allowed_origins=a.allow_origin), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
