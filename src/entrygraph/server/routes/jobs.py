"""Job status and control. The UI polls GET /jobs/{id} while a job runs."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from entrygraph.server.auth.deps import CurrentPrincipal, current_principal, require_role
from entrygraph.server.models import Job

audit = logging.getLogger("entrygraph.server.audit")

router = APIRouter(dependencies=[Depends(current_principal)])


def _job_json(j: Job) -> dict[str, Any]:
    return {
        "id": j.id,
        "type": j.type,
        "status": j.status,
        "params": json.loads(j.params_json) if j.params_json else {},
        "repo_root": j.repo_root,
        "repo_id": j.repo_id,
        "progress": j.progress,
        "phase": j.phase,
        "message": j.message,
        "error": j.error,
        "stats": json.loads(j.stats_json) if j.stats_json else None,
        "created_by": j.created_by,
        "cancel_requested": j.cancel_requested,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


@router.get("/jobs")
def jobs(
    request: Request,
    status: str | None = None,
    repo_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(max(1, min(limit, 200)))
    if status:
        stmt = stmt.where(Job.status == status)
    if repo_id is not None:
        stmt = stmt.where(Job.repo_id == repo_id)
    with request.app.state.app_session_factory() as session:
        rows = session.execute(stmt).scalars().all()
    return {"jobs": [_job_json(j) for j in rows]}


@router.get("/jobs/{job_id}")
def job_detail(request: Request, job_id: str) -> dict[str, Any]:
    with request.app.state.app_session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"job": _job_json(job)}


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_role("admin"))])
def cancel_job(request: Request, job_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    """Queued jobs cancel immediately; running jobs get cancel_requested and
    stop at their next progress checkpoint (cooperative)."""
    with request.app.state.app_session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status == "queued":
            job.status = "cancelled"
            from entrygraph.server.models import utcnow

            job.finished_at = utcnow()
        elif job.status == "running":
            job.cancel_requested = True
        else:
            raise HTTPException(status_code=409, detail=f"job is already {job.status}")
        session.commit()
        state = job.status
    audit.info("job cancel id=%s state=%s by=%s", job_id, state, principal.name)
    return {"status": state}
