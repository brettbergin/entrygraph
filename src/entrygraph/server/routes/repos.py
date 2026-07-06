"""Repository inventory (reads). Registration/indexing/deletion land with the
jobs subsystem (phase 2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from entrygraph.db import models
from entrygraph.server.auth.deps import current_principal
from entrygraph.server.models import RepoSource
from entrygraph.server.routes.serializers import repo_name

router = APIRouter(dependencies=[Depends(current_principal)])


def _sources_by_root(request: Request) -> dict[str, RepoSource]:
    with request.app.state.app_session_factory() as session:
        rows = session.execute(select(RepoSource)).scalars().all()
    return {r.root_path: r for r in rows}


def _repo_json(r: models.Repository, source: RepoSource | None) -> dict[str, Any]:
    return {
        "id": r.id,
        "root_path": r.root_path,
        "name": repo_name(r.root_path),
        "files": r.file_count,
        "symbols": r.symbol_count,
        "indexed_at": r.indexed_at.isoformat() if r.indexed_at else None,
        "sentinel": r.root_path.startswith("sentinel://"),
        "source": {
            "url": source.url,
            "ref": source.ref,
            "depth": source.depth,
            "include_tests": source.include_tests,
        }
        if source
        else None,
    }


@router.get("/repos")
def repos(request: Request) -> dict[str, Any]:
    with request.app.state.graph_session_factory() as session:
        rows = (
            session.execute(select(models.Repository).order_by(models.Repository.root_path))
            .scalars()
            .all()
        )
    sources = _sources_by_root(request)
    return {"repos": [_repo_json(r, sources.get(r.root_path)) for r in rows]}


@router.get("/repos/{repo_id}")
def repo_detail(request: Request, repo_id: int) -> dict[str, Any]:
    with request.app.state.graph_session_factory() as session:
        row = session.execute(
            select(models.Repository).where(models.Repository.id == repo_id)
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="repo not found")
    sources = _sources_by_root(request)
    return {"repo": _repo_json(row, sources.get(row.root_path))}
