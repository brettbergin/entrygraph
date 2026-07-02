"""Engine construction with SQLite tuned for bulk-write and read-heavy workloads."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-64000",  # 64 MiB page cache
    "PRAGMA mmap_size=268435456",  # 256 MiB
)


def make_engine(db_path: str | Path) -> Engine:
    engine = create_engine(f"sqlite:///{Path(db_path)}")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - exercised via tests
        cursor = dbapi_conn.cursor()
        for pragma in _PRAGMAS:
            cursor.execute(pragma)
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
