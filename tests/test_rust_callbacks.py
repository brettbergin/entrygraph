"""End-to-end: Rust function-value callbacks connect handlers to their route
registration (Phase 4).

Regression: the Rust extractor emitted no callback references, so a handler passed
to `post(handler)` had no inbound edge and was unreachable in the call graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

AXUM_APP = Path(__file__).parent / "fixtures" / "rust" / "axum_callback_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(AXUM_APP, db=db)
    yield g
    g.close()


def test_callback_edge_created(graph):
    rows = graph.sql(
        "SELECT src.qname AS src, e.dst_qname AS dst FROM edges e "
        "JOIN symbols src ON e.src_symbol_id = src.id WHERE e.kind = 'callback'"
    )
    pairs = {(r["src"], r["dst"]) for r in rows}
    assert ("_root.register", "_root.handler") in pairs


def test_handler_reachable_only_with_callbacks(graph):
    # the callback edge is what makes the handler (and the command_exec sink in its
    # body) reachable from the route registration site
    kw = {"source": "_root.register", "sink_category": "command_exec", "include_unresolved": True}
    assert graph.paths(**kw) == []
    withcb = graph.paths(**kw, include_callbacks=True)
    assert withcb
    assert "_root.handler" in [s.qname for s in withcb[0].symbols]
    assert withcb[0].symbols[-1].qname == "rs:std.process.Command.new"
