"""End-to-end same-function reaching verification through the graph (#96 Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

APP = Path(__file__).parent / "fixtures" / "python" / "reaching_defs"


@pytest.fixture
def graph(tmp_path):
    g = CodeGraph.index(APP, db=tmp_path / "g.db")
    yield g
    g.close()


def test_confirmed_and_refuted_verdicts(graph):
    paths = graph.paths(source_category="http_input", sink_category="command_exec")
    by_head = {p.symbols[0].qname: p for p in paths}
    assert by_head["app.confirmed_handler"].taint_verified is True
    assert by_head["app.refuted_handler"].taint_verified is False


def test_refuted_is_demoted_below_confirmed(graph):
    paths = graph.paths(source_category="http_input", sink_category="command_exec")
    by_head = {p.symbols[0].qname: p for p in paths}
    assert by_head["app.refuted_handler"].risk_score < by_head["app.confirmed_handler"].risk_score


def test_confirmed_only_drops_refuted(graph):
    paths = graph.paths(
        source_category="http_input", sink_category="command_exec", confirmed_only=True
    )
    heads = {p.symbols[0].qname for p in paths}
    assert "app.confirmed_handler" in heads
    assert "app.refuted_handler" not in heads


def test_staleness_guard_disables_verification(graph, tmp_path):
    # mutate the file after indexing: the content-hash guard makes the verdict None
    # (unknown), so nothing is wrongly demoted against stale facts.
    (APP / "app.py")  # unchanged on disk; instead point the repo hash at a mismatch
    # simulate by editing the on-disk file the index recorded
    target = APP / "app.py"
    original = target.read_text()
    try:
        target.write_text(original + "\n# touched after index\n")
        paths = graph.paths(source_category="http_input", sink_category="command_exec")
        # refuted handler is no longer provably refuted; verdict falls back to None
        by_head = {p.symbols[0].qname: p for p in paths}
        assert by_head["app.refuted_handler"].taint_verified is None
    finally:
        target.write_text(original)


def test_multi_hop_path_is_unverified(tmp_path):
    # source and sink in different functions -> same-function check doesn't apply
    flask_app = Path(__file__).parent / "fixtures" / "python" / "flask_app"
    g = CodeGraph.index(flask_app, db=tmp_path / "f.db")
    try:
        paths = g.paths(source="app.routes.*", sink_category="command_exec")
        multi = [p for p in paths if len(p.symbols) > 2]
        assert multi  # the flask fixture has multi-hop chains
        assert all(p.taint_verified is None for p in multi)
    finally:
        g.close()


@pytest.mark.slow
def test_verification_bounded_parse_cost(tmp_path):
    # the verifier re-parses at most one file per distinct candidate finding and
    # memoizes per file; a repeated query on a small repo must stay well-bounded.
    import time

    g = CodeGraph.index(APP, db=tmp_path / "perf.db")
    try:
        start = time.monotonic()
        for _ in range(20):
            g.paths(source_category="http_input", sink_category="command_exec")
        elapsed = time.monotonic() - start
        assert elapsed < 10.0, f"20 verified queries took {elapsed:.1f}s"
    finally:
        g.close()
