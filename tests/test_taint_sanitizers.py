"""Sanitizer detection over sibling call edges (Phase 1.2).

Regression: builtin sanitizers (external qnames like `py:shlex.quote`) could
never fire — detection only looked at nodes on the source->sink path, but a
sanitizer is a *sibling* call of an on-path function. And a heuristic match must
only discount risk, never drive it to zero (which would hide a real path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

SANITIZER_APP = Path(__file__).parent / "fixtures" / "python" / "sanitizer"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(SANITIZER_APP, db=db)
    yield g
    g.close()


def _path(graph, source):
    paths = graph.paths(source=source, sink_category="command_exec")
    assert paths, f"expected a command_exec path from {source}"
    return paths[0]


def test_sibling_sanitizer_is_detected(graph):
    # shlex.quote is a sibling call of app.sanitized, not a node on the path
    path = _path(graph, "app.sanitized")
    assert any("py.sanitize.shlex" in e.sanitized_by for e in path.edges)


def test_unsanitized_path_has_no_sanitizer(graph):
    path = _path(graph, "app.unsanitized")
    assert all(not e.sanitized_by for e in path.edges)


def test_sanitizer_discounts_but_does_not_zero_risk(graph):
    # the sanitized path must still carry meaningful (nonzero) risk, and be
    # strictly lower-risk than the identical unsanitized path
    sanitized = _path(graph, "app.sanitized")
    unsanitized = _path(graph, "app.unsanitized")
    assert 0.0 < sanitized.risk_score < unsanitized.risk_score


def test_prune_sanitized_drops_only_sanitized_paths(graph):
    kept = graph.paths(source="app.*", sink_category="command_exec", prune_sanitized=True)
    sources = {p.symbols[0].qname for p in kept}
    assert "app.unsanitized" in sources
    assert "app.sanitized" not in sources
