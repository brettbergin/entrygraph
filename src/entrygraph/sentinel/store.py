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
