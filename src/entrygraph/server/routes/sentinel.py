"""Session-authed read views over the Sentinel findings store.

Mounted only when Sentinel is configured (``SENTINEL_GITHUB_APP_ID``). These
wrap the same :mod:`entrygraph.sentinel.store` functions the token-guarded
Sentinel API uses — no business logic is duplicated, only the HTTP + auth
layer differs so the unified dashboard can browse installations, repos, scans,
and findings with a session cookie instead of the static bearer token.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from entrygraph.sentinel import store as sentinel_store
from entrygraph.server.auth.deps import current_principal

router = APIRouter(dependencies=[Depends(current_principal)])


def _factory(request: Request):
    factory = getattr(request.app.state, "sentinel_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=404, detail="Sentinel is not configured")
    return factory


@router.get("/sentinel/installations")
def installations(request: Request) -> dict[str, Any]:
    with _factory(request)() as session:
        rows = sentinel_store.list_installations(session)
        return {
            "installations": [
                {
                    "id": i.id,
                    "account_login": i.account_login,
                    "suspended": i.suspended,
                    "repo_count": sentinel_store.repo_count(session, i.id),
                }
                for i in rows
            ]
        }


@router.get("/sentinel/installations/{installation_id}/repos")
def installation_repos(request: Request, installation_id: int) -> dict[str, Any]:
    with _factory(request)() as session:
        if sentinel_store.get_installation(session, installation_id) is None:
            raise HTTPException(status_code=404, detail="installation not found")
        rows = sentinel_store.installation_repos(session, installation_id)
        return {"repos": [{"repo_id": r.repo_id, "full_name": r.full_name} for r in rows]}


@router.get("/sentinel/repos/{repo_id}/scans")
def scans(request: Request, repo_id: int, limit: int = 50) -> dict[str, Any]:
    with _factory(request)() as session:
        rows = sentinel_store.list_scans(session, repo_id, limit=max(1, min(limit, 200)))
        return {
            "scans": [
                {
                    "id": s.id,
                    "status": s.status,
                    "pr_number": s.pr_number,
                    "head_sha": s.head_sha,
                    "counts": {
                        "new": s.new_count,
                        "known": s.known_count,
                        "fixed": s.fixed_count,
                        "suppressed": s.suppressed_count,
                    },
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in rows
            ]
        }


@router.get("/sentinel/scans/{scan_id}/findings")
def scan_findings(request: Request, scan_id: int, status: str | None = None) -> dict[str, Any]:
    with _factory(request)() as session:
        rows = sentinel_store.scan_findings(session, scan_id, status=status)
        return {
            "findings": [
                {
                    "id": f.id,
                    "fingerprint": f.fingerprint,
                    "status": f.status,
                    "source_category": f.source_category,
                    "sink_id": f.sink_id,
                    "risk": f.risk,
                    "path": json.loads(f.path_json) if f.path_json else None,
                }
                for f in rows
            ]
        }
