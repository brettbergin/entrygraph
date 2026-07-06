"""App-DB lifecycle: schema creation, idempotence, and secret persistence.

These exercise the durable-store logic on SQLite (CI-safe). The same code runs
unchanged against Postgres in a deployment — ``make_app_engine`` only attaches
SQLite pragmas for ``sqlite://`` URLs and every column type is portable — so a
production ``EG_APP_DATABASE_URL=postgresql+psycopg://…`` uses this exact path.
"""

from __future__ import annotations

from sqlalchemy import inspect, select

from entrygraph.server.appdb import (
    APP_SCHEMA_VERSION,
    ensure_app_schema,
    get_or_create_secret,
    make_app_engine,
    make_app_session_factory,
)
from entrygraph.server.models import AppMeta, User


def _engine(tmp_path):
    return make_app_engine(f"sqlite:///{tmp_path / 'app.db'}")


def test_ensure_schema_is_idempotent(tmp_path):
    engine = _engine(tmp_path)
    ensure_app_schema(engine)
    ensure_app_schema(engine)  # second run must be a no-op, not an error
    tables = set(inspect(engine).get_table_names())
    assert {"users", "user_sessions", "api_keys", "jobs", "repo_sources", "app_meta"} <= tables
    with make_app_session_factory(engine)() as s:
        version = s.get(AppMeta, "app_schema_version")
        assert version is not None and int(version.value) == APP_SCHEMA_VERSION


def test_schema_survives_reopen_with_data(tmp_path):
    engine = _engine(tmp_path)
    ensure_app_schema(engine)
    with make_app_session_factory(engine)() as s:
        s.add(User(sub="dev:local", role="admin"))
        s.commit()
    # reopening the same DB must preserve rows (durable, unlike the graph cache)
    engine2 = make_app_engine(f"sqlite:///{tmp_path / 'app.db'}")
    ensure_app_schema(engine2)
    with make_app_session_factory(engine2)() as s:
        assert s.execute(select(User).where(User.sub == "dev:local")).scalar_one().role == "admin"


def test_get_or_create_secret_is_stable(tmp_path):
    engine = _engine(tmp_path)
    ensure_app_schema(engine)
    first = get_or_create_secret(engine, "session_secret")
    assert first and get_or_create_secret(engine, "session_secret") == first  # persisted
    assert get_or_create_secret(engine, "other_secret") != first  # per-key
