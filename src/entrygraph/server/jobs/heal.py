"""Background self-heal: re-index repos whose data predates the current analyzer.

When a deploy ships new detection logic, existing repos keep serving their still
-valid rows but are flagged stale (``repositories.analyzer_version`` behind
``meta.ANALYZER_VERSION``). This sweep enqueues an ordinary index job for each
stale repo, reusing the same path the "Reindex" button uses — the repo heals in
the background, one at a time, while everything else keeps serving. No global
outage, no operator action.

A restart is exactly when staleness appears (a new binary carries a new
ANALYZER_VERSION), so a single sweep at startup covers the common case;
``ServerConfig.heal_interval_s`` can also repeat it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.db import models as graph_models
from entrygraph.db.engine import make_engine
from entrygraph.db.migrations import is_stale
from entrygraph.fs.remote import is_git_url
from entrygraph.server.config import ServerConfig
from entrygraph.server.jobs.runner import enqueue_job
from entrygraph.server.models import Job, RepoSource

logger = logging.getLogger("entrygraph.server.jobs")

_HEAL_ACTOR = "auto-heal"


def _job_source(job: Job) -> str | None:
    try:
        return json.loads(job.params_json).get("source")
    except Exception:
        return None


def sweep_stale_repos(config: ServerConfig, app_session_factory) -> int:
    """Enqueue an index job for each stale repo. Returns the number enqueued.

    Skips repos that already have a queued/running index job (dedup by source) and
    repos whose source (git URL or local path) is no longer reachable."""
    engine = make_engine(config.db_path)
    try:
        with Session(engine) as session:
            stale_roots = [
                r.root_path
                for r in session.execute(select(graph_models.Repository)).scalars()
                if is_stale(r.analyzer_version)
            ]
    finally:
        engine.dispose()
    if not stale_roots:
        return 0

    with app_session_factory() as app:
        sources = {s.root_path: s for s in app.execute(select(RepoSource)).scalars()}
        in_flight = {
            src
            for job in app.execute(
                select(Job).where(Job.type == "index", Job.status.in_(("queued", "running")))
            ).scalars()
            if (src := _job_source(job)) is not None
        }

    enqueued = 0
    for root in stale_roots:
        src_row = sources.get(root)
        source = src_row.url if src_row and src_row.url else root
        if source in in_flight:
            continue  # already being (re)indexed
        if not is_git_url(source) and not Path(source).is_dir():
            logger.warning("heal: skipping %s — source no longer reachable", source)
            continue
        enqueue_job(
            app_session_factory,
            job_type="index",
            params={
                "source": source,
                "ref": src_row.ref if src_row else None,
                "depth": src_row.depth if src_row else 1,
                "include_tests": src_row.include_tests if src_row else False,
                "incremental": True,  # the scanner heal gate forces a full re-scan
            },
            created_by=_HEAL_ACTOR,
        )
        enqueued += 1
    if enqueued:
        logger.info("heal: enqueued %d stale repo re-index job(s)", enqueued)
    return enqueued
