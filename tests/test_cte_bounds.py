"""CTE engine stop-at-sink + LIMIT parity with the memory engine (Phase 2).

Regression: the recursive CTE walked *through* sink nodes and filtered for sinks
only in the outer query, so `source -> sinkA -> sinkB` appeared in SQL results
but never in the memory engine (which stops at the first sink). The fixture's
sinks used to be out-edgeless externals, hiding the divergence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

CHAINED_SINKS = Path(__file__).parent / "fixtures" / "python" / "chained_sinks"
SINKS = ["app.sink_a", "app.sink_b"]
ENGINES = ["memory", "sql"]


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(CHAINED_SINKS, db=db)
    yield g
    g.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_walk_stops_at_first_sink(graph, engine):
    # sink_a reaches sink_b, but a path must stop at the first sink it hits — so
    # the through-sink path [entry, sink_a, sink_b] must NOT appear.
    paths = graph.paths(source="app.entry", sink=SINKS, engine=engine)
    qnames = [[s.qname for s in p.symbols] for p in paths]
    assert qnames == [["app.entry", "app.sink_a"]]


def test_both_engines_agree_with_chained_sinks(graph):
    mem = graph.paths(source="app.entry", sink=SINKS, engine="memory")
    sql = graph.paths(source="app.entry", sink=SINKS, engine="sql")
    assert [[s.qname for s in p.symbols] for p in mem] == [
        [s.qname for s in p.symbols] for p in sql
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_max_paths_is_respected(graph, engine):
    paths = graph.paths(source="app.entry", sink=SINKS, max_paths=1, engine=engine)
    assert len(paths) <= 1
