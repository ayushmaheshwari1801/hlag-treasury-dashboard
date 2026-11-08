"""Treasury API: shared market store, v8 news, signal routing and deterministic cash engine."""
import argparse
import threading
import hmac
import os
from datetime import date, datetime, timezone
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

from .assistant import Assistant, build_context, provider_config
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


class MethodologyRequest(StrictModel):
    row_id: Literal["freight", "bunker", "interest", "fx_eur", "fx_gbp", "fx_cny", "fx_cad"]
    run_id: str = Field(min_length=1)
    horizon_months: Literal[3, 6, 12] = 6


class SensitivityRequest(StrictModel):
    row_id: Literal["freight", "bunker"]
    horizon_months: Literal[3, 6, 12] = 6
    shocks: dict[str, float] = Field(default_factory=dict)
    forecast_id: str | None = None
    scenario: dict | None = None
    as_of: date | None = None
    overrides: dict[str, float | bool | str] = Field(default_factory=dict)


class RecommendationAgentRequest(StrictModel):
    """One Treasury recommendation set for the whole situation (recommendation-agent/3). News is the active Event
    pool: event_ids is the ticked set (default: the top Events by score). No date window: news_from / news_to are
    accepted for older clients and ignored."""
    event_ids: list[str] | None = None
    as_of: date | None = None
    horizon_months: Literal[3, 6, 12] = 6
    mode: Literal["auto", "model", "rule_based"] = "auto"
    max_runs: int = Field(default=10, ge=4, le=20)
    news_from: date | None = None
    news_to: date | None = None


