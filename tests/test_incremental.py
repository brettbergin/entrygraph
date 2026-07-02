"""Incremental indexing must produce a graph identical to a full re-index."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.db.engine import make_engine
from entrygraph.db.models import Edge, Symbol
from entrygraph.pipeline.scanner import index_repository

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture
def repo(tmp_path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(FLASK_APP, dst)
    return dst


def _graph_snapshot(engine) -> tuple[set, set]:
    with Session(engine) as s:
        symbols = {(sym.qname, sym.kind.value) for sym in s.execute(select(Symbol)).scalars()}
        edges = set()
        qname_of = {sym.id: sym.qname for sym in s.execute(select(Symbol)).scalars()}
        for e in s.execute(select(Edge)).scalars():
            edges.add(
                (
                    qname_of.get(e.src_symbol_id),
                    e.kind.value,
                    e.dst_qname,
                    e.dst_symbol_id is not None,
                )
            )
    return symbols, edges


def _full_reindex_snapshot(repo: Path, tmp_path) -> tuple[set, set]:
    engine = make_engine(tmp_path / "full.db")
    index_repository(repo, engine)
    snap = _graph_snapshot(engine)
    engine.dispose()
    return snap


def test_no_change_refresh_is_identical(repo, tmp_path):
    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)
    before = _graph_snapshot(engine)
    stats = index_repository(repo, engine, incremental=True)
    after = _graph_snapshot(engine)
    assert stats.files_indexed == 0  # mtime fast-path: nothing reparsed
    assert before == after


def test_no_change_refresh_preserves_detection_confidence(repo, tmp_path):
    # zero-change early-exit must NOT rewrite detections; a full re-detect with
    # empty extractions would drop import-based signals (flask 0.94 -> 0.8).
    from entrygraph import CodeGraph

    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)
    before = {d.name: round(d.confidence, 3) for d in CodeGraph(engine).detect().frameworks}
    index_repository(repo, engine, incremental=True)
    after = {d.name: round(d.confidence, 3) for d in CodeGraph(engine).detect().frameworks}
    assert before == after
    assert before["flask"] > 0.9  # dep + import, not degraded to dep-only
    engine.dispose()


def test_edit_file_matches_full_reindex(repo, tmp_path):
    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)

    # add a new function to services.py that reaches subprocess directly
    services = repo / "app" / "services.py"
    text = services.read_text() + (
        "\n\ndef extra_report(name):\n    import subprocess\n    return subprocess.run([name])\n"
    )
    services.write_text(text)
    import os

    os.utime(services, ns=(0, 10**18))  # bump mtime so the diff notices

    inc_stats = index_repository(repo, engine, incremental=True)
    assert inc_stats.files_indexed == 1
    inc_snapshot = _graph_snapshot(engine)

    full_snapshot = _full_reindex_snapshot(repo, tmp_path)
    assert inc_snapshot == full_snapshot
    engine.dispose()


def test_delete_file_matches_full_reindex(repo, tmp_path):
    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)

    (repo / "app" / "db.py").unlink()
    import os

    os.utime(repo / "app", ns=(0, 10**18))

    inc_stats = index_repository(repo, engine, incremental=True)
    assert inc_stats.files_deleted == 1
    inc_snapshot = _graph_snapshot(engine)

    full_snapshot = _full_reindex_snapshot(repo, tmp_path)
    assert inc_snapshot == full_snapshot
    engine.dispose()


def test_cross_file_edge_heals(repo, tmp_path):
    """An edge into a symbol whose file is edited must re-bind (not stay NULL)."""
    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)

    def resolved_call(src, dst) -> bool:
        with Session(engine) as s:
            qname_to_id = {sym.qname: sym.id for sym in s.execute(select(Symbol)).scalars()}
            if src not in qname_to_id or dst not in qname_to_id:
                return False
            return bool(
                s.execute(
                    select(Edge).where(
                        Edge.src_symbol_id == qname_to_id[src],
                        Edge.dst_symbol_id == qname_to_id[dst],
                    )
                ).first()
            )

    assert resolved_call("app.routes.create_report", "app.services.run_report")

    # touch services.py (its symbols get new ids); the inbound edge from
    # routes.py must be healed back to the new run_report id.
    services = repo / "app" / "services.py"
    services.write_text(services.read_text() + "\n# touch\n")
    import os

    os.utime(services, ns=(0, 10**18))

    index_repository(repo, engine, incremental=True)
    assert resolved_call("app.routes.create_report", "app.services.run_report")
    engine.dispose()


def test_parallel_matches_sequential(repo, tmp_path):
    seq = make_engine(tmp_path / "seq.db")
    index_repository(repo, seq, max_workers=1)
    par = make_engine(tmp_path / "par.db")
    index_repository(repo, par, max_workers=4)
    # force the pool path regardless of file count
    from entrygraph.pipeline import scanner

    original = scanner._PARALLEL_THRESHOLD
    scanner._PARALLEL_THRESHOLD = 1
    try:
        forced = make_engine(tmp_path / "forced.db")
        index_repository(repo, forced, max_workers=4)
        assert _graph_snapshot(forced) == _graph_snapshot(seq)
        forced.dispose()
    finally:
        scanner._PARALLEL_THRESHOLD = original
    seq.dispose()
    par.dispose()
