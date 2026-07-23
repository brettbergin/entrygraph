"""Ruby entrypoint-rule tests (kept per-language so PRs touching different
languages don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.extract.ir import FileExtraction, RawReference, Span


def _ruby_ext(references, path):
    return FileExtraction(
        path=path,
        language="ruby",
        module_path=path.replace("/", ".").removesuffix(".rb"),
        parse_ok=True,
        error_count=0,
        symbols=[],
        references=list(references),
    )


def _verb(verb, route):
    return RawReference(
        kind="call",
        callee_text=verb,
        callee_name=verb,
        receiver_text=None,
        span=Span(1, 0, 1, 40),
        caller_qualified_name=None,
        arg_preview=f"('{route}')",
    )


def _sinatra_rule():
    return {r.id: r for r in rules_for("ruby", {"sinatra"})}["ruby.sinatra.route"]


def _grape_rule():
    return {r.id: r for r in rules_for("ruby", {"grape"})}["ruby.grape.route"]


def test_sinatra_route_in_app_file_detected():
    hints = _sinatra_rule().match(_ruby_ext([_verb("get", "/health")], "app.rb"))
    assert [h.route for h in hints] == ["/health"]


def test_rack_test_spec_paths_classified_centrally():
    # Rack::Test uses the same bare `get '/x'` DSL inside specs; those files are not
    # the route surface (sinatra corpus: 256/274 routes came from test/spec) (#33).
    # Exclusion moved from per-rule guards to the walk-time classifier (#94); the
    # corpus paths must stay covered there.
    from entrygraph.fs.testfiles import is_test_path

    for path in (
        "test/routing_test.rb",
        "spec/app_spec.rb",
        "rack-protection/spec/lib/rack/protection/ip_spoofing_spec.rb",
        "some/nested/test/helper_test.rb",
        "spec/api_spec.rb",
    ):
        assert is_test_path(path)


def test_grape_detects_routes():
    assert [h.route for h in _grape_rule().match(_ruby_ext([_verb("post", "/x")], "api.rb"))] == [
        "/x"
    ]


# ---------------- route-template parameters ----------------


def test_route_path_params_styles():
    from entrygraph.detect.entrypoints.base import route_path_params

    by_name = {p.name: p for p in route_path_params("/posts/:post_id/comments/:id(.:format)")}
    assert set(by_name) == {"post_id", "id", "format"}
    assert by_name["post_id"].required and by_name["id"].required
    assert not by_name["format"].required  # inside Rails optional parens
    assert all(p.location == "path" and p.provenance == "route" for p in by_name.values())

    (splat,) = route_path_params("/files/*path")
    assert (splat.name, splat.required) == ("path", False)

    # other languages' styles parse too, for later adoption
    assert [p.name for p in route_path_params("/users/{id}")] == ["id"]
    assert [p.name for p in route_path_params("/users/<int:user_id>")] == ["user_id"]
    assert route_path_params(None) == []
    assert route_path_params("/static") == []
    # duplicate segment names collapse to the first occurrence
    assert [p.name for p in route_path_params("/a/:id/b/:id")] == ["id"]


def test_sinatra_route_emits_path_params():
    (hint,) = _sinatra_rule().match(_ruby_ext([_verb("get", "/reports/:id")], "app.rb"))
    (param,) = hint.parameters
    assert (param.name, param.location, param.required) == ("id", "path", True)
    assert param.line == 1  # the registration line


def test_grape_route_emits_path_params():
    (hint,) = _grape_rule().match(_ruby_ext([_verb("get", "/orders/:order_id")], "api.rb"))
    assert [(p.name, p.location) for p in hint.parameters] == [("order_id", "path")]


def test_rails_verb_route_emits_path_params():
    rails = {r.id: r for r in rules_for("ruby", {"rails"})}["ruby.rails.routes"]
    (hint,) = rails.match(_ruby_ext([_verb("get", "/posts/:id")], "config/routes.rb"))
    assert [(p.name, p.required) for p in hint.parameters] == [("id", True)]


# ---------------- enrichment: usage reads + grape params DSL ----------------


def _ref(callee, preview, start, end=None, receiver=None):
    return RawReference(
        kind="call",
        callee_text=callee,
        callee_name=callee,
        receiver_text=receiver,
        span=Span(start, 0, end or start, 40),
        caller_qualified_name=None,
        arg_preview=preview,
    )


def test_sinatra_observed_params_from_handler_block():
    # get '/search' do ... params[:q] ... end — the route call spans the block
    refs = [
        _ref("get", "('/search')", 1, end=4),
        _ref("params", '("q")', 2),  # synthesized subscript read (#87C)
        _ref("params", '("q")', 3),  # re-read of the same key collapses
    ]
    (hint,) = _sinatra_rule().match(_ruby_ext(refs, "app.rb"))
    (q,) = hint.parameters
    assert (q.name, q.location, q.required, q.provenance) == ("q", "query", False, "usage")


def test_sinatra_usage_skips_declared_path_params():
    refs = [
        _ref("get", "('/users/:id')", 1, end=3),
        _ref("params", '("id")', 2),
    ]
    (hint,) = _sinatra_rule().match(_ruby_ext(refs, "app.rb"))
    assert [(p.name, p.provenance) for p in hint.parameters] == [("id", "route")]


def test_grape_params_block_attaches_to_following_route():
    refs = [
        _ref("params", None, 1, end=4),
        _ref("requires", ":name, type: String", 2),
        _ref("optional", ":age, type: Integer", 3),
        _ref("post", "('/users')", 5, end=7),
    ]
    (hint,) = _grape_rule().match(_ruby_ext(refs, "api.rb"))
    got = {(p.name, p.location, p.required, p.type_ref, p.provenance) for p in hint.parameters}
    assert got == {
        ("name", "body", True, "String", "dsl"),
        ("age", "body", False, "Integer", "dsl"),
    }


def test_grape_params_block_not_adjacent_is_ignored():
    refs = [
        _ref("params", None, 1, end=2),
        _ref("requires", ":name", 2),
        _ref("get", "('/users')", 10, end=11),  # far below the block
    ]
    (hint,) = _grape_rule().match(_ruby_ext(refs, "api.rb"))
    assert hint.parameters == []


# ---------------- graphql-ruby ----------------

from entrygraph.extract.ir import RawSymbol
from entrygraph.kinds import EntrypointKind, SymbolKind


def _gql_ext(symbols, references, path="app/graphql/types/query_type.rb"):
    return FileExtraction(
        path=path,
        language="ruby",
        module_path=path.replace("/", ".").removesuffix(".rb"),
        parse_ok=True,
        error_count=0,
        symbols=list(symbols),
        references=list(references),
    )


def _gql_class(name, bases, start=1, end=30, module="app.graphql.types"):
    return RawSymbol(
        kind=SymbolKind.CLASS,
        name=name,
        qualified_name=f"{module}.{name}",
        span=Span(start, 0, end, 3),
        bases=list(bases),
    )


def _field(arg_preview, line=5):
    return RawReference(
        kind="call",
        callee_text="field",
        callee_name="field",
        receiver_text=None,
        span=Span(line, 2, line, 60),
        caller_qualified_name=None,
        arg_preview=arg_preview,
    )


def _field_rule():
    return {r.id: r for r in rules_for("ruby", {"graphql-ruby"})}["ruby.graphql-ruby.field"]


def _resolver_rule():
    return {r.id: r for r in rules_for("ruby", {"graphql-ruby"})}["ruby.graphql-ruby.resolver"]


def test_graphql_field_binds_instance_method():
    cls = _gql_class("QueryType", ["Types::BaseObject"])
    (hint,) = _field_rule().match(_gql_ext([cls], [_field(":posts, [PostType], null: false")]))
    assert hint.kind is EntrypointKind.GRAPHQL_RESOLVER
    assert hint.route == "Query.posts"
    assert hint.handler_qualified_name == "app.graphql.types.QueryType.posts"
    assert hint.metadata["operation"] == "query"


def test_graphql_field_resolver_option_left_unbound():
    cls = _gql_class("QueryType", ["Types::BaseObject"])
    (hint,) = _field_rule().match(_gql_ext([cls], [_field(":posts, resolver: Resolvers::Posts")]))
    assert hint.handler_qualified_name is None
    assert hint.metadata["resolver_class"] == "Resolvers::Posts"


def test_graphql_field_camelizes_route_keeps_name():
    cls = _gql_class("QueryType", ["Types::BaseObject"])
    (hint,) = _field_rule().match(_gql_ext([cls], [_field(":created_at, String, null: true")]))
    assert hint.route == "Query.createdAt"
    assert hint.name == "created_at"


def test_graphql_type_field_operation_is_field():
    cls = _gql_class("UserType", ["Types::BaseObject"])
    (hint,) = _field_rule().match(
        _gql_ext([cls], [_field(":posts, [PostType]")], path="app/graphql/types/user_type.rb")
    )
    assert hint.route == "User.posts"
    assert hint.metadata["operation"] == "field"


def test_graphql_field_outside_graphql_class_ignored():
    cls = _gql_class("Widget", ["ApplicationRecord"])
    assert _field_rule().match(_gql_ext([cls], [_field(":posts")])) == []


def test_graphql_mutation_class_binds_resolve():
    cls = _gql_class("CreateOrder", ["Mutations::BaseMutation"], module="app.graphql.mutations")
    (hint,) = _resolver_rule().match(_gql_ext([cls], []))
    assert hint.route == "Mutation.createOrder"
    assert hint.handler_qualified_name == "app.graphql.mutations.CreateOrder.resolve"
    assert hint.metadata["operation"] == "mutation"


def test_graphql_plain_resolver_class_has_no_route():
    cls = _gql_class("Posts", ["GraphQL::Schema::Resolver"], module="app.graphql.resolvers")
    (hint,) = _resolver_rule().match(_gql_ext([cls], []))
    assert hint.route is None
    assert hint.handler_qualified_name == "app.graphql.resolvers.Posts.resolve"
