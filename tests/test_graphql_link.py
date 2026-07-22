"""Cross-file SDL <-> code-resolver linking (detect/graphql_link.py)."""

from __future__ import annotations

from entrygraph import CodeGraph


def _write_schema(tmp_path):
    (tmp_path / "schema.graphql").write_text(
        "type Query {\n  user(id: ID!): User\n  orphan: String\n}\ntype User {\n  id: ID!\n}\n"
    )


def test_code_resolver_hint_supersedes_sdl_row(tmp_path):
    # Apollo detected: the code-side rule fires for Query.user, so the SDL row
    # for the same route is dropped — one entrypoint per field, bound to code.
    _write_schema(tmp_path)
    (tmp_path / "package.json").write_text('{"name":"app","dependencies":{"@apollo/server":"^4"}}')
    (tmp_path / "resolvers.ts").write_text(
        "export const resolvers = {\n  Query: {\n    user: (_p, { id }) => ({ id }),\n  },\n};\n"
    )
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    eps = graph.entrypoints(kind="graphql_resolver", route="Query.user")
    orphan = graph.entrypoints(kind="graphql_resolver", route="Query.orphan")
    graph.close()
    assert len(eps) == 1
    assert eps[0].framework == "apollo"
    assert eps[0].symbol.qname == "resolvers.resolvers.Query.user"
    # a field with no code resolver keeps its SDL-bound row
    assert len(orphan) == 1
    assert orphan[0].framework == "graphql-sdl"


def test_sdl_hint_rebinds_to_code_resolver_without_framework(tmp_path):
    # No GraphQL framework detected (no manifest): the code-side rule can't fire,
    # but the SDL hint still rebinds to the resolver function via the suffix
    # match, so paths can traverse into the body.
    _write_schema(tmp_path)
    (tmp_path / "handlers.ts").write_text(
        "export const resolvers = {\n  Query: {\n    user: (_p, { id }) => ({ id }),\n  },\n};\n"
    )
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    eps = graph.entrypoints(kind="graphql_resolver", route="Query.user")
    graph.close()
    assert len(eps) == 1
    assert eps[0].framework == "graphql-sdl"
    assert eps[0].symbol.qname == "handlers.resolvers.Query.user"
    assert eps[0].extra["schema_file"] == "schema.graphql"  # SDL provenance kept


def test_sdl_only_repo_keeps_sdl_bindings(tmp_path):
    _write_schema(tmp_path)
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    eps = {e.route: e for e in graph.entrypoints(kind="graphql_resolver")}
    graph.close()
    assert set(eps) == {"Query.user", "Query.orphan"}
    assert eps["Query.user"].symbol.qname == "schema_graphql.Query.user"


def test_sdl_resolver_reaches_sink_via_rebound_handler(tmp_path):
    # end-to-end reachability: schema-first repo, resolver body shells out;
    # graphql_resolver seeds http_input, the rebind connects SDL -> code.
    (tmp_path / "schema.graphql").write_text("type Query {\n  run(cmd: String!): String\n}\n")
    (tmp_path / "impl.ts").write_text(
        "import { execSync } from 'child_process';\n"
        "export const resolvers = {\n"
        "  Query: {\n"
        "    run: (_p, { cmd }) => execSync(cmd).toString(),\n"
        "  },\n"
        "};\n"
    )
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    paths = graph.paths(source_category="http_input", sink_category="command_exec")
    graph.close()
    assert paths
    assert any(p.symbols[0].qname == "impl.resolvers.Query.run" for p in paths)
