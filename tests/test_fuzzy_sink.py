"""Sink preservation vs unique-name fuzzy binding (Phase 1.4).

Regression: an unknown-receiver call like `cursor.execute(sql)` fuzzy-bound to a
unique project method named `execute`, rewriting dst_qname so the `py:*.execute`
SQL sink never got stamped — the vulnerability silently vanished from the graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

FUZZY_SINK_APP = Path(__file__).parent / "fixtures" / "python" / "fuzzy_sink"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(FUZZY_SINK_APP, db=db)
    yield g
    g.close()


def test_sql_sink_survives_unique_name_collision(graph):
    sinks = graph.sql("SELECT dst_qname, sink_id FROM edges WHERE sink_id IS NOT NULL")
    by_qname = {r["dst_qname"]: r["sink_id"] for r in sinks}
    assert by_qname.get("py:*.execute") == "py.sql-execute"


def test_sql_path_reachable_despite_collision(graph):
    paths = graph.paths(source="app.query", sink_category="sql", include_unresolved=True)
    assert paths
    assert paths[0].symbols[-1].qname == "py:*.execute"
