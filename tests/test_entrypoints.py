from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph
from entrygraph.detect.taint import SinkPattern, SinkRegistry, expand_braces

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(FLASK_APP, db=db)
    yield g
    g.close()


def test_framework_detection_persisted(graph):
    report = graph.detect()
    names = {f.name for f in report.frameworks}
    assert {"flask", "click"} <= names
    flask = next(f for f in report.frameworks if f.name == "flask")
    assert flask.confidence > 0.9  # requirements.txt dep + import
    assert flask.language == "python"


def test_flask_routes_detected(graph):
    routes = graph.entrypoints(kind="http_route", framework="flask")
    by_route = {e.route: e for e in routes}
    assert set(by_route) == {"/users/<user_id>", "/reports", "/health"}
    assert by_route["/reports"].http_method == "GET,POST"
    assert by_route["/reports"].symbol.qname == "app.routes.create_report"


def test_click_command_detected(graph):
    commands = graph.entrypoints(kind="cli_command")
    assert any(e.symbol.qname == "cli.report" for e in commands)


def test_main_guard_detected(graph):
    mains = graph.entrypoints(kind="main")
    assert any(e.symbol.qname == "cli" for e in mains)  # module symbol


def test_route_glob_filter(graph):
    assert {e.route for e in graph.entrypoints(route="/users/*")} == {"/users/<user_id>"}


def test_sink_edges_tagged(graph):
    stats = graph.stats()
    assert stats.sink_edges >= 2  # subprocess.run + cursor.execute
    refs = graph.references("py:subprocess.run")
    assert any(r.sink_id == "py.command-exec.subprocess" for r in refs)


def test_paths_by_sink_category(graph):
    paths = graph.paths(source="app.routes.*", sink_category="command_exec")
    assert paths
    assert paths[0].symbols[-1].qname == "py:subprocess.run"
    # entrypoint objects work as sources too
    route = graph.entrypoints(route="/reports")[0]
    assert graph.reachable(source=route, sink_category="command_exec")
    health = graph.entrypoints(route="/health")[0]
    assert not graph.reachable(source=health, sink_category="command_exec")


def test_sql_sink_matches_unresolved_receiver(graph):
    # cursor.execute(...) in app/db.py is unresolved but still sink-tagged
    edges = graph.references("py:*.execute")
    assert any(e.sink_id == "py.sql-execute" for e in edges)


def test_expand_braces():
    assert set(expand_braces("py:os.{system,popen}")) == {"py:os.system", "py:os.popen"}
    assert set(expand_braces("a{b,c}{d,e}")) == {"abd", "abe", "acd", "ace"}
    assert expand_braces("plain") == ["plain"]


def test_registry_arg_hint():
    registry = SinkRegistry(
        [SinkPattern(id="x", category="sql", callee="py:*.execute", require_arg_hint="%s|format")],
        [],
    )
    assert registry.match("py:*.execute", '("SELECT %s", v)') == "x"
    assert registry.match("py:*.execute", '("SELECT 1")') is None
    assert registry.match("py:*.execute", None) is None
