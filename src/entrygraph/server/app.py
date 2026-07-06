"""Unified server ASGI app.

Composes the read API, repo inventory, auth bootstrap, and (when built) the
SPA into one FastAPI app. The graph DB is the rebuildable index; the app DB
holds durable state. ``create_app`` takes injectable pieces so tests run
against tmp databases; ``build_from_env`` assembles production wiring.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from entrygraph.db.engine import make_engine, make_session_factory
from entrygraph.db.meta import create_schema, stored_schema_version
from entrygraph.server.appdb import (
    ensure_app_schema,
    get_or_create_secret,
    make_app_engine,
    make_app_session_factory,
)
from entrygraph.server.auth import oidc as auth_routes
from entrygraph.server.auth.csrf import OriginCheckMiddleware
from entrygraph.server.config import ServerConfig, origin_of
from entrygraph.server.jobs.runner import JobRunner
from entrygraph.server.routes import gate as gate_routes
from entrygraph.server.routes import graph as graph_routes
from entrygraph.server.routes import jobs as jobs_routes
from entrygraph.server.routes import keys as keys_routes
from entrygraph.server.routes import meta as meta_routes
from entrygraph.server.routes import repos as repos_routes
from entrygraph.server.routes import sentinel as sentinel_routes

# where the webapp build lands (webapp/vite.config.ts outDir)
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(config: ServerConfig, *, serve_ui: bool = True) -> FastAPI:
    config.check_bind_safety()

    graph_engine = make_engine(config.db_path)
    if stored_schema_version(graph_engine) is None:
        # an empty/new graph DB is fine — the UI shows the first-run experience;
        # a mismatched one raises per-request via CodeGraph instead of at boot
        create_schema(graph_engine)
    app_engine = make_app_engine(config.app_db_url)
    ensure_app_schema(app_engine)

    app_session_factory = make_app_session_factory(app_engine)
    runner = JobRunner(config, app_session_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        loop_task = asyncio.create_task(runner.run())
        try:
            yield
        finally:
            await runner.stop()
            loop_task.cancel()

    app = FastAPI(title="entrygraph", version="1", lifespan=lifespan)
    app.state.config = config
    app.state.graph_engine = graph_engine
    app.state.graph_session_factory = make_session_factory(graph_engine)
    app.state.app_engine = app_engine
    app.state.app_session_factory = app_session_factory
    app.state.job_runner = runner
    app.state.oauth = auth_routes.make_oauth(config) if config.auth_mode == "oidc" else None
    app.state.sentinel_session_factory = _maybe_sentinel_store()
    app.state.sentinel_enabled = app.state.sentinel_session_factory is not None

    if config.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Origin/Referer check on cookie-authed mutations (bearer requests exempt);
    # allowed origins are the configured external URL + any CORS origins.
    app.add_middleware(
        OriginCheckMiddleware,
        allowed_origins=frozenset(
            {origin_of(config.base_url).lower(), *(o.lower() for o in config.cors_origins)}
        ),
    )
    # Signed-cookie session used ONLY for the transient OIDC state/nonce during
    # the login handshake (what Authlib expects); the real session is a DB row.
    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret or get_or_create_secret(app_engine, "session_secret"),
        session_cookie="eg_oidc_state",
        max_age=600,
        same_site="lax",
        https_only=config.secure_cookies,
    )

    app.include_router(auth_routes.router)
    api_prefix = "/api/v1"
    app.include_router(meta_routes.router, prefix=api_prefix)
    app.include_router(repos_routes.router, prefix=api_prefix)
    app.include_router(jobs_routes.router, prefix=api_prefix)
    app.include_router(gate_routes.router, prefix=api_prefix)
    app.include_router(keys_routes.router, prefix=api_prefix)
    if app.state.sentinel_enabled:
        app.include_router(sentinel_routes.router, prefix=api_prefix)
    app.include_router(graph_routes.router, prefix=api_prefix)

    if serve_ui:
        _mount_spa(app, _STATIC_DIR)
    return app


def _maybe_sentinel_store():
    """A Sentinel findings-store session factory when Sentinel is configured
    (``SENTINEL_GITHUB_APP_ID`` present), else None. Failures to build it (e.g.
    the sentinel extra isn't installed) degrade to "not configured" rather than
    breaking the whole server."""
    import os

    if not os.environ.get("SENTINEL_GITHUB_APP_ID"):
        return None
    try:
        from entrygraph.sentinel.config import SentinelConfig
        from entrygraph.sentinel.store import init_store, make_store_engine

        config = SentinelConfig.from_env()
        return init_store(make_store_engine(config.database_url))
    except Exception:  # pragma: no cover - optional integration
        return None


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA with history fallback: real files win, unknown
    non-API paths get ``index.html`` so React Router deep links work. Absent a
    build, the mount is skipped and the API runs alone."""
    index = static_dir / "index.html"
    if not index.is_file():
        return
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="spa-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(request: Request, path: str):
        candidate = (static_dir / path).resolve()
        if path and candidate.is_relative_to(static_dir.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def build_from_env() -> FastAPI:  # pragma: no cover - production wiring
    return create_app(ServerConfig.from_env())
