"""Parameter rows round-trip: writer insert, (name, location) dedupe, and a full
re-index replacing rows instead of duplicating them.

No built-in rule emits ParameterHints yet (producers land with the Ruby route
work), so these tests register a throwaway rule through the documented
third-party extension point and index a tiny repo.
"""

from __future__ import annotations

import pytest

from entrygraph import CodeGraph
from entrygraph.detect.entrypoints import register
from entrygraph.detect.entrypoints.base import _RULES, EntrypointRule
from entrygraph.extract.ir import EntrypointHint, ParameterHint
from entrygraph.kinds import EntrypointKind


def _hint_with_params(x):
    if not x.path.endswith("app.py"):
        return []
    return [
        EntrypointHint(
            rule_id="test.params.route",
            kind=EntrypointKind.HTTP_ROUTE,
            handler_qualified_name=f"{x.module_path}.show",
            route="/things/:id",
            http_methods=["GET"],
            framework=None,
            parameters=[
                ParameterHint(name="id", location="path", provenance="route", line=1),
                ParameterHint(name="q", location="query", required=False, provenance="usage"),
                # duplicate (name, location) observed twice -> first wins
                ParameterHint(name="id", location="path", provenance="usage"),
            ],
        )
    ]


@pytest.fixture
def params_rule():
    rule = EntrypointRule(
        "test.params.route", "python", None, EntrypointKind.HTTP_ROUTE, _hint_with_params
    )
    register(rule)
    try:
        yield rule
    finally:
        _RULES.remove(rule)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app.py").write_text("def show(thing_id):\n    return thing_id\n")
    return tmp_path


def test_parameter_rows_roundtrip_and_dedupe(params_rule, repo, tmp_path):
    graph = CodeGraph.index(repo, tmp_path / "graph.db")
    (ep,) = graph.entrypoints(kind="http_route")

    assert ep.route == "/things/:id"
    assert [(p.name, p.location) for p in ep.parameters] == [("id", "path"), ("q", "query")]
    id_param, q_param = ep.parameters
    assert (id_param.provenance, id_param.required, id_param.line) == ("route", True, 1)
    assert (q_param.provenance, q_param.required, q_param.line) == ("usage", False, None)


def test_full_reindex_replaces_parameter_rows(params_rule, repo, tmp_path):
    db = tmp_path / "graph.db"
    CodeGraph.index(repo, db)
    graph = CodeGraph.index(repo, db)  # full re-index of the same repo

    (ep,) = graph.entrypoints(kind="http_route")
    assert [(p.name, p.location) for p in ep.parameters] == [("id", "path"), ("q", "query")]
