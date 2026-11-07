"""HTTP layer. ``create_router`` mounts into the future unified dashboard API; ``create_app`` runs standalone.

There is deliberately no endpoint that trades, pays, borrows or hedges. The only writes are audit records.
"""

import argparse
import hmac
import os
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from .audit import AuditStore
from .compound import CompoundRequest
from .datasources import KINDS
from .engines.common import ScenarioRequest
from .models import MODULES, StrictModel, ValidationProblem
from .service import SensitivityService

Module = Annotated[str, "one of fx | interest | bunker | freight"]


class ReviewRequest(StrictModel):
    request: ScenarioRequest = Field(default_factory=ScenarioRequest)
    reviewer: str = Field(min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=4000)


class SourceBody(StrictModel):
    actor: str = Field(min_length=1, max_length=120)
    data: dict


class ClearBody(StrictModel):
    actor: str = Field(min_length=1, max_length=120)


class DashboardBody(StrictModel):
    horizon_months: int | None = None
    shocks: dict[str, float] = Field(default_factory=dict)


class CompoundBody(StrictModel):
    compound: CompoundRequest = Field(default_factory=CompoundRequest)
    risk: ScenarioRequest = Field(default_factory=ScenarioRequest)   # limit / confirmed triggers for the combined status


def create_router(service: SensitivityService, dependencies: list | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/sensitivity", dependencies=dependencies or [], tags=["sensitivity"])

    def module_or_404(module: str) -> str:
        if module not in MODULES:
            raise HTTPException(404, f"Unknown module '{module}'. Choose one of: {', '.join(MODULES)}")
        return module

    @router.get("/overview")
    def overview(as_of: date | None = None):
        return {"as_of": service.context(as_of).as_of, "cards": service.overview(as_of)}

    @router.get("/dashboard")
    def dashboard(horizon_months: Annotated[int | None, Query()] = None, as_of: date | None = None):
        return service.dashboard(horizon_months, as_of)

    @router.post("/dashboard")
    def dashboard_whatif(body: DashboardBody, as_of: date | None = None):
        """What-if: same composite with chosen shocks (slider id -> shock). Nothing is stored."""
        return service.dashboard(body.horizon_months, as_of, body.shocks)

    @router.get("/data-sources")
    def data_sources():
        return {"sources": service.sources.status(), "kinds": list(KINDS),
                "note": "Each kind is a demo default until replaced. PUT validates, audits and persists; DELETE restores the demo default."}

    def kind_or_404(kind: str) -> str:
        if kind not in KINDS:
            raise HTTPException(404, f"Unknown data kind '{kind}'. Choose one of: {', '.join(KINDS)}")
        return kind

    @router.get("/data-sources/{kind}")
    def get_source(kind: str):
        e = service.sources.get(kind_or_404(kind))
        if e is None:
            return {"kind": kind, "source": "demo", "data": None}
        return {"kind": kind, "source": "connected", **e}

    @router.put("/data-sources/{kind}")
    def put_source(kind: str, body: SourceBody):
        return {"kind": kind, "source": "connected", **service.set_source(kind_or_404(kind), body.data, body.actor)}

    @router.delete("/data-sources/{kind}")
    def delete_source(kind: str, actor: Annotated[str, Query(min_length=1, max_length=120)]):
        return {"kind": kind, "source": "demo", "removed": service.clear_source(kind_or_404(kind), actor)}

    @router.get("/modules/{module}/defaults")
    def defaults(module: str, as_of: date | None = None):
        return service.input_controls(module_or_404(module))

    @router.post("/modules/{module}/scenario")
    def scenario(module: str, body: ScenarioRequest | None = None, as_of: date | None = None):
        result = service.compute(module_or_404(module), body or ScenarioRequest(), as_of)
        return {"result": result, "explanation": service.explanation(result)}

    @router.post("/modules/{module}/reset")
    def reset(module: str, actor: str | None = None, as_of: date | None = None):
        module_or_404(module)
        result = service.compute(module, ScenarioRequest(), as_of)
        service.audit.append("reset", module=module, actor=actor, details={"defaults_version": result.defaults_version})
        return {"result": result, "explanation": service.explanation(result)}

    @router.get("/modules/{module}/history")
    def history(module: str, range: Literal["1M", "3M", "6M", "1Y", "All"] = "All", as_of: date | None = None,
                benchmark: str | None = None, currency: str | None = None):
        return service.history(module_or_404(module), range, as_of, benchmark, currency)

    @router.get("/modules/{module}/news")
    def news(module: str, limit: Annotated[int, Query(ge=1, le=50)] = 10):
        return service.news(module_or_404(module), limit)

    @router.post("/compound")
    def compound(body: CompoundBody | None = None, as_of: date | None = None):
        body = body or CompoundBody()
        result, parts = service.compound(body.compound, as_of, body.risk)
        return {"compound": result, "components": parts}

    @router.post("/modules/{module}/reviews", status_code=201)
    def review(module: str, body: ReviewRequest, as_of: date | None = None):
        return service.review(module_or_404(module), body.request, reviewer=body.reviewer, comment=body.comment, as_of=as_of)

    @router.get("/audit")
    def audit(module: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100):
        return {"entries": service.audit.entries(module=module, limit=limit)}

    @router.get("/audit/verify")
    def verify():
        ok, bad = service.audit.verify_chain()
        return {"intact": ok, "first_bad_seq": bad}

    @router.get("/snapshots/{snapshot_id}")
    def snapshot(snapshot_id: str):
        row = service.audit.snapshot(snapshot_id)
        if row is None:
            raise HTTPException(404, "Unknown snapshot")
        return row

    return router


def create_app(service: SensitivityService | None = None, *, token: str | None = None, allowed_origins=()) -> FastAPI:
    if "*" in allowed_origins:
        raise ValueError("Configure explicit frontend origins, not a wildcard")
    service = service or SensitivityService(
        audit=AuditStore(os.environ.get("SENSITIVITY_AUDIT_DB", "data/sensitivity_audit.sqlite")),
        news_dir=Path(os.environ["SENSITIVITY_NEWS_DIR"]) if os.environ.get("SENSITIVITY_NEWS_DIR") else None)
    app = FastAPI(title="Treasury sensitivity API", version="0.1.0")
    app.state.service = service
    if allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins), allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type"])
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if token and (credentials is None or not hmac.compare_digest(credentials.credentials, token)):
            raise HTTPException(401, "A valid bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(ValidationProblem)
    async def problem(_, exc: ValidationProblem):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/healthz")
    def health():
        return {"status": "ok", "formula_version": service.defaults.formula_version,
                "defaults_version": service.defaults.defaults_version, "threshold_version": service.thresholds.version}

    @app.get("/", include_in_schema=False)
    def demo_page():
        """Single-file demo UI; it only calls the API below."""
        return FileResponse(Path(__file__).with_name("static") / "index.html")

    app.include_router(create_router(service, [Depends(authorize)]))
    return app


def main():
    parser = argparse.ArgumentParser(description="Serve the Treasury sensitivity API (read-only apart from audit records)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--allow-origin", action="append", default=[])
    args = parser.parse_args()
    token = os.environ.get("SENSITIVITY_API_TOKEN")
    if args.host not in {"127.0.0.1", "::1", "localhost"} and not token:
        parser.error("Set SENSITIVITY_API_TOKEN before listening on a non-loopback address")
    import uvicorn
    uvicorn.run(create_app(token=token, allowed_origins=args.allow_origin), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
