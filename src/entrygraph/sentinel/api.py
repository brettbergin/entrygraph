"""Sentinel REST API (#126, milestone 4).

Read/write access to a repo's scans, findings, suppressions, and gate policy —
every route scoped by ``installation_id`` + ``owner/repo`` in the path, so one
installation can never read or mutate another's data. Guarded by a single bearer
token (``SENTINEL_API_TOKEN``); with no token configured the API fails closed
(503) rather than serving unauthenticated.

The heavy lifting is in :mod:`entrygraph.sentinel.store`; this module is just the
HTTP surface, so it is exercised with FastAPI's in-process ``TestClient`` on
SQLite.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from entrygraph.gate import store as gate_store
from entrygraph.sentinel import store
from entrygraph.sentinel.config import SentinelConfig


class SuppressionIn(BaseModel):
    fingerprint: str
    reason: str | None = None
    created_by: str | None = None
    expires_at: datetime | None = None


class PolicyIn(BaseModel):
    risk_threshold: float | None = None
    gated_categories: list[str] | None = None
    mode: str | None = None
    min_confidence: str | None = None


def _scan_json(s) -> dict[str, Any]:
    return {
        "id": s.id,
        "pr_number": s.pr_number,
        "head_sha": s.head_sha,
        "base_sha": s.base_sha,
        "status": s.status,
        "counts": {
            "new": s.new_count,
            "known": s.known_count,
            "fixed": s.fixed_count,
            "suppressed": s.suppressed_count,
        },
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _finding_json(f) -> dict[str, Any]:
    return {
        "fingerprint": f.fingerprint,
        "endpoint_fingerprint": f.endpoint_fingerprint,
        "source_category": f.source_category,
        "sink_id": f.sink_id,
        "risk": f.risk,
        "status": f.status,
    }


def create_api(config: SentinelConfig, session_factory) -> FastAPI:
    """Build the Sentinel REST API. ``session_factory`` yields sessions on the
    findings store (see :func:`entrygraph.sentinel.store.init_store`)."""
    app = FastAPI(title="entrygraph Sentinel API", version="1")

    if config.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["authorization", "content-type"],
        )

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not config.api_token:
            raise HTTPException(status_code=503, detail="API disabled: no token configured")
        expected = f"Bearer {config.api_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def resolve_repo(installation_id: int, owner: str, repo: str) -> int:
        """The installation-scoped repo_id, or 404 — never creates a row."""
        with session_factory() as session:
            repo_id = store.repo_id_for(session, installation_id, f"{owner}/{repo}")
        if repo_id is None:
            raise HTTPException(status_code=404, detail="repo not found for this installation")
        return repo_id

    # ---- discovery: the dashboard walks installations -> repos -> scans ----

    @app.get("/installations", dependencies=[Depends(require_token)])
    def get_installations() -> dict[str, Any]:
        with session_factory() as session:
            insts = store.list_installations(session)
            return {
                "installations": [
                    {
                        "id": i.id,
                        "account_login": i.account_login,
                        "suspended": i.suspended,
                        "repo_count": store.repo_count(session, i.id),
                    }
                    for i in insts
                ]
            }

    @app.get("/installations/{installation_id}/repos", dependencies=[Depends(require_token)])
    def get_repos(installation_id: int) -> dict[str, Any]:
        with session_factory() as session:
            repos = store.installation_repos(session, installation_id)
            out = []
            for r in repos:
                latest = store.latest_scan(session, r.repo_id)
                out.append(
                    {
                        "full_name": r.full_name,
                        "latest_scan": _scan_json(latest) if latest is not None else None,
                    }
                )
            return {"repos": out}

    base = "/installations/{installation_id}/repos/{owner}/{repo}"

    @app.get(base + "/scans", dependencies=[Depends(require_token)])
    def get_scans(
        installation_id: int, owner: str, repo: str, limit: int = Query(50, ge=1, le=500)
    ) -> dict[str, Any]:
        repo_id = resolve_repo(installation_id, owner, repo)
        with session_factory() as session:
            scans = store.list_scans(session, repo_id, limit=limit)
            return {"scans": [_scan_json(s) for s in scans]}

    @app.get(base + "/scans/{scan_id}/findings", dependencies=[Depends(require_token)])
    def get_scan_findings(
        installation_id: int, owner: str, repo: str, scan_id: int, status: str | None = None
    ) -> dict[str, Any]:
        repo_id = resolve_repo(installation_id, owner, repo)
        with session_factory() as session:
            scan = store.get_scan(session, repo_id, scan_id)
            if scan is None:
                raise HTTPException(status_code=404, detail="scan not found for this repo")
            findings = store.scan_findings(session, scan_id, status=status)
            return {"scan_id": scan_id, "findings": [_finding_json(f) for f in findings]}

    @app.get(base + "/findings", dependencies=[Depends(require_token)])
    def get_latest_findings(
        installation_id: int, owner: str, repo: str, status: str | None = None
    ) -> dict[str, Any]:
        repo_id = resolve_repo(installation_id, owner, repo)
        with session_factory() as session:
            scan = store.latest_scan(session, repo_id)
            if scan is None:
                return {"scan_id": None, "findings": []}
            findings = store.scan_findings(session, scan.id, status=status)
            return {"scan_id": scan.id, "findings": [_finding_json(f) for f in findings]}

    @app.get(base + "/suppressions", dependencies=[Depends(require_token)])
    def get_suppressions(installation_id: int, owner: str, repo: str) -> dict[str, Any]:
        repo_id = resolve_repo(installation_id, owner, repo)
        with session_factory() as session:
            sups = store.list_suppressions(session, repo_id)
            return {
                "suppressions": [
                    {
                        "fingerprint": s.fingerprint,
                        "reason": s.reason,
                        "created_by": s.created_by,
                        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                    }
                    for s in sups
                ]
            }

    @app.post(base + "/suppressions", status_code=201, dependencies=[Depends(require_token)])
    def add_suppression(
        installation_id: int, owner: str, repo: str, body: SuppressionIn
    ) -> dict[str, Any]:
        repo_id = resolve_repo(installation_id, owner, repo)
        with session_factory() as session:
            store.add_suppression(
                session,
                repo_id,
                body.fingerprint,
                reason=body.reason,
                created_by=body.created_by,
                expires_at=body.expires_at,
            )
        return {"status": "created", "fingerprint": body.fingerprint}

    @app.delete(base + "/suppressions/{fingerprint}", dependencies=[Depends(require_token)])
    def delete_suppression(
        installation_id: int, owner: str, repo: str, fingerprint: str
    ) -> dict[str, Any]:
        repo_id = resolve_repo(installation_id, owner, repo)
        with session_factory() as session:
            removed = store.remove_suppression(session, repo_id, fingerprint)
        if not removed:
            raise HTTPException(status_code=404, detail="suppression not found")
        return {"status": "deleted", "fingerprint": fingerprint}

    @app.get(base + "/policy", dependencies=[Depends(require_token)])
    def get_policy(installation_id: int, owner: str, repo: str) -> dict[str, Any]:
        repo_id = resolve_repo(installation_id, owner, repo)
        with session_factory() as session:
            policy = gate_store.get_policy(session, repo_id)
        return {
            "risk_threshold": policy.risk_threshold,
            "gated_categories": list(policy.gated_categories) if policy.gated_categories else None,
            "mode": policy.mode,
            "min_confidence": policy.min_confidence,
        }

    @app.put(base + "/policy", dependencies=[Depends(require_token)])
    def put_policy(installation_id: int, owner: str, repo: str, body: PolicyIn) -> dict[str, Any]:
        repo_id = resolve_repo(installation_id, owner, repo)
        with session_factory() as session:
            store.set_policy(
                session,
                repo_id,
                risk_threshold=body.risk_threshold,
                gated_categories=body.gated_categories,
                mode=body.mode,
                min_confidence=body.min_confidence,
            )
        return {"status": "updated"}

    return app
