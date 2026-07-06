"""Gate workflow over the web API: run the gate, cut/inspect baselines, browse
scans and findings, manage the policy and suppressions.

Thin HTTP layer over :mod:`entrygraph.gate.engine`/:mod:`entrygraph.gate.store`
and the shared write helpers in :mod:`entrygraph.sentinel.store` — the exact
code paths the CLI ``gate``/``baseline`` commands and Sentinel scans use, so a
gate run from the UI matches CI verdicts bit for bit.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from entrygraph.api import CodeGraph
from entrygraph.db import models
from entrygraph.gate import store as gate_store
from entrygraph.gate.engine import GateResult, run_gate
from entrygraph.gate.sarif import to_sarif
from entrygraph.gate.store import GateFinding, Policy
from entrygraph.sentinel import store as write_store
from entrygraph.server.auth.deps import CurrentPrincipal, current_principal, require_role

audit = logging.getLogger("entrygraph.server.audit")

router = APIRouter(dependencies=[Depends(current_principal)])

_SARIF_MEDIA = "application/sarif+json"


class RunGate(BaseModel):
    branch: str = Field(default="main", max_length=255)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    warn: bool = False  # report but never fail, like `entrygraph gate --warn`
    head_sha: str | None = Field(default=None, max_length=64)


class CutBaseline(BaseModel):
    branch: str = Field(default="main", max_length=255)
    commit: str | None = Field(default=None, max_length=64)


class PolicyUpdate(BaseModel):
    risk_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    gated_categories: list[str] | None = None
    mode: str | None = Field(default=None, pattern="^(block|warn)$")
    min_confidence: str | None = Field(default=None, pattern="^(exact|import|fuzzy|unresolved)$")


class AddSuppression(BaseModel):
    fingerprint: str = Field(min_length=8, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)
    expires_at: datetime | None = None


def _require_repo(request: Request, repo_id: int) -> None:
    with request.app.state.graph_session_factory() as session:
        if session.get(models.Repository, repo_id) is None:
            raise HTTPException(status_code=404, detail="repo not found")


def _finding_json(f: GateFinding) -> dict[str, Any]:
    return {
        "fingerprint": f.strict,
        "endpoint_fingerprint": f.endpoint,
        "source_category": f.source_category,
        "sink_id": f.sink_id,
        "sink_category": f.sink_category,
        "risk": f.risk,
        "hops": f.hops,
    }


def _result_json(result: GateResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "passed": result.passed,
        "mode": result.mode,
        "has_baseline": result.has_baseline,
        "scan_id": result.scan_id,
        "counts": {
            "new": len(result.new),
            "known": len(result.known),
            "fixed": len(result.fixed),
            "suppressed": len(result.suppressed),
        },
        "new": [_finding_json(f) for f in result.new],
        "gating": [_finding_json(f) for f in result.gating],
        "fixed": [_finding_json(f) for f in result.fixed],
    }


@router.post("/repos/{repo_id}/gate", dependencies=[Depends(require_role("admin"))])
def gate(request: Request, repo_id: int, body: RunGate, principal: CurrentPrincipal):
    """Run the reachability gate against the ``branch`` baseline and persist a
    scan. ``Accept: application/sarif+json`` returns SARIF instead of the verdict."""
    _require_repo(request, repo_id)
    graph = CodeGraph(request.app.state.graph_engine, repo_id)
    with request.app.state.graph_session_factory() as session:
        policy = gate_store.get_policy(session, repo_id)
        if body.threshold is not None or body.warn:
            policy = Policy(
                risk_threshold=body.threshold
                if body.threshold is not None
                else policy.risk_threshold,
                gated_categories=policy.gated_categories,
                mode="warn" if body.warn else policy.mode,
                min_confidence=policy.min_confidence,
            )
        result = run_gate(
            graph,
            session,
            repo_id,
            policy=policy,
            branch=body.branch,
            head_sha=body.head_sha,
            now=datetime.now(UTC),
        )
    audit.info(
        "gate run repo=%d status=%s scan=%s by=%s",
        repo_id,
        result.status,
        result.scan_id,
        principal.name,
    )
    if _SARIF_MEDIA in request.headers.get("accept", ""):
        sarif = to_sarif(result.new + result.known, threshold=policy.risk_threshold)
        return JSONResponse(sarif, media_type=_SARIF_MEDIA)
    return _result_json(result)


# ---------------- scans + findings ----------------


@router.get("/repos/{repo_id}/scans")
def scans(request: Request, repo_id: int, limit: int = 50) -> dict[str, Any]:
    _require_repo(request, repo_id)
    with request.app.state.graph_session_factory() as session:
        rows = write_store.list_scans(session, repo_id, limit=max(1, min(limit, 200)))
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


@router.get("/repos/{repo_id}/scans/{scan_id}/findings")
def scan_findings(
    request: Request, repo_id: int, scan_id: int, status: str | None = None
) -> dict[str, Any]:
    _require_repo(request, repo_id)
    with request.app.state.graph_session_factory() as session:
        scan = session.get(models.ScanRun, scan_id)
        if scan is None or scan.repo_id != repo_id:
            raise HTTPException(status_code=404, detail="scan not found")
        rows = write_store.scan_findings(session, scan_id, status=status)
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


# ---------------- baseline ----------------


@router.post("/repos/{repo_id}/baseline", dependencies=[Depends(require_role("admin"))])
def cut_baseline(
    request: Request, repo_id: int, body: CutBaseline, principal: CurrentPrincipal
) -> dict[str, Any]:
    """Accept the current set of reachable dangerous paths as the ``branch``
    baseline — future gate runs report only paths *introduced* after this."""
    _require_repo(request, repo_id)
    graph = CodeGraph(request.app.state.graph_engine, repo_id)
    with request.app.state.graph_session_factory() as session:
        policy = gate_store.get_policy(session, repo_id)
        findings = gate_store.enumerate_findings(graph, policy)
        count = gate_store.save_baseline(
            session,
            repo_id,
            findings,
            branch=body.branch,
            commit_sha=body.commit,
            now=datetime.now(UTC),
        )
    audit.info(
        "baseline cut repo=%d branch=%s paths=%d by=%s",
        repo_id,
        body.branch,
        count,
        principal.name,
    )
    return {"branch": body.branch, "paths": count}


@router.get("/repos/{repo_id}/baseline")
def show_baseline(request: Request, repo_id: int, branch: str = "main") -> dict[str, Any]:
    _require_repo(request, repo_id)
    with request.app.state.graph_session_factory() as session:
        row = session.execute(
            select(models.Baseline).where(
                models.Baseline.repo_id == repo_id, models.Baseline.branch == branch
            )
        ).scalar_one_or_none()
        if row is None:
            return {"baseline": None}
        findings = gate_store.baseline_findings(session, repo_id, branch)
        return {
            "baseline": {
                "branch": branch,
                "commit_sha": row.commit_sha,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "paths": [_finding_json(f) for f in findings],
            }
        }


# ---------------- policy ----------------


def _policy_json(p: Policy) -> dict[str, Any]:
    return {
        "risk_threshold": p.risk_threshold,
        "gated_categories": list(p.gated_categories) if p.gated_categories else None,
        "mode": p.mode,
        "min_confidence": p.min_confidence,
    }


@router.get("/repos/{repo_id}/policy")
def get_policy(request: Request, repo_id: int) -> dict[str, Any]:
    _require_repo(request, repo_id)
    with request.app.state.graph_session_factory() as session:
        return {"policy": _policy_json(gate_store.get_policy(session, repo_id))}


@router.put("/repos/{repo_id}/policy", dependencies=[Depends(require_role("admin"))])
def put_policy(
    request: Request, repo_id: int, body: PolicyUpdate, principal: CurrentPrincipal
) -> dict[str, Any]:
    _require_repo(request, repo_id)
    with request.app.state.graph_session_factory() as session:
        write_store.set_policy(
            session,
            repo_id,
            risk_threshold=body.risk_threshold,
            gated_categories=body.gated_categories,
            mode=body.mode,
            min_confidence=body.min_confidence,
        )
        updated = gate_store.get_policy(session, repo_id)
    audit.info("policy update repo=%d by=%s", repo_id, principal.name)
    return {"policy": _policy_json(updated)}


# ---------------- suppressions ----------------


@router.get("/repos/{repo_id}/suppressions")
def suppressions(request: Request, repo_id: int) -> dict[str, Any]:
    _require_repo(request, repo_id)
    with request.app.state.graph_session_factory() as session:
        rows = write_store.list_suppressions(session, repo_id)
        return {
            "suppressions": [
                {
                    "fingerprint": s.fingerprint,
                    "reason": s.reason,
                    "created_by": s.created_by,
                    "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                }
                for s in rows
            ]
        }


@router.post(
    "/repos/{repo_id}/suppressions",
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
)
def add_suppression(
    request: Request, repo_id: int, body: AddSuppression, principal: CurrentPrincipal
) -> dict[str, Any]:
    _require_repo(request, repo_id)
    with request.app.state.graph_session_factory() as session:
        row = write_store.add_suppression(
            session,
            repo_id,
            body.fingerprint,
            reason=body.reason,
            created_by=principal.name,
            expires_at=body.expires_at,
        )
        fingerprint = row.fingerprint
    audit.info("suppression add repo=%d fp=%s by=%s", repo_id, fingerprint, principal.name)
    return {"fingerprint": fingerprint}


@router.delete(
    "/repos/{repo_id}/suppressions/{fingerprint}",
    dependencies=[Depends(require_role("admin"))],
)
def delete_suppression(
    request: Request, repo_id: int, fingerprint: str, principal: CurrentPrincipal
) -> dict[str, Any]:
    _require_repo(request, repo_id)
    with request.app.state.graph_session_factory() as session:
        removed = write_store.remove_suppression(session, repo_id, fingerprint)
    if not removed:
        raise HTTPException(status_code=404, detail="no suppression for that fingerprint")
    audit.info("suppression remove repo=%d fp=%s by=%s", repo_id, fingerprint, principal.name)
    return {"removed": fingerprint}
