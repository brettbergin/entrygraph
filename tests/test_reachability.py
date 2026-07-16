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
def test_wildcard_source_emits_no_degenerate_self_paths(graph, engine):
    # `--source '*'` matches the sink symbol itself; that used to yield a length-1
    # path (just the sink), which out-ranked real chains (#47). Every emitted path
    # must have >= 2 nodes, and a real multi-hop chain must still appear.
    paths = graph.paths(source="*", sink="py:subprocess.run", engine=engine)
    assert paths
    assert all(len(p.symbols) >= 2 for p in paths)
    assert all(p.symbols[0].qname != p.symbols[-1].qname or len(p.symbols) > 1 for p in paths)
    # an internal sink that '*' also matches as a source: no [(self)] length-1 path
    self_paths = graph.paths(source="*", sink="app.services.run_report", engine=engine)
    assert all(len(p.symbols) >= 2 for p in self_paths)


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


# ---------------- S4: enrichment / fact ranking / confidence gating ----------------


def test_paths_carry_severity_fact(graph):
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    assert paths
    # the tagged sink's catalog severity rides the path as a displayed fact
    assert paths[0].severity in ("critical", "high", "medium", "low")


def test_adaptive_search_widens_to_wildcard_sinks_strict_excludes(graph):
    # find_user -> py:*.execute is an UNRESOLVED wildcard sink (confidence 0). The
    # precise pass excludes it, so --strict returns nothing; the adaptive default
    # finds no high-confidence path and widens to reach it (mode "widened"); an
    # explicit --include-unresolved reaches it directly.
    strict = graph.paths(source="app.db.find_user", sink="py:*.execute", strict=True)
    default = graph.paths(source="app.db.find_user", sink="py:*.execute")
    opted_in = graph.paths(source="app.db.find_user", sink="py:*.execute", include_unresolved=True)
    assert strict == []
    assert strict.mode == "strict"
    assert default and default.mode == "widened"
    assert opted_in and opted_in.mode == "explicit"


def test_adaptive_search_stays_precise_when_high_confidence_path_exists(graph):
    # create_report -> subprocess.run resolves at IMPORT confidence, so the adaptive
    # search returns the precise result without widening.
    out = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    assert out and out.mode == "precise"


def test_sink_terminal_edge_carries_sink_id(graph):
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    terminal = paths[0].edges[-1]
    assert terminal.sink_id == "py.command-exec.subprocess"


def test_engines_agree_on_ranked_paths(graph):
    mem = graph.paths(source="app.routes.create_report", sink="py:subprocess.run", engine="memory")
    sql = graph.paths(source="app.routes.create_report", sink="py:subprocess.run", engine="sql")
    key = lambda p: (p.severity, p.min_confidence, [s.qname for s in p.symbols])  # noqa: E731
    assert [key(p) for p in mem] == [key(p) for p in sql]


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


def test_category_path_requires_a_sink_tagged_terminal_edge(tmp_path):
    # createHash('md5') is weak_crypto; createHash('sha256') is not. Both call the
    # same crypto.createHash symbol, so a path must end at the *tagged* edge — the
    # sha256 call must not be reported as weak_crypto (#34 / F-H15).
    (tmp_path / "a.js").write_text(
        'const crypto = require("crypto");\n'
        'function weak(x) { return crypto.createHash("md5").update(x).digest("hex"); }\n'
        'function strong(x) { return crypto.createHash("sha256").update(x).digest("hex"); }\n'
    )
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    paths = graph.paths(source="*", sink_category="weak_crypto")
    graph.close()
    chains = [[s.qname for s in p.symbols] for p in paths]
    assert ["a.weak", "js:crypto.createHash"] in chains  # md5 kept
    assert all("a.strong" not in c for c in chains)  # sha256 dropped
    assert all(len(c) >= 2 for c in chains)  # no degenerate single-node paths


