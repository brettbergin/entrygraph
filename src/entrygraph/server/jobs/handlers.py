"""Job handlers. Each runs synchronously inside a worker thread.

``run_index_job`` is the whole UI-triggered ingestion path: hardened
clone/fetch via :func:`entrygraph.fs.remote.prepare_source`, then
:func:`index_repository` with progress + cancellation wired to the Job row,
then a RepoSource upsert so "Reindex" works after graph rebuilds.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from sqlalchemy import select

from entrygraph.db import models as graph_models
from entrygraph.db.engine import make_engine
from entrygraph.errors import IndexCancelledError
from entrygraph.fs.remote import clone_destination, is_git_url, prepare_source
from entrygraph.pipeline.scanner import index_repository
from entrygraph.server.config import ServerConfig
from entrygraph.server.jobs.progress import ProgressReporter
from entrygraph.server.models import RepoSource


class JobCancelled(Exception):
    """The job's cancel_requested flag was honored."""


def run_index_job(
    params: dict,
    config: ServerConfig,
    app_session_factory,
    job_id: str,
) -> dict:
    """Execute one index job; returns the stats dict recorded on the Job row.

    ``params``: {source, ref?, depth?, full?, paranoid?, include_tests?, incremental?}
    """
    reporter = ProgressReporter(app_session_factory, job_id)
    source: str = params["source"]

    clone_dir: Path | None = None
    if is_git_url(source):
        if not reporter.set_phase("cloning", f"cloning {source}"):
            raise JobCancelled(job_id)
        # server policy: clones always land under EG_CLONE_DIR (never client-chosen)
        clone_dir = clone_destination(source, Path(config.clone_dir))

    with prepare_source(
        source,
        ref=params.get("ref"),
        depth=int(params.get("depth") or 1),
        timeout=config.git_timeout_s,
        clone_dir=clone_dir,
    ) as clone:
        engine = make_engine(config.db_path)
        try:
            stats = index_repository(
                clone.root,
                engine,
                incremental=bool(params.get("incremental", False)),
                paranoid=bool(params.get("paranoid", False)),
                include_tests=bool(params.get("include_tests", False)),
                on_progress=reporter,
            )
        except IndexCancelledError as exc:
            raise JobCancelled(job_id) from exc
        finally:
            engine.dispose()

        _upsert_repo_source(app_session_factory, clone.root, params, clone.url)
        repo_id = _graph_repo_id(config.db_path, clone.root)

    return {
        "stats": dataclasses.asdict(stats),
        "repo_root": str(clone.root),
        "repo_id": repo_id,
    }


def _upsert_repo_source(app_session_factory, root: Path, params: dict, url: str | None) -> None:
    with app_session_factory() as session:
        row = session.execute(
            select(RepoSource).where(RepoSource.root_path == str(root))
        ).scalar_one_or_none()
        if row is None:
            row = RepoSource(root_path=str(root))
            session.add(row)
        row.url = url
        row.ref = params.get("ref")
        row.depth = int(params.get("depth") or 1)
        row.include_tests = bool(params.get("include_tests", False))
        if params.get("created_by"):
            row.created_by = params["created_by"]
        session.commit()


def _graph_repo_id(db_path: str, root: Path) -> int | None:
    engine = make_engine(db_path)
    try:
        from sqlalchemy.orm import Session

        with Session(engine) as session:
            return session.execute(
                select(graph_models.Repository.id).where(
                    graph_models.Repository.root_path == str(root)
                )
            ).scalar()
    finally:
        engine.dispose()


def delete_repo_data(config: ServerConfig, app_session_factory, repo_id: int) -> str | None:
    """Delete a repository's graph rows (FK cascade takes files/symbols/edges/
    gate rows) and its RepoSource. Returns the removed root_path."""
    engine = make_engine(config.db_path)
    try:
        from sqlalchemy.orm import Session

        with Session(engine) as session:
            repo = session.get(graph_models.Repository, repo_id)
            if repo is None:
                return None
            root = repo.root_path
            session.delete(repo)
            session.commit()
    finally:
        engine.dispose()
    with app_session_factory() as session:
        row = session.execute(
            select(RepoSource).where(RepoSource.root_path == root)
        ).scalar_one_or_none()
        if row is not None:
            session.delete(row)
            session.commit()
    return root


HANDLERS = {"index": run_index_job}


def params_json(params: dict) -> str:
    return json.dumps(params, sort_keys=True)
