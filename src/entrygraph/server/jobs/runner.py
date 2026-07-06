"""JobRunner: the in-process queue consumer.

The Job table is the queue. Claims are single atomic UPDATEs (safe on SQLite
and Postgres), execution happens in a worker thread (indexing is CPU-bound and
saturates cores via its internal parse pool — hence concurrency defaults to 1),
and a boot-scoped worker token lets startup mark jobs orphaned by a crash or
restart as failed instead of leaving them "running" forever.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid

from sqlalchemy import select, update

from entrygraph.server.config import ServerConfig
from entrygraph.server.jobs.handlers import HANDLERS, JobCancelled
from entrygraph.server.models import Job, utcnow

logger = logging.getLogger("entrygraph.server.jobs")

_POLL_INTERVAL_S = 1.0


class JobRunner:
    def __init__(self, config: ServerConfig, app_session_factory) -> None:
        self._config = config
        self._session_factory = app_session_factory
        self._boot_token = uuid.uuid4().hex
        self._wake = asyncio.Event()
        self._stopping = False
        self._tasks: set[asyncio.Task] = set()

    # -------- lifecycle --------

    def recover_orphans(self) -> int:
        """Mark jobs left 'running' by a previous process as failed (startup)."""
        with self._session_factory() as session:
            result = session.execute(
                update(Job)
                .where(Job.status == "running", Job.worker_token != self._boot_token)
                .values(
                    status="failed",
                    error="orphaned by server restart",
                    finished_at=utcnow(),
                )
            )
            session.commit()
            count = result.rowcount or 0
        if count:
            logger.warning("marked %d orphaned running job(s) failed", count)
        return count

    def nudge(self) -> None:
        """Wake the poll loop immediately (called by enqueue endpoints)."""
        self._wake.set()

    async def run(self) -> None:
        self.recover_orphans()
        sem = asyncio.Semaphore(self._config.jobs_concurrency)
        while not self._stopping:
            claimed = self._claim_next()
            if claimed is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=_POLL_INTERVAL_S)
                except TimeoutError:
                    pass
                continue
            await sem.acquire()
            task = asyncio.create_task(self._execute(claimed, sem))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        # let in-flight jobs finish; they're thread-bound and short-checkpointed
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # -------- claim + execute --------

    def _claim_next(self) -> str | None:
        with self._session_factory() as session:
            job_id = session.execute(
                select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(1)
            ).scalar()
            if job_id is None:
                return None
            claimed = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "queued")
                .values(status="running", worker_token=self._boot_token, started_at=utcnow())
            )
            session.commit()
            return job_id if (claimed.rowcount or 0) == 1 else None

    async def _execute(self, job_id: str, sem: asyncio.Semaphore) -> None:
        try:
            import json

            with self._session_factory() as session:
                job = session.get(Job, job_id)
                if job is None:
                    return
                job_type, params = job.type, json.loads(job.params_json)
            handler = HANDLERS.get(job_type)
            if handler is None:
                self._finish(job_id, "failed", error=f"unknown job type {job_type!r}")
                return
            result = await asyncio.to_thread(
                handler, params, self._config, self._session_factory, job_id
            )
            self._finish(job_id, "succeeded", result=result)
        except JobCancelled:
            self._finish(job_id, "cancelled")
        except Exception as exc:  # a failed job must never kill the runner
            logger.exception("job %s failed", job_id)
            tail = traceback.format_exc(limit=8)
            self._finish(job_id, "failed", error=f"{exc}\n{tail}"[-4000:])
        finally:
            sem.release()

    def _finish(
        self, job_id: str, status: str, *, result: dict | None = None, error: str | None = None
    ) -> None:
        import json

        with self._session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.status = status
            job.finished_at = utcnow()
            if status == "succeeded":
                job.progress = 1.0
                job.phase = "done"
                job.message = None
            if error is not None:
                job.error = error
            if result is not None:
                job.stats_json = json.dumps(result.get("stats"))
                job.repo_root = result.get("repo_root")
                job.repo_id = result.get("repo_id")
            session.commit()


def enqueue_job(session_factory, *, job_type: str, params: dict, created_by: str | None) -> str:
    """Insert a queued Job row; returns its id. Callers should nudge the runner."""
    import json

    job = Job(
        id=uuid.uuid4().hex,
        type=job_type,
        status="queued",
        params_json=json.dumps(params, sort_keys=True),
        created_by=created_by,
    )
    with session_factory() as session:
        session.add(job)
        session.commit()
    return job.id