def test_route_handler_passed_by_reference_binds_to_the_handler(tmp_path):
    # router.get('/x', ctrl.fn) binds to the module, but the resolver emits a
    # callback edge at the registration line — the route must bind to that real
    # handler so ctrl.fn (not the whole module) is the http_input source, and the
    # taint path resolves WITHOUT --include-callbacks (#34, call-based binding).
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"name":"app","dependencies":{"express":"^4"}}')
    (src / "controller.js").write_text(
        'const { exec } = require("child_process");\n'
        "function getUser(req, res) { exec('lookup ' + req.body.id); }\n"
        "module.exports = { getUser };\n"
    )
    (src / "routes.js").write_text(
        'const { Router } = require("express");\n'
        'const ctrl = require("./controller");\n'
        "const router = Router();\n"
        'router.get("/user", ctrl.getUser);\n'
        "module.exports = router;\n"
    )
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    # the route binds to the handler function, not the routes module
    eps = graph.entrypoints(kind="http_route")
    assert eps and eps[0].symbol.qname == "controller.getUser"
    # and the taint path resolves by default (no include_callbacks)
    paths = graph.paths(source_category="http_input", sink_category="command_exec")
    graph.close()
    chains = [[s.qname for s in p.symbols] for p in paths]
    assert ["controller.getUser", "js:child_process.exec"] in chains


def test_unknown_category_raises_with_valid_set(graph):
    # an unknown category otherwise resolves to an empty pattern set and silently
    # returns zero paths — indistinguishable from "no reachable sinks"
    from entrygraph.errors import UnknownCategoryError

    with pytest.raises(UnknownCategoryError, match="command_exec"):
        graph.paths(source_category="http_input", sink_category="command_injection")
    with pytest.raises(UnknownCategoryError, match="http_input"):
        graph.paths(source_category="cli_command", sink_category="command_exec")
    # 'all' and real categories are accepted
    assert graph.paths(source_category="http_input", sink_category="all") is not None


def test_bare_sink_name_resolves_without_language_prefix(graph):
    # sink symbols carry a language prefix (py:subprocess.run); a bare name should
    # still resolve so users needn't know the convention
    prefixed = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    bare = graph.paths(source="app.routes.create_report", sink="subprocess.run")
    assert bare and len(bare) == len(prefixed)


@pytest.mark.parametrize("engine", ENGINES)
def test_reachable_and_paths_agree_on_category_terminal(tmp_path, engine):
    # reachable() must apply the same tagged-terminal-edge filter as paths(): a
    # handler that only calls the sink symbol with a constant (untagged edge) is
    # not a command_exec reach (Bug 4 — the two used to disagree).
    (tmp_path / "app.py").write_text(
        "import subprocess\n"
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        '@app.route("/c")\n'
        "def c():\n"
        '    subprocess.run("ls", shell=True)\n'  # constant arg -> untagged
    )
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    try:
        paths = graph.paths(
            source_category="http_input", sink_category="command_exec", engine=engine
        )
        reach = graph.reachable(
            source_category="http_input", sink_category="command_exec", engine=engine
        )
        assert bool(paths) == reach
    finally:
        graph.close()


def test_category_finding_survives_untagged_crowding(tmp_path):
    # a real tainted path must not be crowded out of the candidate pool by many
    # benign (untagged, constant-arg) arrivals at the same shared sink symbol
    # (Bug 3). The terminal-edge filter runs inside the traversal, not after.
    lines = ["import subprocess", "from flask import Flask, request", "app = Flask(__name__)"]
    for i in range(80):
        lines += [f'@app.route("/b{i}")', f"def b{i}():", '    subprocess.run("ls", shell=True)']
    lines += [
        '@app.route("/vuln")',
        "def vuln():",
        '    cmd = request.args.get("cmd")',
        "    subprocess.run(cmd, shell=True)",
    ]
    (tmp_path / "app.py").write_text("\n".join(lines) + "\n")
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    try:
        paths = graph.paths(
            source_category="http_input", sink_category="command_exec", max_paths=5
        )
        heads = [p.symbols[0].qname for p in paths]
        assert "app.vuln" in heads
        # the confirmed flow ranks first (confirmed > unchecked > refuted)
        assert heads[0] == "app.vuln"
    finally:
        graph.close()


def test_sink_category_all_unions_every_tagged_sink(graph):
    # `all` is a meta-category: any tagged sink of any category. It must include the
    # command_exec path that `--sink-category command_exec` finds, and be a superset.
    cmd = {
        tuple(s.qname for s in p.symbols)
        for p in graph.paths(source_category="http_input", sink_category="command_exec")
    }
    allp = {
        tuple(s.qname for s in p.symbols)
        for p in graph.paths(source_category="http_input", sink_category="all")
    }
    assert cmd and cmd <= allp
    assert any(p and p[-1] == "py:subprocess.run" for p in allp)
