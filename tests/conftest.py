from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from entrygraph.db.engine import make_engine, make_session_factory
from entrygraph.db.meta import create_schema

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_engine(tmp_path: Path) -> Engine:
    """A file-backed engine (not :memory:) so WAL/pragma behavior is exercised."""
    engine = make_engine(tmp_path / "test.db")
    create_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(tmp_engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(tmp_engine)


@pytest.fixture
def fixture_repo(tmp_path: Path):
    """Copy a fixture repo into tmp_path so tests can mutate files."""

    def _copy(name: str) -> Path:
        src = FIXTURES / name
        dst = tmp_path / "repo" / name.replace("/", "_")
        shutil.copytree(src, dst)
        return dst

    return _copy
