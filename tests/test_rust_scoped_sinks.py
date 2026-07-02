"""Fully-scoped Rust calls stamp their sink (Phase 4 follow-up).

Regression: a `::`-scoped call like `std::process::Command::new(...)` resolved to
`rs:*.new` (receiver collapsed to a `*.name` guess), so the command_exec sink
pattern `rs:{std.process.Command.new,...}` never matched. Scoped paths are now
kept whole, so the sink stamps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

SCOPED_SINK_APP = Path(__file__).parent / "fixtures" / "rust" / "scoped_sink_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(SCOPED_SINK_APP, db=db)
    yield g
    g.close()


def test_scoped_command_sink_stamped(graph):
    sinks = {
        r["dst_qname"]: r["sink_id"]
        for r in graph.sql("SELECT dst_qname, sink_id FROM edges WHERE sink_id IS NOT NULL")
    }
    assert sinks.get("rs:std.process.Command.new") == "rust.command-exec"
    assert sinks.get("rs:std.fs.read") == "rust.path-traversal"


def test_scoped_command_sink_reachable(graph):
    # the sink is an external guess (UNRESOLVED tier), so opt into it
    paths = graph.paths(
        source="_root.run_inline", sink_category="command_exec", include_unresolved=True
    )
    assert paths
    assert paths[0].symbols[-1].qname == "rs:std.process.Command.new"
