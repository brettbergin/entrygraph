"""Source provenance: explicit accessor read vs handler-as-source (#96 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

APP = Path(__file__).parent / "fixtures" / "python" / "source_split"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "g.db"
    g = CodeGraph.index(APP, db=db)
    yield g
    g.close()


def test_both_handlers_found_by_default(graph):
    paths = graph.paths(source_category="http_input", sink_category="command_exec")
    heads = {p.symbols[0].qname for p in paths}
    assert "app.explicit_handler" in heads
    assert "app.implicit_handler" in heads


def test_source_kind_classified(graph):
    paths = graph.paths(source_category="http_input", sink_category="command_exec")
    by_head = {p.symbols[0].qname: p for p in paths}
    assert by_head["app.explicit_handler"].source_kind == "explicit"
    # the implicit handler has request-shaped params -> handler_params tier
    assert by_head["app.implicit_handler"].source_kind in ("handler", "handler_params")


def test_explicit_sources_flag_drops_implicit(graph):
    paths = graph.paths(
        source_category="http_input", sink_category="command_exec", explicit_sources=True
    )
    heads = {p.symbols[0].qname for p in paths}
    assert "app.explicit_handler" in heads
    assert "app.implicit_handler" not in heads
