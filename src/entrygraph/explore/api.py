"""Read-only HTTP API over an entrygraph index for the explorer UI.

Serves the indexed graph — repos, stats, symbols, entrypoints, a symbol's
callers/callees, source->sink paths, and a symbol's call-graph neighborhood —
straight from :class:`entrygraph.api.CodeGraph`. The database is a global
multi-repo store, so every route is scoped by ``repo_id`` and a ``CodeGraph`` is
bound per request. Nothing here writes or executes code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import select

from entrygraph.api import CodeGraph
from entrygraph.db import models
from entrygraph.db.engine import make_engine, make_session_factory
from entrygraph.errors import SymbolNotFoundError


def _repo_name(root_path: str) -> str:
    # sentinel:// synthetic roots and real paths both reduce to a readable name
    return root_path.rstrip("/").split("/")[-1] or root_path


def _symbol_json(s) -> dict[str, Any]:
    return {
        "id": s.id,
        "kind": s.kind,
        "name": s.name,
        "qname": s.qname,
        "file": s.file,
        "line": s.start_line,
        "end_line": s.end_line,
        "signature": s.signature,
        "exported": s.is_exported,
    }


def _entrypoint_json(e) -> dict[str, Any]:
    return {
        "id": e.id,
        "kind": e.kind,
        "framework": e.framework,
        "route": e.route,
        "http_method": e.http_method,
        "handler": _symbol_json(e.symbol) if e.symbol else None,
    }


def _path_json(p) -> dict[str, Any]:
    return {
        "risk": round(p.risk_score, 4) if p.risk_score is not None else None,
        "verified": p.taint_verified,
        "source_category": p.source_category,
        "source_channel": p.source_channel,
        "source_key": p.source_key,
        "may_continue": p.may_continue,
        "hops": [
            {"qname": sym.qname, "name": sym.name, "file": sym.file, "kind": sym.kind}
            for sym in p.symbols
        ],
        "sink_id": p.edges[-1].sink_id if p.edges else None,
    }


def create_app(db_path: str | Path, *, serve_ui: bool = True) -> FastAPI:
    """Build the explorer API over the index at ``db_path``. When a built UI is
    present (``explore/static``) and ``serve_ui`` is set, it is mounted at ``/``."""
    engine = make_engine(db_path)
    session_factory = make_session_factory(engine)
    app = FastAPI(title="entrygraph explorer", version="1")

    def graph(repo_id: int) -> CodeGraph:
        with session_factory() as session:
            exists = session.execute(
                select(models.Repository.id).where(models.Repository.id == repo_id)
            ).scalar()
        if exists is None:
            raise HTTPException(status_code=404, detail="repo not found")
        return CodeGraph(engine, repo_id)

    @app.get("/api/repos")
    def repos() -> dict[str, Any]:
        with session_factory() as session:
            rows = (
                session.execute(select(models.Repository).order_by(models.Repository.root_path))
                .scalars()
                .all()
            )
        return {
            "repos": [
                {
                    "id": r.id,
                    "root_path": r.root_path,
                    "name": _repo_name(r.root_path),
                    "files": r.file_count,
                    "symbols": r.symbol_count,
                }
                for r in rows
            ]
        }

    @app.get("/api/repos/{repo_id}/stats")
    def stats(repo_id: int) -> dict[str, Any]:
        g = graph(repo_id)
        st = g.stats()
        det = g.detect()
        return {
            "stats": {
                "files": st.files,
                "symbols": st.symbols,
                "edges": st.edges,
                "resolved_edges": st.resolved_edges,
                "entrypoints": st.entrypoints,
                "sink_edges": st.sink_edges,
                "source_edges": st.source_edges,
            },
            "languages": [
                {"name": lang.name, "files": lang.file_count, "percent": round(lang.percent, 1)}
                for lang in det.languages
            ],
            "frameworks": [
                {"name": fw.name, "language": fw.language, "confidence": round(fw.confidence, 2)}
                for fw in det.frameworks
            ],
        }

    @app.get("/api/repos/{repo_id}/symbols")
    def symbols(
        repo_id: int,
        q: str | None = None,
        kind: str | None = None,
        file: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        g = graph(repo_id)
        name = f"*{q}*" if q else None
        rows = g.symbols(name=name, kind=kind, file=file, limit=limit, offset=offset)
        return {"symbols": [_symbol_json(s) for s in rows]}

    @app.get("/api/repos/{repo_id}/entrypoints")
    def entrypoints(
        repo_id: int, framework: str | None = None, kind: str | None = None
    ) -> dict[str, Any]:
        g = graph(repo_id)
        rows = g.entrypoints(framework=framework, kind=kind)
        return {"entrypoints": [_entrypoint_json(e) for e in rows]}

    @app.get("/api/repos/{repo_id}/symbol")
    def symbol_detail(repo_id: int, qname: str) -> dict[str, Any]:
        g = graph(repo_id)
        try:
            sym = g.symbol(qname)
        except SymbolNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        callers = g.callers(qname)[:200]
        callees = g.callees(qname)[:200]
        return {
            "symbol": _symbol_json(sym),
            "callers": [_symbol_json(s) for s in callers],
            "callees": [_symbol_json(s) for s in callees],
        }

    @app.get("/api/repos/{repo_id}/graph")
    def neighborhood(repo_id: int, qname: str) -> dict[str, Any]:
        """The call-graph neighborhood of ``qname``: nodes (it + its direct callers
        and callees) and directed edges, for the graph view."""
        g = graph(repo_id)
        try:
            center = g.symbol(qname)
        except SymbolNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        callers = g.callers(qname)[:60]
        callees = g.callees(qname)[:60]
        nodes = {center.qname: {**_symbol_json(center), "role": "center"}}
        edges = []
        for c in callers:
            nodes.setdefault(c.qname, {**_symbol_json(c), "role": "caller"})
            edges.append({"from": c.qname, "to": center.qname})
        for c in callees:
            nodes.setdefault(c.qname, {**_symbol_json(c), "role": "callee"})
            edges.append({"from": center.qname, "to": c.qname})
        return {"nodes": list(nodes.values()), "edges": edges}

    @app.get("/api/repos/{repo_id}/paths")
    def paths(
        repo_id: int,
        source_category: str | None = "http_input",
        sink_category: str | None = "all",
        source: str | None = None,
        sink: str | None = None,
        include_unresolved: bool = False,
        max_paths: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        g = graph(repo_id)
        rows = g.paths(
            source=source,
            source_category=source_category if not source else None,
            sink=sink,
            sink_category=sink_category if not sink else None,
            include_unresolved=include_unresolved,
            max_paths=max_paths,
        )
        return {"paths": [_path_json(p) for p in rows], "mode": getattr(rows, "mode", None)}

    if serve_ui:
        _mount_ui(app)
    return app


def _mount_ui(app: FastAPI) -> None:
    static_dir = Path(__file__).parent / "static"
    if not (static_dir / "index.html").is_file():
        return
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="explorer")
