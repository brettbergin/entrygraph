"""End-to-end taint-source catalog wiring (Phase 1.1).

Regression: `[[source]]` catalog entries used to be dead configuration — parsed
but never matched or stamped, so `paths(source_category=...)` was impossible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

TAINT_SOURCE_APP = Path(__file__).parent / "fixtures" / "python" / "taint_source"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(TAINT_SOURCE_APP, db=db)
    yield g
    g.close()


def test_source_edges_stamped(graph):
    # os.getenv call in handler() is stamped as a taint source
    assert graph.stats().source_edges >= 1


def test_paths_from_source_category_to_sink_category(graph):
    # env_input source (os.getenv) reaches a command_exec sink (os.system)
    paths = graph.paths(source_category="env_input", sink_category="command_exec")
    assert paths
    qnames = [s.qname for s in paths[0].symbols]
    assert qnames[0] == "handler.handler"
    assert qnames[-1] == "py:os.system"


def test_reachable_from_source_category(graph):
    assert graph.reachable(source_category="env_input", sink_category="command_exec")
    # a category with no matching source in this repo yields no reach
    assert not graph.reachable(source_category="http_input", sink_category="command_exec")


def test_catalog_source_marks_origin_tainted(graph):
    # the source-tainted risk factor should apply (handler calls os.getenv), so
    # the path risk exceeds the untainted-source baseline discount
    path = graph.paths(source_category="env_input", sink_category="command_exec")[0]
    assert path.risk_score and path.risk_score > 0.5


CLI_APP = Path(__file__).parent / "fixtures" / "python" / "cli_app"


@pytest.fixture(scope="module")
def cli_graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "cli.db"
    g = CodeGraph.index(CLI_APP, db=db)
    yield g
    g.close()


def test_cli_command_handler_is_a_cli_arg_source(cli_graph):
    # click-decorated handler (CLI_COMMAND entrypoint) reaches a command sink;
    # its args are injected as params, so only handler-as-source can see it (#86)
    paths = cli_graph.paths(source_category="cli_arg", sink_category="command_exec")
    chains = {tuple(s.qname for s in p.symbols) for p in paths}
    assert ("cli.deploy", "cli.run_deploy", "py:subprocess.run") in chains


def test_argparse_accessor_is_a_cli_arg_source(cli_graph):
    # parse_args() is a catalog cli_arg accessor -> tool.main is a source
    paths = cli_graph.paths(source_category="cli_arg", sink_category="command_exec")
    chains = {tuple(s.qname for s in p.symbols) for p in paths}
    assert ("tool.main", "py:subprocess.run") in chains


def test_cli_args_are_not_env_input(cli_graph):
    # the old workaround category must NOT cover argv (#86 repro)
    assert cli_graph.paths(source_category="env_input", sink_category="command_exec") == []
    assert not cli_graph.reachable(source_category="env_input", sink_category="command_exec")


def test_cli_arg_category_is_registered():
    from entrygraph.detect.taint import builtin_registry

    r = builtin_registry()
    ids = r.source_ids_for_category("cli_arg")
    assert {"py.cli-args", "go.cli-flags", "rust.cli-args"} <= ids
    # rust argv reclassified: cli_arg now, env_input no longer (#86)
    assert r.match_source("rs:std.env.args") == "rust.cli-args"
    assert r.match_source("rs:std.env.var") == "rust.env"
    assert "rust.cli-args" not in r.source_ids_for_category("env_input")
