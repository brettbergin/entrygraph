"""SDL extraction end-to-end: .graphql files parse, root fields become entrypoints."""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

SCHEMA_APP = Path(__file__).parent / "fixtures" / "graphql" / "schema_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(SCHEMA_APP, db=db)
    yield g
    g.close()


def test_root_fields_become_entrypoints(graph):
    eps = graph.entrypoints(kind="graphql_resolver")
    by_route = {e.route: e for e in eps}
    assert {"Query.user", "Query.posts", "Query.search", "Mutation.createUser"} <= set(by_route)
    ep = by_route["Query.user"]
    assert ep.framework == "graphql-sdl"
    assert ep.http_method is None
    # bound to the SDL field symbol, not the coarse module fallback
    assert ep.symbol.qname == "schema.schema_graphql.Query.user"


def test_extend_type_contributes_root_fields(graph):
    eps = graph.entrypoints(kind="graphql_resolver", route="Query.search")
    assert len(eps) == 1
    assert eps[0].symbol.qname == "schema.schema_graphql.Query.search"


def test_custom_schema_roots_detected(graph):
    eps = graph.entrypoints(kind="graphql_resolver", route="RootQuery.ping")
    assert len(eps) == 1
    assert eps[0].symbol.qname == "schema.custom_roots_gql.RootQuery.ping"


def test_non_root_type_fields_are_symbols_not_entrypoints(graph):
    routes = {e.route for e in graph.entrypoints(kind="graphql_resolver")}
    assert "User.posts" not in routes
    assert "Post.author" not in routes
    # ... but the fields exist as symbols
    syms = graph.symbols(qname="schema.schema_graphql.User.posts")
    assert len(syms) == 1
    assert syms[0].kind == "field"


def test_type_definitions_are_class_symbols(graph):
    syms = graph.symbols(qname="schema.schema_graphql.Query")
    assert len(syms) == 1
    assert syms[0].kind == "class"


def test_sdl_resolver_is_http_input_source(graph):
    # graphql_resolver entrypoints seed the http_input source category; with no
    # code resolvers in this fixture there is nothing to reach, but resolution
    # must not error and must include the resolver symbols as sources.
    paths = graph.paths(source_category="http_input", sink_category="command_exec")
    assert paths == []
