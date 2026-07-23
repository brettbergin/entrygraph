"""In-place schema migrations: an older DB upgrades without losing data or
forcing a re-index, and per-repo analyzer staleness is derived correctly."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from entrygraph.db.engine import make_engine
from entrygraph.db.meta import ANALYZER_VERSION, SCHEMA_VERSION, create_schema
from entrygraph.db.migrations import is_stale, prepare_db
from entrygraph.db.models import Meta, Repository


def _old_db(path: Path, version: int) -> None:
    """Materialize a DB that looks like it was written by schema `version`:
    current tables minus the structures added after it, stamped at `version`,
    with one repo row inserted the way a binary of that era would."""
    engine = make_engine(path)
    create_schema(engine)  # builds the current schema
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE entrypoint_parameters"))  # added in v11
        if version < 10:
            conn.execute(text("ALTER TABLE repositories DROP COLUMN analyzer_version"))
        conn.execute(
            text("UPDATE meta SET value = :v WHERE key = 'schema_version'"), {"v": str(version)}
        )
        conn.execute(
            text(
                "INSERT INTO repositories (id, root_path, file_count, symbol_count, "
                "index_generation) VALUES (1, '/repo/a', 7, 55, 3)"
            )
        )
        if version >= 10:  # a v10 binary stamped the analyzer version it ran
            conn.execute(text("UPDATE repositories SET analyzer_version = :v"), {"v": 1})
    engine.dispose()


def _repo(engine) -> Repository:
    with Session(engine) as s:
        return s.get(Repository, 1)


def test_v9_migrates_in_place_no_data_loss(tmp_path):
    db = tmp_path / "v9.db"
    _old_db(db, 9)
    engine = make_engine(db)

    rebuilt = prepare_db(engine)

    assert rebuilt is False  # migrated in place, not dropped
    with Session(engine) as s:
        assert int(s.get(Meta, "schema_version").value) == SCHEMA_VERSION
        r = s.get(Repository, 1)
        assert (r.file_count, r.symbol_count, r.index_generation) == (7, 55, 3)  # preserved
        # v9 data reflects the v9-era analyzer (1), NOT whatever this binary runs:
        # under any later analyzer it reads stale and heals per-repo.
        assert r.analyzer_version == 1
        assert is_stale(r.analyzer_version) is (ANALYZER_VERSION > 1)


def test_v8_rescue_migrates_in_place_and_flags_stale(tmp_path):
    db = tmp_path / "v8.db"
    _old_db(db, 8)
    engine = make_engine(db)

    rebuilt = prepare_db(engine)

    assert rebuilt is False  # rescued in place, not dropped
    with Session(engine) as s:
        assert int(s.get(Meta, "schema_version").value) == SCHEMA_VERSION
        r = s.get(Repository, 1)
        assert (r.file_count, r.symbol_count) == (7, 55)  # existing data still served
        # v8 predates GraphQL detection -> stale, will heal in the background
        assert is_stale(r.analyzer_version) is True


def test_v10_adds_parameters_table_in_place(tmp_path):
    db = tmp_path / "v10.db"
    _old_db(db, 10)
    engine = make_engine(db)

    rebuilt = prepare_db(engine)

    assert rebuilt is False  # migrated in place, not dropped
    with Session(engine) as s:
        assert int(s.get(Meta, "schema_version").value) == SCHEMA_VERSION
        r = s.get(Repository, 1)
        assert (r.file_count, r.symbol_count, r.index_generation) == (7, 55, 3)  # preserved
        # the step is purely additive: analyzer staleness is untouched by it
        assert r.analyzer_version == 1
        cols = {row[1] for row in s.execute(text("PRAGMA table_info(entrypoint_parameters)"))}
        assert {"entrypoint_id", "name", "location", "required", "provenance"} <= cols


def test_migration_writes_a_backup(tmp_path):
    db = tmp_path / "v9.db"
    _old_db(db, 9)
    prepare_db(make_engine(db))
    assert (tmp_path / "v9.db.bak-v9").exists()  # recoverable snapshot before migrating


def test_already_current_is_a_noop(tmp_path):
    db = tmp_path / "cur.db"
    create_schema(make_engine(db))  # fresh -> stamped at SCHEMA_VERSION
    assert prepare_db(make_engine(db)) is False


def test_missing_migration_path_falls_back_to_rebuild(tmp_path):
    # a version with no migration step in the chain can't be migrated; the cache
    # is rebuilt from scratch as a last resort (data not preserved, but no crash).
    db = tmp_path / "gap.db"
    _old_db(db, 2)  # no MIGRATIONS[2..] path to SCHEMA_VERSION
    engine = make_engine(db)

    rebuilt = prepare_db(engine)

    assert rebuilt is True
    with Session(engine) as s:
        assert int(s.get(Meta, "schema_version").value) == SCHEMA_VERSION
        assert s.get(Repository, 1) is None  # rebuilt empty


def test_newer_than_binary_raises(tmp_path):
    import pytest

    from entrygraph.errors import SchemaMismatchError

    db = tmp_path / "future.db"
    create_schema(make_engine(db))
    engine = make_engine(db)
    with Session(engine) as s:
        s.get(Meta, "schema_version").value = str(SCHEMA_VERSION + 1)
        s.commit()
    with pytest.raises(SchemaMismatchError):
        prepare_db(engine)
