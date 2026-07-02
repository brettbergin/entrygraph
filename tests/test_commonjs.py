"""End-to-end CommonJS `require()` support (Phase 1.3).

Regression: the JS extractor dropped the `require()` captures entirely, so a
pure-CommonJS app produced no imports — no framework detection and no
canonicalization of `cp.execSync` to a `child_process` sink.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

COMMONJS_APP = Path(__file__).parent / "fixtures" / "javascript" / "commonjs_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(COMMONJS_APP, db=db)
    yield g
    g.close()


def test_commonjs_require_enables_framework_detection(graph):
    names = {f.name for f in graph.detect().frameworks}
    assert "express" in names


def test_commonjs_require_canonicalizes_command_sinks(graph):
    # both the default-bound (cp.execSync) and destructured (exec) forms must
    # resolve to child_process command_exec sinks
    assert graph.stats().sink_edges >= 2
    sinks = {
        row["dst_qname"]
        for row in graph.sql("SELECT dst_qname FROM edges WHERE sink_id IS NOT NULL")
    }
    assert "js:child_process.execSync" in sinks
    assert "js:child_process.exec" in sinks
