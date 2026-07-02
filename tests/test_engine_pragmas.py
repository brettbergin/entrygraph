from __future__ import annotations

from sqlalchemy import text


def test_pragmas_applied_per_connection(tmp_engine):
    with tmp_engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL
        assert conn.execute(text("PRAGMA temp_store")).scalar() == 2  # MEMORY


def test_schema_version_stamped(tmp_engine):
    from entrygraph.db.meta import SCHEMA_VERSION, check_schema, stored_schema_version

    assert stored_schema_version(tmp_engine) == SCHEMA_VERSION
    check_schema(tmp_engine)  # must not raise


def test_schema_mismatch_raises(tmp_engine):
    import pytest
    from sqlalchemy.orm import Session

    from entrygraph.db.meta import check_schema
    from entrygraph.db.models import Meta
    from entrygraph.errors import SchemaMismatchError

    with Session(tmp_engine) as s:
        s.get(Meta, "schema_version").value = "999"
        s.commit()
    with pytest.raises(SchemaMismatchError):
        check_schema(tmp_engine)


def test_ensure_schema_rebuilds_on_mismatch(tmp_engine):
    from sqlalchemy.orm import Session

    from entrygraph.db.meta import SCHEMA_VERSION, ensure_schema, stored_schema_version
    from entrygraph.db.models import Meta, Repository

    with Session(tmp_engine) as s:
        s.add(Repository(id=1, root_path="/tmp/x"))
        s.get(Meta, "schema_version").value = "999"
        s.commit()

    rebuilt = ensure_schema(tmp_engine)
    assert rebuilt is True
    assert stored_schema_version(tmp_engine) == SCHEMA_VERSION
    with Session(tmp_engine) as s:
        assert s.get(Repository, 1) is None  # old data dropped

    assert ensure_schema(tmp_engine) is False  # already current: no-op
