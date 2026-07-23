"""Version bookkeeping for the graph database.

Two independent versions, deliberately decoupled:

- ``SCHEMA_VERSION`` — the on-disk *structure* (tables/columns/indexes). Advanced
  by ordered, in-place migrations (see ``db.migrations``); a mismatch is never a
  reason to drop data.
- ``ANALYZER_VERSION`` — the *extraction/detection logic* that fills the tables.
  Bumped whenever that logic changes such that already-stored rows would differ
  (e.g. teaching the analyzer a new framework). Stored per repo in
  ``repositories.analyzer_version``; a repo behind the current value keeps
  serving its still-valid rows and is re-scanned per-repo in the background.

The split is what makes upgrades safe: a structural change migrates in place, an
analyzer change self-heals per repo — neither forces a fleet-wide re-index.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from entrygraph.db.models import Base, Meta

SCHEMA_VERSION = 10  # repositories.analyzer_version column (migrated in place from 8/9)

# Bump when parsing/detection logic changes so that already-indexed repos would
# produce different rows. Do NOT bump SCHEMA_VERSION for this. Stale repos heal
# per-repo (scanner heal gate) instead of a global rebuild.
ANALYZER_VERSION = 1  # baseline: GraphQL detection + everything current as of schema 10

# analyzer_version stamped on rows that predate the current analyzer (e.g. a
# database migrated up from schema 8, which had no GraphQL detection). Anything
# below ANALYZER_VERSION reads as stale.
STALE_ANALYZER_VERSION = 0


def create_schema(engine: Engine) -> None:
    """Create all tables and stamp the current schema version on a fresh DB."""
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
