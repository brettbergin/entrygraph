"""App-DB lifecycle: engine construction and additive schema upgrades.

Unlike the graph DB (a rebuildable cache that drops everything on a version
mismatch), the app DB is durable. Five small tables don't justify alembic:
``ensure_app_schema`` runs ``create_all`` then applies ordered, additive
upgrade functions keyed by version. New columns/tables go in an upgrade
function; destructive changes are not allowed here.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from entrygraph.server.models import AppBase, AppMeta

APP_SCHEMA_VERSION = 1

# version -> upgrade applied when moving from version-1 to version. Additive only.
_UPGRADES: dict[int, Callable[[Connection], None]] = {}


def make_app_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}  # busy_timeout: index jobs hold writes
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - via tests
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_app_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def ensure_app_schema(engine: Engine) -> None:
    """Create missing tables and apply pending additive upgrades. Idempotent."""
    AppBase.metadata.create_all(engine)
    with Session(engine) as session:
        row = session.get(AppMeta, "app_schema_version")
        current = int(row.value) if row else 0
        if current == 0:
            # fresh DB: create_all built the latest schema already
            session.add(AppMeta(key="app_schema_version", value=str(APP_SCHEMA_VERSION)))
            session.commit()
            return
        if row is None:  # unreachable: current > 0 implies the meta row exists
            raise RuntimeError("app_meta schema row vanished mid-upgrade")
        if current > APP_SCHEMA_VERSION:
            raise RuntimeError(
                f"app database schema version {current} is newer than this entrygraph "
                f"({APP_SCHEMA_VERSION}); upgrade entrygraph"
            )
        for version in range(current + 1, APP_SCHEMA_VERSION + 1):
            upgrade = _UPGRADES.get(version)
            if upgrade is not None:
                with engine.begin() as conn:
                    upgrade(conn)
            row.value = str(version)
            session.commit()


def get_or_create_secret(engine: Engine, key: str) -> str:
    """A persisted random secret (e.g. the OIDC-state cookie signer) so sessions
    survive server restarts without requiring the operator to mint one."""
    with Session(engine) as session:
        row = session.execute(select(AppMeta).where(AppMeta.key == key)).scalar_one_or_none()
        if row is not None:
            return row.value
        value = secrets.token_urlsafe(48)
        session.add(AppMeta(key=key, value=value))
        session.commit()
        return value
