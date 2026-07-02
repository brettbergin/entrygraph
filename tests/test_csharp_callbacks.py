"""End-to-end: C# method-group callbacks connect minimal-API handlers to their
registration site (Phase 4).

Regression: the C# extractor emitted no callback references, so a handler passed
to `app.MapPost("/run", Handler)` had no inbound edge and was unreachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

MINIMALAPI_APP = Path(__file__).parent / "fixtures" / "csharp" / "minimalapi_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(MINIMALAPI_APP, db=db)
    yield g
    g.close()


def test_callback_edge_created(graph):
    rows = graph.sql(
        "SELECT src.qname AS src, e.dst_qname AS dst FROM edges e "
        "JOIN symbols src ON e.src_symbol_id = src.id WHERE e.kind = 'callback'"
    )
    pairs = {(r["src"], r["dst"]) for r in rows}
    assert ("Program.Program.Main", "Program.Program.Handler") in pairs


def test_handler_reachable_only_with_callbacks(graph):
    kw = {
        "source": "Program.Program.Main",
        "sink_category": "command_exec",
        "include_unresolved": True,
    }
    assert graph.paths(**kw) == []
    withcb = graph.paths(**kw, include_callbacks=True)
    assert withcb
    assert "Program.Program.Handler" in [s.qname for s in withcb[0].symbols]
    assert withcb[0].symbols[-1].qname == "cs:System.Diagnostics.Process.Start"
