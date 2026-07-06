"""Read surface over the graph index — CLI feature parity for queries.

Every route is scoped by ``repo_id`` in the global multi-repo DB and binds a
:class:`entrygraph.api.CodeGraph` per request. Nothing here writes.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from entrygraph.api import CodeGraph
from entrygraph.db import models
from entrygraph.errors import SymbolNotFoundError
from entrygraph.kinds import Confidence
from entrygraph.server.auth.deps import current_principal
from entrygraph.server.routes.serializers import (
    entrypoint_json,
    file_json,
    make_line_reader,
    path_json,
    symbol_json,
)

router = APIRouter(dependencies=[Depends(current_principal)])

_CONFIDENCE_TIERS = {
    "unresolved": int(Confidence.UNRESOLVED),
    "fuzzy": int(Confidence.FUZZY),
    "import": int(Confidence.IMPORT),
    "exact": int(Confidence.EXACT),
}


def get_graph(request: Request, repo_id: int) -> CodeGraph:
    session_factory = request.app.state.graph_session_factory
    with session_factory() as session:
        exists = session.execute(
            select(models.Repository.id).where(models.Repository.id == repo_id)
        ).scalar()
    if exists is None:
        raise HTTPException(status_code=404, detail="repo not found")
    return CodeGraph(request.app.state.graph_engine, repo_id)


Graph = Annotated[CodeGraph, Depends(get_graph)]


@router.get("/repos/{repo_id}/stats")
def stats(g: Graph) -> dict[str, Any]:
    st = g.stats()
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
    }


@router.get("/repos/{repo_id}/detect")
def detect(g: Graph) -> dict[str, Any]:
    det = g.detect()
    return {
        "languages": [
            {
                "name": lang.name,
                "files": lang.file_count,
                "bytes": lang.byte_count,
                "percent": round(lang.percent, 1),
            }
            for lang in det.languages
        ],
        "frameworks": [
            {
                "name": fw.name,
                "language": fw.language,
                "confidence": round(fw.confidence, 2),
                "evidence": list(fw.evidence),
            }
            for fw in det.frameworks
        ],
    }


@router.get("/repos/{repo_id}/files")
def files(g: Graph, language: str | None = None, path: str | None = None) -> dict[str, Any]:
    rows = g.files(language=language, path=path)
    return {"files": [file_json(f) for f in rows]}


@router.get("/repos/{repo_id}/symbols")
def symbols(
    g: Graph,
    q: str | None = None,
    qname: str | None = None,
    kind: str | None = None,
    file: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    name = f"*{q}*" if q else None
    rows = g.symbols(name=name, qname=qname, kind=kind, file=file, limit=limit, offset=offset)
    return {"symbols": [symbol_json(s) for s in rows]}


@router.get("/repos/{repo_id}/entrypoints")
def entrypoints(
    g: Graph,
    framework: str | None = None,
    kind: str | None = None,
    route: str | None = None,
) -> dict[str, Any]:
    rows = g.entrypoints(framework=framework, kind=kind, route=route)
    return {"entrypoints": [entrypoint_json(e) for e in rows]}


@router.get("/repos/{repo_id}/symbol")
def symbol_detail(g: Graph, qname: str) -> dict[str, Any]:
    try:
        sym = g.symbol(qname)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    callers = g.callers(qname)[:200]
    callees = g.callees(qname)[:200]
    return {
        "symbol": symbol_json(sym),
        "callers": [symbol_json(s) for s in callers],
        "callees": [symbol_json(s) for s in callees],
    }


@router.get("/repos/{repo_id}/callers")
def callers(g: Graph, qname: str, depth: int = Query(1, ge=1, le=5)) -> dict[str, Any]:
    try:
        rows = g.callers(qname, depth=depth)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"symbols": [symbol_json(s) for s in rows]}


@router.get("/repos/{repo_id}/callees")
def callees(g: Graph, qname: str, depth: int = Query(1, ge=1, le=5)) -> dict[str, Any]:
    try:
        rows = g.callees(qname, depth=depth)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"symbols": [symbol_json(s) for s in rows]}


@router.get("/repos/{repo_id}/graph")
def neighborhood(g: Graph, qname: str) -> dict[str, Any]:
    """The call-graph neighborhood of ``qname``: nodes (it + its direct callers
    and callees) and directed edges, for the graph view."""
    try:
        center = g.symbol(qname)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    in_syms = g.callers(qname)[:60]
    out_syms = g.callees(qname)[:60]
    nodes = {center.qname: {**symbol_json(center), "role": "center"}}
    edges = []
    for c in in_syms:
        nodes.setdefault(c.qname, {**symbol_json(c), "role": "caller"})
        edges.append({"from": c.qname, "to": center.qname})
    for c in out_syms:
        nodes.setdefault(c.qname, {**symbol_json(c), "role": "callee"})
        edges.append({"from": center.qname, "to": c.qname})
    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/repos/{repo_id}/paths")
def paths(
    g: Graph,
    source_category: str | None = "http_input",
    sink_category: str | None = "all",
    source: str | None = None,
    sink: str | None = None,
    max_depth: int = Query(25, ge=1, le=50),
    max_paths: int = Query(50, ge=1, le=200),
    min_confidence: str | None = Query(None, pattern="^(exact|import|fuzzy|unresolved)$"),
    strict: bool = False,
    include_fuzzy: bool = False,
    include_unresolved: bool = False,
    include_callbacks: bool = False,
    prune_sanitized: bool = False,
    explicit_sources: bool = False,
    confirmed_only: bool = False,
    taint_hops: int = Query(5, ge=0, le=10),
) -> dict[str, Any]:
    """Source→sink reachability with the full CLI ``paths`` flag surface.

    An explicit ``source``/``sink`` qname/glob replaces its category filter, the
    same way the CLI resolves the pair.
    """
    rows = g.paths(
        source=source,
        source_category=source_category if not source else None,
        sink=sink,
        sink_category=sink_category if not sink else None,
        max_depth=max_depth,
        max_paths=max_paths,
        min_confidence=_CONFIDENCE_TIERS[min_confidence] if min_confidence else None,
        strict=strict,
        include_fuzzy=include_fuzzy,
        include_unresolved=include_unresolved,
        include_callbacks=include_callbacks,
        prune_sanitized=prune_sanitized,
        explicit_sources=explicit_sources,
        confirmed_only=confirmed_only,
        taint_hops=taint_hops,
    )
    read_line = make_line_reader(g.repo_root)
    return {
        "paths": [path_json(p, read_line) for p in rows],
        "mode": getattr(rows, "mode", None),
        "truncated": bool(getattr(rows, "truncated", False)),
    }
