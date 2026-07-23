"""Repository inventory + lifecycle: register (clone+index), reindex, delete.

Registration is where user-supplied input meets the filesystem and network, so
the validation is deliberately strict: https/ssh git URLs only (no file:// or
git://), optional host allowlist, clones forced under EG_CLONE_DIR, depth
capped, and local-path registration gated by EG_ALLOW_LOCAL_PATHS + admin.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from entrygraph.db import models
from entrygraph.db.migrations import is_stale
from entrygraph.fs.remote import is_git_url
from entrygraph.server.auth.deps import CurrentPrincipal, current_principal, require_role
from entrygraph.server.jobs.handlers import delete_repo_data
from entrygraph.server.jobs.runner import enqueue_job
from entrygraph.server.models import RepoSource
from entrygraph.server.routes.serializers import repo_name

audit = logging.getLogger("entrygraph.server.audit")

router = APIRouter(dependencies=[Depends(current_principal)])

_MAX_DEPTH = 50
_SCP_STYLE = re.compile(r"^[\w.-]+@[\w.-]+:")


class RegisterRepo(BaseModel):
    source: str = Field(min_length=1, max_length=2000)
    ref: str | None = Field(default=None, max_length=255)
    depth: int = Field(default=1, ge=0, le=_MAX_DEPTH)
    include_tests: bool = False


class ReindexRepo(BaseModel):
    full: bool = False
    paranoid: bool = False
    include_tests: bool = False
    ref: str | None = Field(default=None, max_length=255)


def _validate_source(request: Request, source: str) -> None:
    config = request.app.state.config
    if is_git_url(source):
        if "://" in source:
            scheme = urlsplit(source).scheme.lower()
            if scheme not in ("https", "ssh"):
                raise HTTPException(
                    status_code=422,
                    detail=f"unsupported URL scheme {scheme!r}: "
                    "use https:// or ssh (git@host:path)",
                )
            host = urlsplit(source).hostname or ""
        elif _SCP_STYLE.match(source):
            host = source.partition("@")[2].partition(":")[0]
        else:
            raise HTTPException(status_code=422, detail="unrecognized git URL")
        if config.allowed_git_hosts and host not in config.allowed_git_hosts:
            raise HTTPException(
                status_code=422,
                detail=f"git host {host!r} is not in EG_ALLOWED_GIT_HOSTS",
            )
        return
    # local path
    if not config.local_paths_allowed:
        raise HTTPException(
            status_code=422,
            detail="registering local paths is disabled (EG_ALLOW_LOCAL_PATHS)",
        )
    path = Path(source)
    if not path.is_absolute():
        raise HTTPException(status_code=422, detail="local path must be absolute")
    if not path.resolve().is_dir():
        raise HTTPException(status_code=422, detail=f"no directory at {source}")


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
        # analyzer_version behind current: rows are valid and still served, but a
        # background/manual re-scan will refresh them.
        "stale": is_stale(r.analyzer_version),
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


@router.post("/repos", status_code=202, dependencies=[Depends(require_role("admin"))])
def register_repo(
    request: Request, body: RegisterRepo, principal: CurrentPrincipal
) -> dict[str, Any]:
    """Register a repo (git URL or local path) and index it — returns a job id."""
    _validate_source(request, body.source)
    params = {
        "source": body.source,
        "ref": body.ref,
        "depth": body.depth,
        "include_tests": body.include_tests,
        "incremental": False,
        "created_by": principal.name,
    }
    job_id = enqueue_job(
        request.app.state.app_session_factory,
        job_type="index",
        params=params,
        created_by=principal.name,
    )
    request.app.state.job_runner.nudge()
    audit.info("repo register source=%s job=%s by=%s", body.source, job_id, principal.name)
    return {"job_id": job_id}


@router.post(
    "/repos/{repo_id}/index", status_code=202, dependencies=[Depends(require_role("admin"))]
)
def reindex_repo(
    request: Request, repo_id: int, body: ReindexRepo, principal: CurrentPrincipal
) -> dict[str, Any]:
    """Reindex a known repo. Cloned repos fetch/reset from their recorded origin
    (RepoSource); local repos re-walk in place. Incremental unless `full`."""
    with request.app.state.graph_session_factory() as session:
        repo = session.get(models.Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="repo not found")
        root = repo.root_path
    source_row = _sources_by_root(request).get(root)
    source = source_row.url if source_row and source_row.url else root
    if not is_git_url(source) and not Path(source).is_dir():
        raise HTTPException(status_code=422, detail=f"repo root {source} no longer exists")
    params = {
        "source": source,
        "ref": body.ref or (source_row.ref if source_row else None),
        "depth": source_row.depth if source_row else 1,
        "include_tests": body.include_tests,
        "paranoid": body.paranoid,
        "incremental": not body.full,
        "created_by": principal.name,
    }
    job_id = enqueue_job(
        request.app.state.app_session_factory,
        job_type="index",
        params=params,
        created_by=principal.name,
    )
    request.app.state.job_runner.nudge()
    audit.info("repo reindex id=%d job=%s by=%s", repo_id, job_id, principal.name)
    return {"job_id": job_id}


@router.delete("/repos/{repo_id}", dependencies=[Depends(require_role("admin"))])
def delete_repo(request: Request, repo_id: int, principal: CurrentPrincipal) -> dict[str, Any]:
    root = delete_repo_data(
        request.app.state.config, request.app.state.app_session_factory, repo_id
    )
    if root is None:
        raise HTTPException(status_code=404, detail="repo not found")
    audit.info("repo delete id=%d root=%s by=%s", repo_id, root, principal.name)
    return {"deleted": root}
