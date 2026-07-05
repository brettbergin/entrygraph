"""Sentinel persistence: installations + installation-scoped repos (#126, M2).

Backs the existing gate tables (baselines / scan runs / findings / suppressions /
policy) with a URL-configured engine — SQLite for tests, Postgres in production —
plus the ``Installation`` / ``InstallationRepo`` tables. A repo's baselines and
findings hang off a stable central ``Repository`` row keyed by a synthetic
``root_path`` (``sentinel://<installation>/<owner>/<name>``), so a scan can index
the head checkout into an ephemeral graph DB while its baseline lives here.

Every write is installation-scoped; :func:`delete_installation` hard-deletes all
of an installation's data through the repository cascade — the uninstall path.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, create_engine, delete, event, select
from sqlalchemy.orm import Session, sessionmaker

from entrygraph.db import models
from entrygraph.db.meta import create_schema
from entrygraph.db.models import Repository
from entrygraph.sentinel.models import (  # noqa: F401 (register tables)
    Installation,
    InstallationRepo,
)


def make_store_engine(database_url: str) -> Engine:
    """Engine for the Sentinel findings store. SQLite gets foreign-key enforcement
    turned on so the uninstall cascade actually fires; Postgres enforces it
    natively."""
    engine = create_engine(database_url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record) -> None:  # pragma: no cover - trivial pragma
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def init_store(engine: Engine) -> sessionmaker[Session]:
    """Create the schema (core gate tables + the Sentinel tables, since importing
    this module registered them) and return a session factory."""
    create_schema(engine)
    return sessionmaker(engine, expire_on_commit=False)


# ---------------- installations ----------------


def upsert_installation(
    session: Session, installation_id: int, account_login: str, *, now: datetime
) -> Installation:
    """Record (or refresh) an installation. Idempotent on the GitHub installation id."""
    inst = session.get(Installation, installation_id)
    if inst is None:
        inst = Installation(
            id=installation_id, account_login=account_login, created_at=now, suspended=False
        )
        session.add(inst)
    else:
        inst.account_login = account_login
        inst.suspended = False
    session.commit()
    return inst


def ensure_installation(
    session: Session, installation_id: int, account_login: str, *, now: datetime
) -> Installation:
    """Create the installation row if it is missing, else return it unchanged.

    Unlike :func:`upsert_installation` this never clears a suspension — it is the
    defensive guard the scan path uses so a repo mapping never dangles, without
    resurrecting a suspended installation."""
    inst = session.get(Installation, installation_id)
    if inst is None:
        inst = Installation(
            id=installation_id, account_login=account_login, created_at=now, suspended=False
        )
        session.add(inst)
        session.commit()
    return inst


def get_installation(session: Session, installation_id: int) -> Installation | None:
    return session.get(Installation, installation_id)


def set_suspended(session: Session, installation_id: int, suspended: bool) -> None:
    inst = session.get(Installation, installation_id)
    if inst is not None:
        inst.suspended = suspended
        session.commit()


def _synthetic_root(installation_id: int, full_name: str) -> str:
    return f"sentinel://{installation_id}/{full_name}"


def resolve_repo(session: Session, installation_id: int, full_name: str, *, now: datetime) -> int:
    """Stable central ``repo_id`` for an installation's repo, creating the
    ``Repository`` row and the installation mapping on first sight."""
    mapping = session.execute(
        select(InstallationRepo).where(
            InstallationRepo.installation_id == installation_id,
            InstallationRepo.full_name == full_name,
        )
    ).scalar_one_or_none()
    if mapping is not None:
        return mapping.repo_id

    root = _synthetic_root(installation_id, full_name)
    repo = session.execute(
        select(Repository).where(Repository.root_path == root)
    ).scalar_one_or_none()
    if repo is None:
        repo = Repository(root_path=root, indexed_at=now)
        session.add(repo)
        session.flush()  # assign repo.id
    session.add(
        InstallationRepo(installation_id=installation_id, repo_id=repo.id, full_name=full_name)
    )
    session.commit()
    return repo.id


def installation_repo_ids(session: Session, installation_id: int) -> list[int]:
    rows = session.execute(
        select(InstallationRepo.repo_id).where(InstallationRepo.installation_id == installation_id)
    ).scalars()
    return list(rows)


def delete_installation(session: Session, installation_id: int) -> None:
    """Hard-delete an installation and every trace of its repos — the uninstall
    path. Deleting the ``Repository`` rows cascades to the graph, baselines,
    findings, suppressions, and the installation mapping; the installation row
    goes last."""
    repo_ids = installation_repo_ids(session, installation_id)
    if repo_ids:
        session.execute(delete(Repository).where(Repository.id.in_(repo_ids)))
    session.execute(delete(Installation).where(Installation.id == installation_id))
    session.commit()


# ---------------- REST API queries (M4) ----------------


def repo_id_for(session: Session, installation_id: int, full_name: str) -> int | None:
    """The central repo_id for an installation's repo, or None if never scanned.

    Read-only counterpart to :func:`resolve_repo` — the API never creates rows for
    a repo that has no history."""
    return session.execute(
        select(InstallationRepo.repo_id).where(
            InstallationRepo.installation_id == installation_id,
            InstallationRepo.full_name == full_name,
        )
    ).scalar_one_or_none()


def list_scans(session: Session, repo_id: int, *, limit: int = 50) -> list[models.ScanRun]:
    """Most recent scan runs for a repo, newest first."""
    return list(
        session.execute(
            select(models.ScanRun)
            .where(models.ScanRun.repo_id == repo_id)
            .order_by(models.ScanRun.created_at.desc(), models.ScanRun.id.desc())
            .limit(limit)
        ).scalars()
    )


def get_scan(session: Session, repo_id: int, scan_id: int) -> models.ScanRun | None:
    """A scan run, but only if it belongs to ``repo_id`` (installation-scoped)."""
    return session.execute(
        select(models.ScanRun).where(
            models.ScanRun.id == scan_id, models.ScanRun.repo_id == repo_id
        )
    ).scalar_one_or_none()


def scan_findings(
    session: Session, scan_id: int, *, status: str | None = None
) -> list[models.Finding]:
    stmt = select(models.Finding).where(models.Finding.scan_run_id == scan_id)
    if status is not None:
        stmt = stmt.where(models.Finding.status == status)
    return list(session.execute(stmt.order_by(models.Finding.risk.desc())).scalars())


def latest_scan(session: Session, repo_id: int) -> models.ScanRun | None:
    return session.execute(
        select(models.ScanRun)
        .where(models.ScanRun.repo_id == repo_id)
        .order_by(models.ScanRun.created_at.desc(), models.ScanRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_suppressions(session: Session, repo_id: int) -> list[models.Suppression]:
    return list(
        session.execute(
            select(models.Suppression).where(models.Suppression.repo_id == repo_id)
        ).scalars()
    )


def add_suppression(
    session: Session,
    repo_id: int,
    fingerprint: str,
    *,
    reason: str | None = None,
    created_by: str | None = None,
    expires_at: datetime | None = None,
) -> models.Suppression:
    """Add (or replace) a waiver for a fingerprint on a repo."""
    existing = session.execute(
        select(models.Suppression).where(
            models.Suppression.repo_id == repo_id,
            models.Suppression.fingerprint == fingerprint,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.reason = reason
        existing.created_by = created_by
        existing.expires_at = expires_at
        session.commit()
        return existing
    sup = models.Suppression(
        repo_id=repo_id,
        fingerprint=fingerprint,
        reason=reason,
        created_by=created_by,
        expires_at=expires_at,
    )
    session.add(sup)
    session.commit()
    return sup


def remove_suppression(session: Session, repo_id: int, fingerprint: str) -> bool:
    """Delete a waiver; returns True if one existed."""
    row = session.execute(
        select(models.Suppression).where(
            models.Suppression.repo_id == repo_id,
            models.Suppression.fingerprint == fingerprint,
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def set_policy(
    session: Session,
    repo_id: int,
    *,
    risk_threshold: float | None = None,
    gated_categories: list[str] | None = None,
    mode: str | None = None,
    min_confidence: str | None = None,
) -> models.RepoPolicy:
    """Upsert a repo's gate policy; only the provided fields change."""
    import json

    row = session.get(models.RepoPolicy, repo_id)
    if row is None:
        row = models.RepoPolicy(repo_id=repo_id)
        session.add(row)
    if risk_threshold is not None:
        row.risk_threshold = risk_threshold
    if gated_categories is not None:
        row.gated_categories = json.dumps(gated_categories)
    if mode is not None:
        row.mode = mode
    if min_confidence is not None:
        row.min_confidence = min_confidence
    session.commit()
    return row
