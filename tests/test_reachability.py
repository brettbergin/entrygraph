"""Reachability tests, parametrized over both engines to keep them identical."""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"

ENGINES = ["memory", "sql"]


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(FLASK_APP, db=db)
    yield g
    g.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_route_reaches_sink(graph, engine):
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run",
                        engine=engine)
    assert paths
    assert paths[0].symbols[0].qname == "app.routes.create_report"
    assert paths[0].symbols[-1].qname == "py:subprocess.run"


@pytest.mark.parametrize("engine", ENGINES)
def test_unreachable(graph, engine):
    assert graph.paths(source="app.routes.health", sink="py:subprocess.run",
                       engine=engine) == []
    assert not graph.reachable(source="app.routes.health", sink="py:subprocess.run",
                               engine=engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_reachable_true(graph, engine):
    assert graph.reachable(source="app.routes.create_report", sink="py:subprocess.run",
                           engine=engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_max_depth(graph, engine):
    assert not graph.reachable(source="app.routes.create_report", sink="py:subprocess.run",
                               max_depth=1, engine=engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_cycle_terminates(graph, engine):
    # the fixture has a render_and_execute <-> start cycle; enumeration must halt
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run",
                        max_paths=20, engine=engine)
    assert paths
    for path in paths:  # simple paths: no repeated node
        ids = [s.id for s in path.symbols]
        assert len(ids) == len(set(ids))


def test_engines_agree_on_path_set(graph):
    """Both engines must return the same shortest path for the same query."""
    mem = graph.paths(source="app.routes.create_report", sink="py:subprocess.run",
                      engine="memory")
    sql = graph.paths(source="app.routes.create_report", sink="py:subprocess.run",
                      engine="sql")
    assert [s.qname for s in mem[0].symbols] == [s.qname for s in sql[0].symbols]


def test_unknown_engine_raises(graph):
    with pytest.raises(ValueError):
        graph.paths(source="app.routes.*", sink="py:subprocess.run", engine="bogus")


# ---------------- S4: enrichment / risk scoring / confidence gating ----------------

def test_paths_carry_risk_score(graph):
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    assert paths
    assert paths[0].risk_score is not None and paths[0].risk_score > 0


def test_default_floor_excludes_wildcard_sinks_but_flag_includes(graph):
    # find_user -> py:*.execute is an UNRESOLVED wildcard sink (confidence 0)
    default = graph.paths(source="app.db.find_user", sink="py:*.execute")
    opted_in = graph.paths(source="app.db.find_user", sink="py:*.execute",
                           include_unresolved=True)
    assert default == []
    assert opted_in


def test_sink_terminal_edge_carries_sink_id(graph):
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    terminal = paths[0].edges[-1]
    assert terminal.sink_id == "py.command-exec.subprocess"


def test_engines_agree_on_risk_ranked_paths(graph):
    mem = graph.paths(source="app.routes.create_report", sink="py:subprocess.run",
                      engine="memory")
    sql = graph.paths(source="app.routes.create_report", sink="py:subprocess.run",
                      engine="sql")
    assert [round(p.risk_score, 4) for p in mem] == [round(p.risk_score, 4) for p in sql]
