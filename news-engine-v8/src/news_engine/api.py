"""A synchronous JSON interface for the parent Treasury application."""

from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from .clients import ExaClient, LLMClient
from .config import load_credentials
from .models import ArticleInput, RunResult, StrictModel
from .pipeline import run_pipeline
from .storage import BusyError, Store


class RunRequest(StrictModel):
    geographic_scopes: list[str] | None = Field(default=None, min_length=1, max_length=30)
    articles: list[ArticleInput] | None = None
    workers: int = Field(default=4, strict=True, ge=1, le=8)
    max_queries: int = Field(default=7, strict=True, ge=1, le=12)
    max_articles: int = Field(default=10, strict=True, ge=1)
    analysis_batch_size: int = Field(default=3, strict=True, ge=1, le=4)
    analysis_workers: int = Field(default=2, strict=True, ge=1, le=8)


def create_app(*, database=Path("data/news-mvp-v8-issue-identity-v2.sqlite"), env_file=Path(".env"),
               token=None, allowed_origins=(), llm=None, exa=None):
    token = token or os.environ.get("NEWS_API_TOKEN")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("NEWS_API_TOKEN or an explicit API token is required")
    if "*" in allowed_origins:
        raise ValueError("CORS requires explicit frontend origins")
    clients = {"llm": llm, "exa": exa}
    owned = []
    run_lock = Lock()

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            for client in owned:
                client.close()

    app = FastAPI(title="Treasury News MVP", version="8.0", lifespan=lifespan)
    if allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), token.encode()):
            raise HTTPException(401, "A valid service bearer token is required",
                                headers={"WWW-Authenticate": "Bearer"})

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/api/news/runs", response_model=RunResult, dependencies=[Depends(authorize)])
    def run(request: RunRequest):
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "News pipeline is busy")
        try:
            if clients["llm"] is None or (request.articles is None and clients["exa"] is None):
                try:
                    credentials = load_credentials(env_file, require_exa=request.articles is None)
                    if clients["llm"] is None:
                        clients["llm"] = LLMClient(credentials.llm_api_key, credentials.llm_model, credentials.llm_base_url)
                        owned.append(clients["llm"])
                    if request.articles is None and clients["exa"] is None:
                        clients["exa"] = ExaClient(credentials.exa_api_key)
                        owned.append(clients["exa"])
                except ValueError:
                    raise HTTPException(503, "News provider configuration is unavailable") from None
            try:
                with Store(database) as store:
                    return run_pipeline(store, clients["llm"], articles=request.articles,
                                        exa=clients["exa"], workers=request.workers, max_queries=request.max_queries, geographic_scopes=request.geographic_scopes,
                                        analysis_batch_size=request.analysis_batch_size,analysis_workers=request.analysis_workers, max_articles=request.max_articles)
            except BusyError:
                raise HTTPException(409, "News pipeline is busy") from None
            except ValueError:
                raise HTTPException(409, "Database or classification policy is incompatible; use a fresh MVP database") from None
            except RuntimeError:
                raise HTTPException(503, "News execution is unavailable") from None
        finally:
            run_lock.release()

    @app.get("/api/news/runs/{run_id}", response_model=RunResult, dependencies=[Depends(authorize)])
    def result(run_id: str):
        try:
            with Store(database) as store:
                return store.get_run(run_id)
        except KeyError:
            raise HTTPException(404, "Unknown news run") from None
        except BusyError:
            raise HTTPException(409, "News run is still executing") from None
        except ValueError:
            raise HTTPException(503, "News database is unavailable") from None

    return app

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=8000)