class RecommendationDecision(StrictModel):
    decision_id: str = Field(min_length=1)
    decision: Literal["approve", "defer", "reject"]
    reviewer: str = Field(min_length=1, max_length=200)
    comment: str = Field(default="", max_length=2000)


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
        """The scenario the page shows: moved sliders on top of the preset or saved news forecast (observed weekly
        moves when neither is selected), plus per-run overrides. Used by the dashboard and the hedge tool."""
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

    # ------------------------------------------------------------------ row Methodology view (read-only)
    @app.post("/api/v1/engine/methodology", dependencies=protected, tags=["hlag engine"])
    def row_methodology(body: MethodologyRequest):
        """How one card row was built, from the engine: text, formulas with current values, the row's components
        by horizon, assumptions with override status, sources, playbook and limitations. Reads the saved run."""
        from .engine_adapter import page_market as engine_page_market
        from .methodology import methodology
        adapter = spec_only()
        try:
            run = cash_routes.run_store.get(body.run_id)
        except (KeyError, LookupError):
            raise HTTPException(404, 'Unknown run') from None
        if run is None:
            raise HTTPException(404, 'Unknown run')
        try:
            # The view's header reads the engine store (its snapshot and real prints); the page tiles are unchanged.
            return methodology(adapter.shared, run, body.row_id, body.horizon_months,
                               engine_page_market(adapter.shared, date.fromisoformat(run['as_of'])))
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    sensitivity_cache: dict[str, dict] = {}

    @app.post("/api/v1/engine/sensitivity", dependencies=protected, tags=["hlag engine"])
    def row_sensitivity(body: SensitivityRequest):
        """Five real engine runs of the page's current scenario with only this driver's shock set to a fixed move
        (-20%, -10%, 0, +10%, +20%); everything else held. Nothing is written to the engine store. Cached."""
        import os
        from .methodology import SENSITIVITY_STEPS, row_value, sensitivity_cache_key, sensitivity_shock
        adapter = spec_only()
        day = body.as_of or service.clock().astimezone(service.tz).date()
        adjustments = [a for a in intelligence.adjustments() if a['status'] == 'approved' and a['review_date'] > day.isoformat()]
        # The page's market (the one the card's run uses), so the bunker step matches the bunker slider.
        _, market, _ = page_scenario(day, body.shocks, body.scenario, body.forecast_id, body.overrides)
        key = sensitivity_cache_key(body.model_dump(mode='json'), day, adjustments, adapter.shared.profile.content_hash,
                                    os.path.getmtime(adapter.store.path))
        if key in sensitivity_cache:
            return {**sensitivity_cache[key], 'cached': True}
        points = []
        try:
            for step in SENSITIVITY_STEPS:
                slider = sensitivity_shock(body.row_id, step, market)
                params, _, _ = page_scenario(day, {**body.shocks, body.row_id: slider}, body.scenario, body.forecast_id,
                                             body.overrides)
                run = adapter.run(day, params, adjustments)
                points.append({'step_pct': step, 'slider_value': slider, **row_value(run, body.row_id, body.horizon_months)})
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from None
        vlsfo = next((r.get('value') for r in market['series'] if r.get('series_id') == 'VLSFO_RTM'), None)
        result = {'row_id': body.row_id, 'as_of': day.isoformat(), 'horizon_months': body.horizon_months,
                  'points': points, 'unit': 'EUR m', 'slider_unit': '%' if body.row_id == 'freight' else 'USD/t',
                  'basis': ('Route rate move on every trade (WCI), in %.' if body.row_id == 'freight' else
                            f'Fuel move of the stated % of VLSFO Rotterdam at T0 (USD {vlsfo:,.0f}/t), applied as the '
                            'same USD/t to every grade and hub, as the bunker slider does.'),
                  'note': 'Each point is a separate engine run of the current scenario with only this shock changed; '
                          'other drivers, overrides and adjustments are held. Nothing is written to the engine store.',
                  'read_only': True}
        if len(sensitivity_cache) >= 32:
            sensitivity_cache.pop(next(iter(sensitivity_cache)))
        sensitivity_cache[key] = result
        return {**result, 'cached': False}

    # ------------------------------------------------------------------ hedge recommendation agent (read-only)
    def recommendation_store():
        # Imported here: the module needs hlag_engine, which the mock cash engine does not.
        from .recommendation_agent import RecommendationSessions
        if not hasattr(app.state, "recommendation_sessions"):
            app.state.recommendation_sessions = RecommendationSessions(intelligence)
        return app.state.recommendation_sessions

    fact_cache: dict = {}
    fact_lock = threading.RLock()
    # Part of the fact pack cache key: changes when the engine store changes (market refresh). Tests may fix it.
    app.state.store_version = lambda adapter: os.path.getmtime(adapter.store.path)
    fact_state = {"building": False, "last_error": None}

    def recommendation_key(day: date, months: int, event_ids: list[str] | None):
        """Active Events, ticked set, saved forecasts and approved adjustments, plus the cache key of the fact pack."""
        import hashlib as _h
        import json as _j
        import os as _os
        from .dashboard_adapter import horizon_cutoff
        from .fact_pack import select_active_events
        from .forecast import ForecastAgent
        adapter = spec_only()
        pool, _ = news.cumulative()
        if not pool or not pool.get("events"):
            raise HTTPException(409, "No news Events are available; collect or import news first")
        events = select_active_events(pool)
        known = {e["event_id"] for e in events["included"] + events["not_included"]}
        ticked = list(event_ids) if event_ids is not None else [e["event_id"] for e in events["included"]]
        unknown = sorted(set(ticked) - known)
        if unknown:
            raise HTTPException(422, "Unknown or unscored Events: " + ", ".join(unknown))
        if not ticked:
            raise HTTPException(422, "Tick at least one Event")
        horizon = min(365, (date.fromisoformat(horizon_cutoff(day, months)) - day).days)
        forecasts = ForecastAgent(intelligence, news).forecasts()
        adjustments = [a for a in intelligence.adjustments() if a['status'] == 'approved' and a['review_date'] > day.isoformat()]
        key = _h.sha256(_j.dumps([day.isoformat(), months, sorted(ticked), pool.get("run_id"),
                                  sorted(f["forecast_id"] for f in forecasts), app.state.store_version(adapter),
                                  adapter.shared.profile.content_hash, adjustments], default=str).encode()).hexdigest()
        return {"adapter": adapter, "pool": pool, "events": events, "ticked": ticked, "horizon": horizon,
                "forecasts": forecasts, "adjustments": adjustments, "key": key}

    def recommendation_inputs(day: date, months: int, event_ids: list[str] | None):
        """The fact pack for one situation, built once and cached (read-only): the combined news forecast (saved model
        forecasts first, rules for the rest), the baseline, situation and trend runs, the situation's mirror for the
        hedge lab, market alerts, the 7-day market moves and the engine playbook."""
        import json as _j
        import os as _os
        import time as _t
        from hlag_engine.company_profile.registers import load_registers
        from .engine_adapter import page_market as engine_page_market
        from .fact_pack import (build_fact_pack, combine_event_forecasts, market_alerts, market_moves_7d,
                                playbook_entries, situation_runs)
        from .recommendation_agent import HedgeLab
        k = recommendation_key(day, months, event_ids)
        with fact_lock:
            if k["key"] in fact_cache:
                return {**fact_cache[k["key"]], "cached": True}
            started = _t.perf_counter()
            adapter, pool, ticked = k["adapter"], k["pool"], k["ticked"]
            chosen = [e for e in pool["events"] if e["event_id"] in set(ticked)]
            try:
                combined = combine_event_forecasts(chosen, pool, k["forecasts"], day, k["horizon"])
            except ValueError as exc:
                raise HTTPException(422, f"The combined news forecast is invalid: {exc}") from None
            _, market, _ = page_scenario(day)
            runs = situation_runs(adapter, day, combined["scenario"], k["adjustments"], market=market)
            mkey = ("moves", day.isoformat(), app.state.store_version(adapter), adapter.shared.profile.content_hash,
                    _j.dumps(k["adjustments"], default=str))
            if mkey not in fact_cache:
                fact_cache[mkey] = market_moves_7d(adapter, day, k["adjustments"])
            mirror_lab = HedgeLab(adapter, day, combined["scenario"], k["adjustments"], max_runs=20)
            mirror_lab.seed(runs["situation_result"])
            _, mirror = mirror_lab._ensure_base()          # the situation's mirror, once, for every session's lab
            pack = build_fact_pack(as_of=day, months=months, runs=runs, market=engine_page_market(adapter.shared, day),
                                   events=k["events"], ticked=ticked, combined=combined, pool=pool,
                                   profile=adapter.shared.profile, registers=load_registers(),
                                   alerts=market_alerts(adapter, day), market_moves=fact_cache[mkey],
                                   playbook=playbook_entries(adapter))
            pack["engine_runs"] = 4  # baseline, situation, trend continues, situation mirror
            meta = {"as_of": day.isoformat(), "built_at": datetime.now(timezone.utc).isoformat(),
                    "build_seconds": round(_t.perf_counter() - started, 1), "ticked": ticked}
            out = {"adapter": adapter, "pack": pack, "combined": combined, "adjustments": k["adjustments"],
                   "events": k["events"], "situation_result": runs["situation_result"], "mirror_result": mirror,
                   "meta": meta}
            if len(fact_cache) > 16:
                fact_cache.pop(next(iter(fact_cache)))
            fact_cache[k["key"]] = out
            return {**out, "cached": False}

    def run_recommendation_session(day: date, months: int, event_ids, mode: str, max_runs: int = 10,
                                   prepared: bool = False) -> dict:
        import time as _t
        from .recommendation_agent import HedgeLab, RecommendationAgent
        started = _t.perf_counter()
        inputs = recommendation_inputs(day, months, event_ids)
        try:
            lab = HedgeLab(inputs["adapter"], day, inputs["combined"]["scenario"], inputs["adjustments"], max_runs=max_runs)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        lab.seed(inputs["situation_result"], inputs["mirror_result"])   # no repeat of the fact pack's runs
        agent = RecommendationAgent(assistant.config, transport=assistant.transport)
        result = agent.run(lab, inputs["pack"], mode)
        extra = {"prepared": True, "prepared_at": datetime.now(timezone.utc).isoformat()} if prepared else {"prepared": False}
        return recommendation_store().save({**result, **extra, "horizon_months": months,
                                            "fact_pack": {**inputs["meta"], "cached": inputs["cached"]},
                                            "session_seconds": round(_t.perf_counter() - started, 1)})

    def prepare_recommendations(months: int = 6, session: bool = True):
        """Build the default fact pack and one complete recommendation session ahead of time (startup, after a news
        refresh): with the model when one is configured, otherwise rule-based. Read-only; never raises."""
        try:
            fact_state["building"] = True
            day = service.clock().astimezone(service.tz).date()
            recommendation_inputs(day, months, None)
            if session:
                fact_state["session_id"] = run_recommendation_session(day, months, None, "auto", prepared=True)["session_id"]
            fact_state["last_error"] = None
        except Exception as exc:  # a missing pool or engine store must not stop the dashboard
            fact_state["last_error"] = f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"
        finally:
            fact_state["building"] = False
    app.state.prepare_recommendations = prepare_recommendations
    app.state.recommendation_fact_state = fact_state

    def prepare_recommendations_later():
        """Start the preparation in the background; returns the thread (None when one is already running)."""
        if cash_engine != 'spec' or fact_state["building"]:
            return None
        fact_state["building"] = True
        thread = threading.Thread(target=prepare_recommendations, daemon=True)
        fact_state["thread"] = thread
        thread.start()
        return thread
    app.state.prepare_recommendations_later = prepare_recommendations_later

    @app.middleware("http")
    async def rebuild_after_news_refresh(request, call_next):
        response = await call_next(request)
        if (request.method == "POST" and response.status_code == 200
                and request.url.path in ("/api/v1/intelligence/collect", "/api/v1/intelligence/import-result")):
            prepare_recommendations_later()
        return response

    def fact_pack_status(day: date, months: int = 6) -> dict:
        try:
            k = recommendation_key(day, months, None)
        except HTTPException as exc:
            return {"status": "unavailable", "reason": exc.detail}
        entry = fact_cache.get(k["key"])
        if entry:
            return {"status": "ready", **entry["meta"]}
        return {"status": "building" if fact_state["building"] else "not_built", "as_of": day.isoformat(),
                "last_error": fact_state["last_error"]}

    @app.get("/api/v1/recommendations/agent/latest", dependencies=protected, tags=["recommendations"])
    def recommendation_latest():
        """The latest recommendation session (a prepared one shows its prepared_at), for the page to show on opening."""
        spec_only()
        store = recommendation_store()
        listed = store.list(1)
        if not listed:
            return {"session": None, "preparing": fact_state["building"], "last_error": fact_state["last_error"]}
        return {"session": store.get(listed[0]["session_id"]), "preparing": fact_state["building"],
                "last_error": fact_state["last_error"]}

    @app.get("/api/v1/recommendations/active-events", dependencies=protected, tags=["recommendations"])
    def recommendation_active_events(as_of: date | None = None, horizon_months: Literal[3, 6, 12] = 6):
        """The active Events (top by score, ticked by default), the rest as "not included", and which have a saved
        model forecast prepared at startup."""
        from .dashboard_adapter import horizon_cutoff
        from .fact_pack import saved_model_forecast, select_active_events
        from .forecast import ForecastAgent
        spec_only()
        day = as_of or service.clock().astimezone(service.tz).date()
        pool, _ = news.cumulative()
        events = select_active_events(pool)
        horizon = min(365, (date.fromisoformat(horizon_cutoff(day, horizon_months)) - day).days)
        forecasts = ForecastAgent(intelligence, news).forecasts()
        for e in events["included"] + events["not_included"]:
            e["forecast"] = "model_saved" if saved_model_forecast(forecasts, e["event_id"], day.isoformat(), horizon,
                                                                  events["pool_run_id"]) else "rule_based"
        return {**events, "as_of": day.isoformat(), "limit": len(events["included"]),
                "more_count": len(events["not_included"]), "fact_pack": fact_pack_status(day, horizon_months)}

    @app.post("/api/v1/recommendations/agent", dependencies=protected, tags=["recommendations"])
    def recommendation_agent(body: RecommendationAgentRequest):
        """One Treasury recommendation set for the whole situation, grounded in a fact pack and checked item by item.
        Hedges are tested by the engine (write=False) against the baseline plus the combined news forecast. Nothing is
        executed."""
        day = body.as_of or service.clock().astimezone(service.tz).date()
        return run_recommendation_session(day, body.horizon_months, body.event_ids, body.mode, body.max_runs)

    @app.post("/api/v1/recommendations/agent/{session_id}/decisions", dependencies=protected, tags=["recommendations"])
    def recommendation_decision(session_id: str, body: RecommendationDecision):
        """Approve / Defer / Reject one decision. Recorded in the audit log only; no scenario, assumption, profile or
        adjustment changes."""
        spec_only()
        store = recommendation_store()
        s = store.get(session_id)
        if s is None:
            raise HTTPException(404, "Unknown recommendation session")
        if body.decision_id not in {d.get("decision_id") for d in s.get("decisions", [])}:
            raise HTTPException(404, "Unknown decision in this session")
        if not body.reviewer.strip():
            raise HTTPException(422, "A reviewer name is required")
        return store.record_decision(session_id, body.decision_id, body.decision, body.reviewer.strip(), body.comment)

    @app.get("/api/v1/recommendations/agent/{session_id}/decisions", dependencies=protected, tags=["recommendations"])
    def recommendation_decisions(session_id: str):
        spec_only()
        store = recommendation_store()
        if store.get(session_id) is None:
            raise HTTPException(404, "Unknown recommendation session")
        return {"decisions": store.decisions(session_id)}

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
            return FileResponse(fe / "index.html")
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
    app = create_app(token=token, allowed_origins=a.allow_origin)
    # Recommendations are prepared in the background (fact pack, then one session: with the model when configured),
    # as run_dashboard.py does; the page shows the latest session and its prepared time.
    app.state.prepare_recommendations_later()
    uvicorn.run(app, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
