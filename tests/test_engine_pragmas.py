from __future__ import annotations

from sqlalchemy import text


def test_pragmas_applied_per_connection(tmp_engine):
    with tmp_engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL
        assert conn.execute(text("PRAGMA temp_store")).scalar() == 2  # MEMORY


def test_schema_version_stamped(tmp_engine):
    from entrygraph.db.meta import SCHEMA_VERSION, stored_schema_version
    from entrygraph.db.migrations import prepare_db

    assert stored_schema_version(tmp_engine) == SCHEMA_VERSION
    assert prepare_db(tmp_engine) is False  # already current: no migration, no rebuild


def test_schema_newer_than_binary_raises(tmp_engine):
    import pytest
    from sqlalchemy.orm import Session

    from entrygraph.db.migrations import prepare_db
    from entrygraph.db.models import Meta
    from entrygraph.errors import SchemaMismatchError

    # a DB written by a future entrygraph cannot be read safely and must not be
    # silently rebuilt.
    with Session(tmp_engine) as s:
        s.get(Meta, "schema_version").value = "999"
        s.commit()
    with pytest.raises(SchemaMismatchError):
        prepare_db(tmp_engine)
