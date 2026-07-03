"""Go entrypoint-rule tests (kept per-language so PRs touching different languages
don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.extract.ir import FileExtraction, RawReference, Span


def _go_ext(references, path="main.go"):
    return FileExtraction(
        path=path,
        language="go",
        module_path="app",
        parse_ok=True,
        error_count=0,
        symbols=[],
        references=list(references),
    )


def _call(callee, receiver, arg, start=1, end=None, assign=None):
    return RawReference(
        kind="call",
        callee_text=f"{receiver}.{callee}" if receiver else callee,
        callee_name=callee,
        receiver_text=receiver,
        span=Span(start, 0, end if end is not None else start, 40),
        caller_qualified_name="app.routes",
        arg_preview=arg,
        assign_target=assign,
    )


def _chi_rule():
    return {r.id: r for r in rules_for("go", {"chi"})}["go.chi.route"]


def test_chi_route_prefix_is_composed():
    # r.Route("/admin", func(r){ r.Get("/users", h); r.Post("/users", h) }) — the
    # inner verb routes fall inside the Route call's span and get the prefix (#37).
    refs = [
        _call("Get", "r", '("/health", health)', start=2),
        _call("Route", "r", '("/admin", func(r chi.Router) { ... })', start=3, end=6),
        _call("Get", "r", '("/users", listUsers)', start=4),
        _call("Post", "r", '("/users", createUser)', start=5),
    ]
    got = {(h.http_methods[0], h.route) for h in _chi_rule().match(_go_ext(refs))}
    assert got == {
        ("GET", "/health"),  # top-level, no prefix
        ("GET", "/admin/users"),  # composed with the enclosing Route prefix
        ("POST", "/admin/users"),
    }


def test_chi_nested_route_prefixes_stack():
    refs = [
        _call("Route", "r", '("/api", func(r){ ... })', start=1, end=6),
        _call("Route", "r", '("/v1", func(r){ ... })', start=2, end=5),
        _call("Get", "r", '("/users", h)', start=3),
    ]
    got = {(h.http_methods[0], h.route) for h in _chi_rule().match(_go_ext(refs))}
    assert got == {("GET", "/api/v1/users")}  # both enclosing prefixes stack


def test_chi_mount_prefix_not_composed_across_functions():
    # r.Mount("/api", apiRouter()) points at a router built elsewhere (single-line,
    # no inline body) — its prefix is out of static reach and must not be applied.
    refs = [
        _call("Mount", "r", '("/api", apiRouter())', start=1),
        _call("Get", "r", '("/health", h)', start=2),
    ]
    got = {(h.http_methods[0], h.route) for h in _chi_rule().match(_go_ext(refs))}
    assert got == {("GET", "/health")}  # no phantom /api prefix


def _grpc_rule():
    return {r.id: r for r in rules_for("go", {"grpc-go"})}["go.grpc.service"]


def test_grpc_service_registration_detected():
    # pb.RegisterPusherServer(grpcServer, t.Ingester) -> one RPC entrypoint whose
    # service name rides in `route` (no name column; keeps services distinct in
    # dedup since they all share handler=None). Impl type is unresolvable, so the
    # handler is left unbound (scanner anchors it on the module) (#37).
    refs = [
        _call("RegisterPusherServer", "logproto", "(t.Server.GRPC, t.Ingester)"),
        _call("RegisterQuerierServer", "logproto", "(t.Server.GRPC, t.Ingester)", start=2),
    ]
    hints = _grpc_rule().match(_go_ext(refs, path="pkg/loki/modules.go"))
    got = {(h.route, h.name, h.framework, h.handler_qualified_name) for h in hints}
    assert got == {
        ("/Pusher", "Pusher", "grpc-go", None),
        ("/Querier", "Querier", "grpc-go", None),
    }


def test_grpc_same_service_deduped_within_file():
    refs = [
        _call("RegisterPusherServer", "logproto", "(s, t.distributor)"),
        _call("RegisterPusherServer", "logproto", "(s, t.Ingester)", start=2),
    ]
    hints = _grpc_rule().match(_go_ext(refs, path="pkg/loki/modules.go"))
    assert [h.route for h in hints] == ["/Pusher"]


def test_grpc_gateway_and_non_server_calls_ignored():
    # grpc-gateway registrars end in `Handler`/`HandlerFromEndpoint`, not `Server`,
    # and must not be treated as service registrations.
    refs = [
        _call("RegisterPusherHandler", "gw", "(ctx, mux, conn)"),
        _call("RegisterPusherHandlerFromEndpoint", "gw", "(ctx, mux, addr, opts)", start=2),
        _call("Serve", "grpcServer", "(lis)", start=3),
    ]
    assert _grpc_rule().match(_go_ext(refs, path="pkg/loki/modules.go")) == []


def test_grpc_test_harness_registrations_excluded():
    # A real service registered inside a *_test.go harness is not production surface.
    refs = [_call("RegisterFrontendServer", "frontendv1pb", "(grpcServer, v1)")]
    assert _grpc_rule().match(_go_ext(refs, path="pkg/frontend/frontend_test.go")) == []


def _gin_rule():
    return {r.id: r for r in rules_for("go", {"gin"})}["go.gin.route"]


def _fiber_rule():
    return {r.id: r for r in rules_for("go", {"fiber"})}["go.fiber.route"]


def test_gin_group_prefix_composed():
    # api := router.Group("/api"); api.GET("/users", h) -> /api/users (#36). The
    # group var is bound via the call's assign_target.
    refs = [
        _call("Group", "router", '("/api")', start=1, assign="api"),
        _call("GET", "api", '("/users", listUsers)', start=2),
        _call("GET", "router", '("/health", health)', start=3),  # ungrouped
    ]
    got = {(h.http_methods[0], h.route) for h in _gin_rule().match(_go_ext(refs))}
    assert got == {("GET", "/api/users"), ("GET", "/health")}


def test_gin_nested_groups_stack():
    # api := app.Group("/api"); v1 := api.Group("/v1"); v1.GET("/users", h)
    refs = [
        _call("Group", "app", '("/api")', start=1, assign="api"),
        _call("Group", "api", '("/v1")', start=2, assign="v1"),
        _call("GET", "v1", '("/users", h)', start=3),
    ]
    got = {(h.http_methods[0], h.route) for h in _gin_rule().match(_go_ext(refs))}
    assert got == {("GET", "/api/v1/users")}


def test_gin_grouped_route_without_leading_slash():
    # Grouped routes commonly drop the leading slash (gin versioning example);
    # accept it for a concrete verb on a known group var and still prefix it.
    refs = [
        _call("Group", "router", '("/v1")', start=1, assign="apiV1"),
        _call("GET", "apiV1", '("users", h)', start=2),
    ]
    got = {(h.http_methods[0], h.route) for h in _gin_rule().match(_go_ext(refs))}
    assert got == {("GET", "/v1/users")}


def test_gin_ungrouped_route_still_requires_leading_slash():
    # A slash-less path on a non-group receiver stays filtered (guards against
    # Handle/Any's method-first arg and stray string args).
    refs = [_call("GET", "router", '("users", h)', start=1)]
    assert _gin_rule().match(_go_ext(refs)) == []


def test_gin_group_passed_to_function_has_no_prefix():
    # h(app.Group("/api")) has no assign target — out of static reach, so a later
    # same-named receiver must not pick up a phantom prefix.
    refs = [
        _call("Group", "app", '("/api")', start=1, assign=None),
        _call("GET", "app", '("/health", h)', start=2),
    ]
    got = {(h.http_methods[0], h.route) for h in _gin_rule().match(_go_ext(refs))}
    assert got == {("GET", "/health")}


def test_fiber_group_prefix_end_to_end():
    # Exercises the real Go extractor (assign_target capture) + fiber rule together.
    from entrygraph.extract.base import FileContext
    from entrygraph.extract.golang import GoExtractor
    from entrygraph.parsing.parsers import parse

    src = (
        b"package router\n"
        b"func SetupRoutes(app *fiber.App) {\n"
        b'	api := app.Group("/api")\n'
        b'	api.Get("/", handler.Hello)\n'
        b'	auth := api.Group("/auth")\n'
        b'	auth.Post("/login", handler.Login)\n'
        b"}\n"
    )
    ctx = FileContext(
        path="router/router.go",
        language="go",
        module_path="router",
        source=src,
        is_package=False,
    )
    x = GoExtractor().extract(parse("go", src), ctx)
    got = {(h.http_methods[0], h.route) for h in _fiber_rule().match(x)}
    assert got == {("GET", "/api"), ("POST", "/api/auth/login")}
