"""Schema-version bookkeeping.

The database is a rebuildable cache of the repository, not a system of record:
instead of migrations, a version mismatch either raises (read paths) or drops
and recreates every table (index paths).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from entrygraph.db.models import Base, Meta
from entrygraph.errors import SchemaMismatchError

SCHEMA_VERSION = 8  # gate/sentinel tables removed; risk scoring retired from paths


def create_schema(engine: Engine) -> None:
    """Create all tables and stamp the current schema version."""
    from entrygraph import __version__

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        existing = session.get(Meta, "schema_version")
        if existing is None:
            session.add_all(
                [
                    Meta(key="schema_version", value=str(SCHEMA_VERSION)),
                    Meta(key="entrygraph_version", value=__version__),
                    Meta(key="created_at", value=datetime.now(UTC).isoformat()),
                ]
            )
            session.commit()


def stored_schema_version(engine: Engine) -> int | None:
    """Return the version stamped in the db, or None if the meta table is absent/empty."""
    with Session(engine) as session:
        try:
            row = session.execute(select(Meta.value).where(Meta.key == "schema_version")).scalar()
        except Exception:
            return None
    return int(row) if row is not None else None


def check_schema(engine: Engine) -> None:
    """Raise SchemaMismatchError unless the db matches SCHEMA_VERSION."""
    version = stored_schema_version(engine)
    if version != SCHEMA_VERSION:
        raise SchemaMismatchError(
            f"database schema version {version!r} != expected {SCHEMA_VERSION}; "
            "re-run `entrygraph index` to rebuild the index"
        )


def ensure_schema(engine: Engine) -> bool:
    """Make the db usable for indexing: create if empty, rebuild if mismatched.

    Returns True if the schema was (re)created from scratch.
    """
    version = stored_schema_version(engine)
    if version == SCHEMA_VERSION:
        return False
    if version is not None:
        Base.metadata.drop_all(engine)
    create_schema(engine)
    return True
