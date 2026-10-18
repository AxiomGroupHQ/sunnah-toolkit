"""FastAPI application factory. Serves REST under /v1 and MCP under /mcp.

Rate limiting is off by default (self-hosters can do it at their edge).
The public instance turns on the in-memory per-client limits in
api/ratelimit.py via RATE_LIMIT_SEARCH_PER_MIN / RATE_LIMIT_PER_MIN.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from ..core import querylog
from ..core import reranker as _reranker_mod
from ..mcp.server import mcp
from . import accounts, auth, ratelimit
from .routes import router
from .ui import index as _ui_index


_log = logging.getLogger(__name__)


# ME-004: track the warm-load outcome so /healthz can surface degraded state
# instead of returning a flat `{"ok": True}` while every search silently
# falls back to the heuristic.
_warm_state: dict[str, str | bool] = {
    "reranker": "unknown",
    "reranker_status": "unknown",
}


def _warm_reranker() -> None:
    """Eagerly load + run one dummy pair so the first user request doesn't
    pay the model-load and JIT-warmup cost.

    Records the outcome in `_warm_state` so /healthz can surface it. If the
    operator sets RERANKER_REQUIRED=1 a warm-load failure is fatal (the
    lifespan re-raises, uvicorn exits non-zero) — this is the fail-fast
    path for deployments where degraded ranking is unacceptable.
    """
    if not _reranker_mod.reranker_enabled():
        _log.info("reranker disabled; skipping warm load")
        _warm_state["reranker"] = "none"
        _warm_state["reranker_status"] = "disabled"
        return
    name = _reranker_mod.default_reranker_name()
    _warm_state["reranker"] = name
    try:
        r = _reranker_mod.get_reranker(name)
        r.score("warmup", ["A short warmup document."])
        _log.info("reranker %s warmed", name)
        _warm_state["reranker_status"] = "ok"
    except Exception as e:
        _log.warning("reranker %s warm load failed: %s", name, e)
        _warm_state["reranker_status"] = f"fell_back: {type(e).__name__}"
        if os.environ.get("RERANKER_REQUIRED", "").strip() in ("1", "true", "yes"):
            # Fail-fast deployment mode: don't pretend healthy if the
            # configured reranker won't load.
            raise


def _cors_origins() -> list[str]:
    """ME-008: read allowed origins from CORS_ALLOW_ORIGINS (comma-separated).

    Defaults to ["*"] for unauthenticated demo usage. Once a deployment
    enables auth-in-browser, the operator should set CORS_ALLOW_ORIGINS to
    the actual UI origin(s) so combining `*` with Authorization headers
    cannot leak bearer tokens to malicious pages.
    """
    raw = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app(keys_file: str | Path | None = None) -> FastAPI:
    auth.load_keys(keys_file)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _warm_reranker()
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(
        title="Sunnah Semantic Search",
        description="Hadith lookup over REST (/v1/...) and MCP (/mcp).",
        version="0.1.0",
        lifespan=lifespan,
    )

    origins = _cors_origins()
    # ME-008: when CORS_ALLOW_ORIGINS is "*" we strip Authorization from
    # allow_headers — a star-origin policy + Authorization is a token-leak
    # vector once the UI ships auth. Operators who want cross-origin auth
    # must set explicit origins.
    allow_headers = ["*"] if origins != ["*"] else ["Content-Type", "Accept"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=allow_headers,
    )

    # www.<domain> -> <domain>, so the site has one canonical address.
    @app.middleware("http")
    async def _strip_www(request: Request, call_next):
        host = request.headers.get("host", "")
        if host.startswith("www."):
            url = request.url.replace(scheme="https", netloc=host[4:])
            return RedirectResponse(str(url), status_code=301)
        return await call_next(request)

    search_limit, general_limit = ratelimit.from_env()

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        limiter = search_limit if ratelimit.is_search(request.method, request.url.path) else general_limit
        if limiter is not None:
            key = ratelimit.client_key(request.headers, request.client.host if request.client else None)
            wait = limiter.hit(key)
            if wait:
                return JSONResponse(
                    {"detail": f"Too many requests. Try again in {wait} seconds."},
                    status_code=429, headers={"Retry-After": str(wait)})
        return await call_next(request)

    # Who is asking, for the query log (core/querylog.py). No IPs or user
    # agents: only the source, Cloudflare's country code, and ids the web UI
    # sends to group one search's calls and one tab's searches.
    @app.middleware("http")
    async def _query_meta(request: Request, call_next):
        h = request.headers

        def clean(v: str | None, n: int = 64) -> str | None:
            return v.strip()[:n] if v and v.strip() else None

        path = request.url.path
        meta = {
            "source": "mcp" if path.startswith("/mcp") else ("web" if h.get("x-client") == "web" else "api"),
            "local": 0 if h.get("cf-ray") else 1,
            "country": clean(h.get("cf-ipcountry"), 8),
            "session": clean(h.get("x-session")),
            "search_id": clean(h.get("x-search-id")),
            "trigger": clean(h.get("x-search-trigger"), 16),
            "ui_mode": clean(h.get("x-ui-mode"), 16),
            "collections": clean(h.get("x-collections"), 2000),
            "skip": h.get("x-warmup") == "1",
        }
        token = querylog.request_meta.set(meta)
        try:
            return await call_next(request)
        finally:
            querylog.request_meta.reset(token)

    @app.get("/healthz")
    def healthz() -> dict:
        # ME-004: include the warm-load outcome so an ops dashboard polling
        # /healthz sees the difference between "fresh reranker" and
        # "running on the heuristic fallback".
        return {
            "ok": True,
            "reranker": _warm_state["reranker"],
            "reranker_status": _warm_state["reranker_status"],
        }

    # Optional sign-in pages and /auth/me (api/accounts.py). Nothing under
    # /auth may be cached: responses carry cookies or say who is signed in.
    @app.middleware("http")
    async def _no_store_auth(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Demo UI at /. Must be registered before the catch-all MCP mount below.
    app.get("/", include_in_schema=False)(_ui_index)

    app.include_router(accounts.build_router())
    app.include_router(router)
    app.mount("/", mcp_app)

    return app
