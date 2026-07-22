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
