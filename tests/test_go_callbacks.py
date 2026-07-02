"""End-to-end: Go function-value callbacks connect handlers to their registration
site (Phase 4).

Regression: the Go extractor emitted no callback references, so a handler passed
to `http.HandleFunc("/", handler)` had no inbound edge — its body (and any sink it
reached) was unreachable in the call graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

NETHTTP_APP = Path(__file__).parent / "fixtures" / "go" / "nethttp_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(NETHTTP_APP, db=db)
    yield g
    g.close()


def test_callback_edge_created(graph):
    rows = graph.sql(
        "SELECT src.qname AS src, e.dst_qname AS dst FROM edges e "
        "JOIN symbols src ON e.src_symbol_id = src.id WHERE e.kind = 'callback'"
    )
    pairs = {(r["src"], r["dst"]) for r in rows}
    assert ("_root.main", "_root.handler") in pairs


def test_handler_reachable_only_with_callbacks(graph):
    kw = {"source": "_root.main", "sink_category": "command_exec", "include_unresolved": True}
    # without callback traversal the handler is severed from main
    assert graph.paths(**kw) == []
    # with it, main -> handler -> exec.Command resolves
    withcb = graph.paths(**kw, include_callbacks=True)
    assert withcb
    assert withcb[0].symbols[-1].qname == "go:os/exec.Command"
    assert "_root.handler" in [s.qname for s in withcb[0].symbols]
