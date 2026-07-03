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
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run", engine=engine)
    assert paths
    assert paths[0].symbols[0].qname == "app.routes.create_report"
    assert paths[0].symbols[-1].qname == "py:subprocess.run"


@pytest.mark.parametrize("engine", ENGINES)
def test_unreachable(graph, engine):
    assert graph.paths(source="app.routes.health", sink="py:subprocess.run", engine=engine) == []
    assert not graph.reachable(source="app.routes.health", sink="py:subprocess.run", engine=engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_reachable_true(graph, engine):
    assert graph.reachable(
        source="app.routes.create_report", sink="py:subprocess.run", engine=engine
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_max_depth(graph, engine):
    assert not graph.reachable(
        source="app.routes.create_report", sink="py:subprocess.run", max_depth=1, engine=engine
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cycle_terminates(graph, engine):
    # the fixture has a render_and_execute <-> start cycle; enumeration must halt
    paths = graph.paths(
        source="app.routes.create_report", sink="py:subprocess.run", max_paths=20, engine=engine
    )
    assert paths
    for path in paths:  # simple paths: no repeated node
        ids = [s.id for s in path.symbols]
        assert len(ids) == len(set(ids))


def test_engines_agree_on_path_set(graph):
    """Both engines must return the same shortest path for the same query."""
    mem = graph.paths(source="app.routes.create_report", sink="py:subprocess.run", engine="memory")
    sql = graph.paths(source="app.routes.create_report", sink="py:subprocess.run", engine="sql")
    assert [s.qname for s in mem[0].symbols] == [s.qname for s in sql[0].symbols]


def test_unknown_engine_raises(graph):
    with pytest.raises(ValueError):
        graph.paths(source="app.routes.*", sink="py:subprocess.run", engine="bogus")


def test_shared_adjacency_cache_across_configs(graph):
    # different confidence-floor / CHA combinations must reuse one cache, not
    # build a full duplicate graph per combination (memory dedup).
    graph._adjacency.clear()
    kw = {"source": "app.routes.create_report", "sink": "py:subprocess.run"}
    graph.paths(**kw)
    graph.paths(**kw, include_fuzzy=True)
    graph.paths(**kw, include_unresolved=True)
    graph.reachable(**kw, include_unresolved=True)
    assert len(graph._adjacency) == 1


# ---------------- S4: enrichment / risk scoring / confidence gating ----------------


def test_paths_carry_risk_score(graph):
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    assert paths
    assert paths[0].risk_score is not None and paths[0].risk_score > 0


def test_default_floor_excludes_wildcard_sinks_but_flag_includes(graph):
    # find_user -> py:*.execute is an UNRESOLVED wildcard sink (confidence 0)
    default = graph.paths(source="app.db.find_user", sink="py:*.execute")
    opted_in = graph.paths(source="app.db.find_user", sink="py:*.execute", include_unresolved=True)
    assert default == []
    assert opted_in


def test_sink_terminal_edge_carries_sink_id(graph):
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    terminal = paths[0].edges[-1]
    assert terminal.sink_id == "py.command-exec.subprocess"


def test_engines_agree_on_risk_ranked_paths(graph):
    mem = graph.paths(source="app.routes.create_report", sink="py:subprocess.run", engine="memory")
    sql = graph.paths(source="app.routes.create_report", sink="py:subprocess.run", engine="sql")
    assert [round(p.risk_score, 4) for p in mem] == [round(p.risk_score, 4) for p in sql]


def test_widen_flags_are_monotonic(graph):
    # widening the edge frontier must yield a superset of the base paths, never
    # drop them (regression: max_paths truncation during DFS made widening
    # return a different, sometimes smaller, slice).
    def sig(paths):
        return {tuple(s.qname for s in p.symbols) for p in paths}

    base = graph.paths(source="*", sink_category="command_exec", max_paths=50)
    for flag in ("include_fuzzy", "include_unresolved", "include_callbacks"):
        wide = graph.paths(source="*", sink_category="command_exec", max_paths=50, **{flag: True})
        assert sig(base) <= sig(wide), f"{flag} dropped base paths"


def test_paths_result_carries_truncated_flag(graph):
    # a normal, complete search is not flagged truncated
    paths = graph.paths(source="*", sink_category="command_exec")
    assert getattr(paths, "truncated", None) is False


def test_dfs_reports_truncation_when_budget_is_spent(monkeypatch):
    from entrygraph.graph import adjacency
    from entrygraph.graph.adjacency import AdjacencyCache, Hop

    # force a budget of 1 so a multi-hop search cannot finish
    monkeypatch.setattr(adjacency, "_MIN_DFS_VISITS", 1)
    monkeypatch.setattr(adjacency, "_DFS_VISIT_FACTOR", 0)
    cache = AdjacencyCache(1, frozenset({"calls"}))
    # chain 1 -> 2 -> 3 (3 is the sink), needs >1 visit to reach
    cache.forward = {1: [Hop(2, "calls", 1, 3)], 2: [Hop(3, "calls", 2, 3)]}
    result = cache.paths({1}, {3}, max_paths=10)
    assert result == []  # budget spent before reaching the sink
    assert result.truncated is True


def test_http_route_handler_is_an_http_input_source(tmp_path):
    # Express reads request data as a property (`req.body`), not a catalog-matched
    # call, so it produces no source edge — the handler itself must count as an
    # http_input source or the app can never yield a taint path (#34 / F-H9).
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"name":"app","dependencies":{"express":"^4"}}')
    (src / "app.js").write_text(
        'const express = require("express");\n'
        'const { exec } = require("child_process");\n'
        "const app = express();\n"
        "function runReport(req, res) {\n"
        "  const name = req.body.name;\n"  # property-read source (not a call)
        '  exec("report " + name);\n'  # command_exec sink
        "}\n"
        'app.post("/reports", runReport);\n'
    )
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    paths = graph.paths(source_category="http_input", sink_category="command_exec")
    graph.close()
    chains = [[s.qname for s in p.symbols] for p in paths]
    assert ["app.runReport", "js:child_process.exec"] in chains
