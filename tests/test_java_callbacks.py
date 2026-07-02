"""End-to-end: Java method-reference callbacks connect handlers to their
registration site (Phase 4).

Regression: the Java extractor emitted no callback references, so a handler passed
as a method reference (`this::handle`) had no inbound edge and was unreachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

METHODREF_APP = Path(__file__).parent / "fixtures" / "java" / "methodref_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(METHODREF_APP, db=db)
    yield g
    g.close()


def test_callback_edge_created(graph):
    rows = graph.sql(
        "SELECT src.qname AS src, e.dst_qname AS dst FROM edges e "
        "JOIN symbols src ON e.src_symbol_id = src.id WHERE e.kind = 'callback'"
    )
    pairs = {(r["src"], r["dst"]) for r in rows}
    assert ("com.example.App.setup", "com.example.App.handle") in pairs


def test_handler_reachable_only_with_callbacks(graph):
    kw = {
        "source": "com.example.App.setup",
        "sink_category": "command_exec",
        "include_unresolved": True,
    }
    assert graph.paths(**kw) == []
    withcb = graph.paths(**kw, include_callbacks=True)
    assert withcb
    assert "com.example.App.handle" in [s.qname for s in withcb[0].symbols]
