"""`may_continue` must account for excluded class-hierarchy frontiers (Phase 2).

Regression: a path node whose only additional continuations are CHA edges (which
sit at FUZZY confidence) was not flagged when CHA was off, because the frontier
check only looked at `confidence < floor` or `via == 'dynamic'`. Users were told
the path set was complete when it wasn't.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

MAY_CONTINUE_APP = Path(__file__).parent / "fixtures" / "python" / "may_continue"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(MAY_CONTINUE_APP, db=db)
    yield g
    g.close()


def test_cha_frontier_sets_may_continue(graph):
    # At the unresolved floor the `py:*.run` guess is no longer an excluded
    # frontier, so the ONLY remaining excluded continuation off source_fn is the
    # CHA edge (obj.run). With CHA off it must still flag may_continue.
    paths = graph.paths(
        source="app.source_fn", sink_category="command_exec", include_unresolved=True
    )
    assert paths and paths[0].may_continue is True


def test_including_cha_clears_the_frontier(graph):
    # with CHA traversed, that frontier is no longer excluded; the CHA targets are
    # empty bodies, so nothing else continues -> may_continue is False
    paths = graph.paths(
        source="app.source_fn",
        sink_category="command_exec",
        include_unresolved=True,
        include_fuzzy=True,
    )
    assert paths and paths[0].may_continue is False
