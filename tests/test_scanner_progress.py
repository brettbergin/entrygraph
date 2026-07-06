"""index_repository progress callback + cancellation."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from entrygraph.db.engine import make_engine
from entrygraph.db.models import Symbol
from entrygraph.errors import IndexCancelledError
from entrygraph.pipeline.scanner import index_repository

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


def _symbol_count(engine) -> int:
    with Session(engine) as session:
        return session.execute(select(func.count(Symbol.id))).scalar() or 0


def test_progress_fires_per_phase(tmp_path):
    engine = make_engine(tmp_path / "g.db")
    events: list[tuple[str, int, int]] = []

    stats = index_repository(FLASK_APP, engine, on_progress=lambda *e: events.append(e))

    assert stats.symbols > 0
    phases = [e[0] for e in events]
    assert phases[0] == "walking"
    assert "resolving" in phases
    assert phases[-1] == "writing"
    # done never exceeds total
    assert all(done <= total for _, done, total in events)


def test_no_callback_is_default(tmp_path):
    engine = make_engine(tmp_path / "g.db")
    assert index_repository(FLASK_APP, engine).symbols > 0


def test_throwing_callback_does_not_corrupt_run(tmp_path):
    engine = make_engine(tmp_path / "g.db")

    def bad(*_e):
        raise RuntimeError("observer crashed")

    stats = index_repository(FLASK_APP, engine, on_progress=bad)
    assert stats.symbols > 0
    assert _symbol_count(engine) == stats.symbols


def test_cancellation_rolls_back(tmp_path):
    engine = make_engine(tmp_path / "g.db")

    with pytest.raises(IndexCancelledError):
        index_repository(FLASK_APP, engine, on_progress=lambda *_e: False)
    # the transaction rolled back: no partial graph was committed
    assert _symbol_count(engine) == 0


def test_cancellation_preserves_prior_index(tmp_path):
    engine = make_engine(tmp_path / "g.db")
    stats = index_repository(FLASK_APP, engine)
    assert stats.symbols > 0

    def cancel_late(phase: str, _done: int, _total: int):
        return False if phase == "writing" else None

    with pytest.raises(IndexCancelledError):
        index_repository(FLASK_APP, engine, on_progress=cancel_late)
    # a cancelled re-index leaves the previous graph intact
    assert _symbol_count(engine) == stats.symbols
