"""In-place schema migrations for the graph database.

The graph DB is a rebuildable cache, but rebuilding means re-parsing every repo —
untenable for a customer with thousands of repos on every deploy. So a structural
change is applied in place by an ordered migration step instead of dropping all
tables. This mirrors the durable app-DB upgrade loop in ``entrygraph.server.appdb``.

``MIGRATIONS`` maps a *from* schema version to a function that migrates the DB to
the next version. ``prepare_db`` is the single entry point every DB open goes
through: it creates a fresh DB, no-ops when already current, migrates in place
when behind, and raises when the DB is newer than this binary. A logic/extractor
change must NOT add a migration here (see ``meta.ANALYZER_VERSION``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from entrygraph.db.meta import (
    ANALYZER_VERSION,
    SCHEMA_VERSION,
    STALE_ANALYZER_VERSION,
    create_schema,
    stored_schema_version,
)
from entrygraph.db.models import Base
from entrygraph.errors import SchemaMismatchError

# from_version -> function migrating the DB from that version to from_version + 1.
MIGRATIONS: dict[int, Callable[[Connection], None]] = {}

# The analyzer version that v9-era data reflects (GraphQL detection, analyzer 1).
# The 9->10 backfill must stamp THIS, not the live ANALYZER_VERSION: a v9 DB
# migrating under a newer binary predates that binary's analyzer and must read
# as stale, not be silently promoted to current.
_V9_ANALYZER_VERSION = 1

# Transient meta key set by the 8->9 step so the 9->10 step knows the data
# predates GraphQL detection and must be flagged analyzer-stale (not backfilled
# to the current analyzer). Deleted once consumed.
_PRE_ANALYZER_MARKER = "_migration_pre_analyzer"


def _register(from_version: int, fn: Callable[[Connection], None]) -> None:
    MIGRATIONS[from_version] = fn


def _has_column(conn: Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(text(f"PRAGMA table_info({table})")))


def _v8_to_v9(conn: Connection) -> None:
    """Schema 8 and 9 are structurally identical (v9 added GraphQL detection, not
    columns). But v8 data predates that analyzer, so mark the DB pre-analyzer; the
    9->10 step reads this to flag those repos stale instead of current."""
    conn.execute(
        text("INSERT OR REPLACE INTO meta (key, value) VALUES (:k, '1')"),
        {"k": _PRE_ANALYZER_MARKER},
    )


def _v9_to_v10(conn: Connection) -> None:
    """Add ``repositories.analyzer_version`` and backfill it.

    A DB that reached here from v9 reflects the v9-era analyzer (1), so its repos
    are stamped 1 — current if this binary is still on analyzer 1, stale (healing
    per-repo) under any later analyzer. A DB that came up from v8 carries the
    pre-analyzer marker and is stamped fully stale."""
    if not _has_column(conn, "repositories", "analyzer_version"):
        conn.execute(text("ALTER TABLE repositories ADD COLUMN analyzer_version INTEGER"))
    marker = conn.execute(
        text("SELECT value FROM meta WHERE key = :k"), {"k": _PRE_ANALYZER_MARKER}
    ).scalar()
    backfill = STALE_ANALYZER_VERSION if marker else _V9_ANALYZER_VERSION
    conn.execute(
        text("UPDATE repositories SET analyzer_version = :v WHERE analyzer_version IS NULL"),
        {"v": backfill},
    )
    conn.execute(text("DELETE FROM meta WHERE key = :k"), {"k": _PRE_ANALYZER_MARKER})


def _v10_to_v11(conn: Connection) -> None:
    """Add the ``entrypoint_parameters`` table (first-class parameter rows).

    Purely additive: existing rows are untouched and keep serving. Parameter
    rows appear per repo when an analyzer bump marks it stale and the heal
    re-index runs — this step itself does not change analyzer staleness."""
    Base.metadata.tables["entrypoint_parameters"].create(conn, checkfirst=True)


_register(8, _v8_to_v9)
_register(9, _v9_to_v10)
_register(10, _v10_to_v11)


def _backup(engine: Engine, from_version: int) -> None:
    """Best-effort consistent snapshot before migrating, so a failed multi-step
    migration is recoverable. VACUUM INTO captures committed WAL content too; a
    plain file copy would not. Never fatal — migrations are transactional."""
    db_path = engine.url.database
    if not db_path or db_path == ":memory:":
        return
    backup = f"{db_path}.bak-v{from_version}"
    with contextlib.suppress(Exception):
        Path(backup).unlink(missing_ok=True)
        raw = engine.raw_connection()
        try:
            dbapi = raw.driver_connection
            if dbapi is None:
                return
            prior = dbapi.isolation_level
            dbapi.isolation_level = None  # autocommit: VACUUM can't run in a transaction
            try:
                dbapi.execute(f"VACUUM INTO '{backup}'")
            finally:
                dbapi.isolation_level = prior
        finally:
            raw.close()


def run_migrations(engine: Engine, from_version: int, to_version: int) -> bool:
    """Migrate the DB in place from ``from_version`` to ``to_version``.

    Returns True only if it had to fall back to a full rebuild (a missing step in
    the chain — never expected in practice, but preserving cache semantics). Each
    step and its version stamp commit atomically together."""
    if any(v not in MIGRATIONS for v in range(from_version, to_version)):
        Base.metadata.drop_all(engine)
        create_schema(engine)
        return True
    _backup(engine, from_version)
    for v in range(from_version, to_version):
        with engine.begin() as conn:
            MIGRATIONS[v](conn)
            conn.execute(
                text("UPDATE meta SET value = :v WHERE key = 'schema_version'"),
                {"v": str(v + 1)},
            )
    return False


def prepare_db(engine: Engine) -> bool:
    """Make the DB usable at the current SCHEMA_VERSION, migrating in place.

    Returns True if the DB was created or rebuilt from scratch (no prior data
    preserved), False if it was already current or migrated in place. Callers that
    index use the return to force a full (non-incremental) build when there was
    nothing to preserve.

    Raises SchemaMismatchError only when the DB is *newer* than this binary — that
    can't be read safely and must not be silently rebuilt."""
    version = stored_schema_version(engine)
    if version is None:
        create_schema(engine)
        return True
    if version == SCHEMA_VERSION:
        # A pre-existing DB stamped at the current version still needs any tables
        # added since it was created; create_all is idempotent (CREATE IF NOT
        # EXISTS) and never drops.
        Base.metadata.create_all(engine)
        return False
    if version > SCHEMA_VERSION:
        raise SchemaMismatchError(
            f"database schema version {version} is newer than this entrygraph "
            f"(supports {SCHEMA_VERSION}); upgrade entrygraph to read it"
        )
    return run_migrations(engine, version, SCHEMA_VERSION)


def is_stale(analyzer_version: int | None) -> bool:
    """Whether a repo's stored analyzer version is behind the current one."""
    return (analyzer_version or STALE_ANALYZER_VERSION) < ANALYZER_VERSION
