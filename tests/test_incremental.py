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


def test_modified_file_counts_as_indexed_not_deleted(repo, tmp_path):
    # A content change to one file must be reported under files_indexed, not
    # files_deleted (which used to count reparsed files too) (#46).
    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)
    target = repo / "app" / "routes.py"
    target.write_text(target.read_text() + "\n# touched\n")
    stats = index_repository(repo, engine, incremental=True)
    assert stats.files_indexed == 1
    assert stats.files_deleted == 0
    engine.dispose()


def test_removed_file_counts_as_deleted(repo, tmp_path):
    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)
    (repo / "cli.py").unlink()
    stats = index_repository(repo, engine, incremental=True)
    assert stats.files_deleted == 1
    assert stats.files_indexed == 0  # nothing else changed
    engine.dispose()


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


def test_worker_hashes_enable_paranoid_no_change(repo, tmp_path):
    # the parse worker now computes each file's content_hash (instead of the diff
    # phase reading the file a second time). A paranoid refresh re-hashes every
    # file and compares to the stored hash; it must find nothing changed, proving
    # the worker-supplied hashes were stored correctly (not empty/placeholder).
    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)
    stats = index_repository(repo, engine, incremental=True, paranoid=True)
    assert stats.files_indexed == 0
    engine.dispose()


def test_streaming_edge_writes_across_batches(tmp_path, monkeypatch):
    # force many small flushes so the edge/entrypoint writers cross batch
    # boundaries; the resulting graph must be identical to a single bulk insert.
    import entrygraph.pipeline.writer as writer

    monkeypatch.setattr(writer, "_BATCH", 3)
    engine = make_engine(tmp_path / "stream.db")
    stats = index_repository(FLASK_APP, engine)
    assert stats.edges > 3  # spans multiple batches

    from entrygraph import CodeGraph

    g = CodeGraph(engine)
    # edge count persisted matches what the writer reported, and reachability holds
    assert g.stats().edges == stats.edges
    assert g.reachable(source="app.routes.create_report", sink="py:subprocess.run")
    engine.dispose()


def test_worker_hashes_predicate():
    # only supported+unskipped files are hashed by the worker; everything else the
    # worker never reads is hashed in the diff phase.
    from entrygraph.fs.hashing import _worker_hashes
    from entrygraph.fs.walker import WalkedFile

    def wf(language, skip=None):
        return WalkedFile(
            path="x", abs_path="/x", language=language, size_bytes=1, mtime_ns=0, skip_reason=skip
        )

    assert _worker_hashes(wf("python")) is True
    assert _worker_hashes(wf("python", "binary")) is False  # skipped -> worker won't read
    assert _worker_hashes(wf("markdown")) is False  # recognized for stats, not extracted
    assert _worker_hashes(wf(None)) is False


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


def test_reindex_shared_package_file_matches_full(tmp_path):
    # Two files in the same Go package share a module_path and both define
    # `func init()`. Re-indexing one file must not cascade-delete the other file's
    # symbols/edges (parent_id and edge.src_symbol_id FKs are ondelete=CASCADE, and
    # both were bound to the last-written colliding symbol). Regression for #41 F-H11.
    repo = tmp_path / "gomod"
    (repo / "middleware").mkdir(parents=True)
    (repo / "middleware" / "a.go").write_text(
        'package middleware\nimport "fmt"\nfunc init() { doThing() }\n'
        'func doThing() { fmt.Println("a") }\n'
    )
    terminal = repo / "middleware" / "terminal.go"
    terminal.write_text(
        'package middleware\nimport "fmt"\nfunc init() { other() }\n'
        'func other() { fmt.Println("b") }\n'
    )
    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)

    terminal.write_text(terminal.read_text() + "\n// touch\n")
    stats = index_repository(repo, engine, incremental=True)
    assert stats.files_indexed == 1  # only terminal.go reparsed
    incremental = _graph_snapshot(engine)
    engine.dispose()

    assert incremental == _full_reindex_snapshot(repo, tmp_path)
    # the sibling file's symbols/edges survive the re-index
    syms = {q for q, _ in incremental[0]}
    assert {"middleware.doThing", "middleware.other"} <= syms
