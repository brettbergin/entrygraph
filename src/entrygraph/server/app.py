"""Unified server ASGI app.

Composes the read API, repo inventory, auth bootstrap, and (when built) the
SPA into one FastAPI app. The graph DB is the rebuildable index; the app DB
holds durable state. ``create_app`` takes injectable pieces so tests run
against tmp databases; ``build_from_env`` assembles production wiring.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from entrygraph.db.engine import make_engine, make_session_factory
from entrygraph.db.meta import create_schema, stored_schema_version
from entrygraph.server.appdb import ensure_app_schema, make_app_engine, make_app_session_factory
from entrygraph.server.config import ServerConfig
from entrygraph.server.routes import graph as graph_routes
from entrygraph.server.routes import meta as meta_routes
from entrygraph.server.routes import repos as repos_routes

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

    app = FastAPI(title="entrygraph", version="1")
    app.state.config = config
    app.state.graph_engine = graph_engine
    app.state.graph_session_factory = make_session_factory(graph_engine)
    app.state.app_engine = app_engine
    app.state.app_session_factory = make_app_session_factory(app_engine)
    app.state.sentinel_enabled = False

    if config.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    api_prefix = "/api/v1"
    app.include_router(meta_routes.router, prefix=api_prefix)
    app.include_router(repos_routes.router, prefix=api_prefix)
    app.include_router(graph_routes.router, prefix=api_prefix)

    if serve_ui:
        _mount_spa(app, _STATIC_DIR)
    return app


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
