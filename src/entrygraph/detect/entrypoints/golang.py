"""Go entrypoint rules: the package-main entrypoint, net/http handler
registration, gin router routes, and cobra CLI commands."""

from __future__ import annotations

import re

from entrygraph.detect.entrypoints.base import (
    EntrypointRule,
    first_string_arg,
    register,
)
from entrygraph.extract.ir import EntrypointHint, FileExtraction
from entrygraph.kinds import EntrypointKind, SymbolKind

# gin exports UPPERCASE verbs (r.GET); chi/fiber use TitleCase (r.Get). Accept
# both so the shared _gin_style matcher recognizes chi/fiber, not just gin.
_HTTP_VERBS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})
_GIN_METHODS = (
    _HTTP_VERBS | frozenset(v.title() for v in _HTTP_VERBS) | frozenset({"Any", "Handle"})
)
_NETHTTP_REGISTER = frozenset({"HandleFunc", "Handle"})


def _go_main(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol in x.symbols:
        if (
            symbol.kind is SymbolKind.FUNCTION
            and symbol.name == "main"
            and symbol.parent_qualified_name is None
        ):
            hints.append(
                EntrypointHint(
                    rule_id="go.core.main",
                    kind=EntrypointKind.MAIN,
                    handler_qualified_name=symbol.qualified_name,
                    name=symbol.qualified_name,
                    span=symbol.span,
                )
            )
    return hints


def _nethttp_routes(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.receiver_text == "http"
            and ref.callee_name in _NETHTTP_REGISTER
            and ref.arg_preview
        ):
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if route is not None:
                hints.append(
                    EntrypointHint(
                        rule_id="go.nethttp.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=ref.caller_qualified_name,
                        route=route,
                        http_methods=["*"],
                        framework="net/http",
                        metadata={"registration": ref.arg_preview},
                    )
                )
    return hints


def _group_prefixes(x: FileExtraction) -> dict[str, str]:
    """Map each gin/fiber router-group variable to its composed path prefix.

    `api := app.Group("/api"); v1 := api.Group("/v1")` -> {api: /api, v1: /api/v1}.
    A nested group inherits its parent group's prefix; Go requires a variable to be
    declared before use, so ascending line order resolves the chain in one pass. A
    group with no assign target (`h(app.Group("/x"))`, passed straight into another
    function) is out of static reach and contributes no prefix.
    """
    prefixes: dict[str, str] = {}
    groups = sorted(
        (
            ref
            for ref in x.references
            if ref.kind == "call"
            and ref.callee_name == "Group"
            and ref.assign_target
            and ref.receiver_text is not None
            and ref.arg_preview
        ),
        key=lambda r: r.span.start_line,
    )
    for ref in groups:
        if ref.arg_preview is None:  # already filtered above; narrows for the type-checker
            continue
        local = first_string_arg("(" + ref.arg_preview.lstrip("("))
        if local is None:
            continue  # closure-only group (chi-style middleware group) — no prefix
        parent = prefixes.get(ref.receiver_text or "", "")
        prefixes[ref.assign_target or ""] = _compose_prefixes([parent, local])
    return prefixes


def _gin_routes(x: FileExtraction) -> list[EntrypointHint]:
    prefixes = _group_prefixes(x)
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.receiver_text is not None
            and ref.callee_name in _GIN_METHODS
            and ref.arg_preview
        ):
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if route is None:
                continue
            verb = ref.callee_name.upper()
            is_verb = verb in _HTTP_VERBS
            group_prefix = prefixes.get(ref.receiver_text)
            # A grouped route often drops the leading slash (`v1.GET("users")`) since
            # the group carries the prefix; accept that only for a concrete verb on a
            # known group var. Otherwise keep the conservative leading-slash check
            # (guards against Handle/Any's method-first arg and stray string args).
            if not route.startswith("/") and not (is_verb and group_prefix is not None):
                continue
            method = verb if is_verb else "*"
            full = _compose_prefixes([group_prefix or "", route])
            hints.append(
                EntrypointHint(
                    rule_id="go.gin.route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=ref.caller_qualified_name,
                    route=full,
                    http_methods=[method],
                    framework="gin",
                    metadata={"registration": ref.arg_preview},
                )
            )
    return hints


def _gin_style(framework: str):
    """chi and fiber share gin's r.Get('/x', handler) registration shape."""

    def matcher(x: FileExtraction) -> list[EntrypointHint]:
        hints = []
        for hint in _gin_routes(x):
            hints.append(
                EntrypointHint(
                    rule_id=f"go.{framework}.route",
                    kind=hint.kind,
                    handler_qualified_name=hint.handler_qualified_name,
                    route=hint.route,
                    http_methods=hint.http_methods,
                    framework=framework,
                    metadata=hint.metadata,
                )
            )
        return hints

    return matcher


def _compose_prefixes(parts: list[str]) -> str:
    segs = [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(segs)


def _chi_routes(x: FileExtraction) -> list[EntrypointHint]:
    """chi routes, with `r.Route("/prefix", func(r){...})` sub-router prefixes.

    Route nests its sub-routes in an inline closure, so a verb route whose line
    falls inside a Route call's span gets that prefix (nested Routes stack). Mount
    (`r.Mount("/api", subRouter())`) points at a router built in another function,
    which is out of static reach, so its prefix isn't composed."""
    scopes: list[tuple[int, int, str]] = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.callee_name in ("Route", "Mount")
            and ref.receiver_text is not None
            and ref.arg_preview
            and ref.span.end_line > ref.span.start_line  # has an inline closure body
        ):
            prefix = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if prefix and prefix.startswith("/"):
                scopes.append((ref.span.start_line, ref.span.end_line, prefix))
    scopes.sort()

    hints = []
    for ref in x.references:
        if (
            ref.kind != "call"
            or ref.receiver_text is None
            or ref.callee_name not in _GIN_METHODS
            or not ref.arg_preview
        ):
            continue
        route = first_string_arg("(" + ref.arg_preview.lstrip("("))
        if route is None or not route.startswith("/"):
            continue
        verb = ref.callee_name.upper()
        method = verb if verb in _HTTP_VERBS else "*"
        line = ref.span.start_line
        prefixes = [p for (s, e, p) in scopes if s < line <= e]
        hints.append(
            EntrypointHint(
                rule_id="go.chi.route",
                kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=ref.caller_qualified_name,
                route=_compose_prefixes([*prefixes, route]),
                http_methods=[method],
                framework="chi",
                metadata={"registration": ref.arg_preview},
            )
        )
    return hints


_QUOTED = re.compile(r'"([^"]+)"')


def _gorilla_routes(x: FileExtraction) -> list[EntrypointHint]:
    """gorilla/mux, including the chained builder forms.

    A registration is a method chain on one statement:
        r.HandleFunc("/x", h).Methods("GET")
        r.Path("/x").Methods("GET", "POST").HandlerFunc(h)
    so the route (Path/HandleFunc), verb(s) (Methods) and handler are aggregated
    per line. Previously only bare HandleFunc was matched — the `.Path().Methods()`
    form and any `.Methods()` verb were dropped (loki: ~0% of gorilla routes).
    """
    routes: dict[int, str] = {}
    methods: dict[int, list[str]] = {}
    handlers: dict[int, str | None] = {}
    for ref in x.references:
        if ref.kind != "call" or not ref.arg_preview:
            continue
        line = ref.span.start_line
        # a route is opened by HandleFunc("/x", h) or Path("/x") on the router;
        # exclude net/http's own http.HandleFunc.
        defines_route = (
            ref.callee_name in ("HandleFunc", "Handle") and ref.receiver_text not in (None, "http")
        ) or (ref.callee_name == "Path" and ref.receiver_text is not None)
        if defines_route:
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if route is not None and route.startswith("/"):
                routes.setdefault(line, route)
                handlers.setdefault(line, ref.caller_qualified_name)
        elif ref.callee_name == "Methods":
            verbs = [v.upper() for v in _QUOTED.findall(ref.arg_preview)]
            if verbs:
                methods.setdefault(line, []).extend(verbs)
    return [
        EntrypointHint(
            rule_id="go.gorilla-mux.route",
            kind=EntrypointKind.HTTP_ROUTE,
            handler_qualified_name=handlers.get(line),
            route=route,
            http_methods=methods.get(line, ["*"]),
            framework="gorilla-mux",
        )
        for line, route in routes.items()
    ]


def _cobra_commands(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for ref in x.references:
        if ref.kind == "composite" and ref.callee_text == "cobra.Command":
            hints.append(
                EntrypointHint(
                    rule_id="go.cobra.command",
                    kind=EntrypointKind.CLI_COMMAND,
                    handler_qualified_name=ref.caller_qualified_name,
                    name=first_string_arg("(" + (ref.arg_preview or "") + ")") or None,
                    framework="cobra",
                    span=ref.span,
                )
            )
    return hints


# gRPC service registration: `pb.RegisterFooServer(grpcServer, impl)`. The `Server`
# suffix anchor keeps grpc-gateway's `RegisterFooHandler` / `...HandlerFromEndpoint`
# out. Definitions (`func RegisterFooServer(...)`) are not call references, so only
# real registration call sites match.
_GRPC_REGISTER = re.compile(r"^Register(?P<service>[A-Za-z0-9]+)Server$")


def _grpc_services(x: FileExtraction) -> list[EntrypointHint]:
    """Mark each registered gRPC service as an RPC entrypoint (one per service).

    This is a service-level marker: the implementation (arg 2) is almost always a
    field/local var (`t.Ingester`, `frontendV1`) whose concrete type the IR can't
    infer, so the entrypoint anchors on the registration file's module rather than
    expanding to the impl's individual RPC methods. The service name rides in
    `route` — the Entrypoint row has no name column, and it keeps distinct services
    from collapsing in dedup (they all share `handler_qualified_name=None`).
    """
    hints = []
    seen: set[str] = set()
    for ref in x.references:
        if ref.kind != "call":
            continue
        m = _GRPC_REGISTER.match(ref.callee_name)
        if m is None:
            continue
        service = m.group("service")
        if service in seen:
            continue
        seen.add(service)
        hints.append(
            EntrypointHint(
                rule_id="go.grpc.service",
                kind=EntrypointKind.RPC_HANDLER,
                handler_qualified_name=None,
                route=f"/{service}",
                name=service,
                framework="grpc-go",
                span=ref.span,
            )
        )
    return hints


# gqlgen's generated wiring instantiates unexported per-type resolver structs
# (`type queryResolver struct{ *Resolver }`); user code implements the schema as
# exported methods on them in resolver.go / *.resolvers.go. The lowercase-first
# anchor excludes both the exported root `Resolver` (whose Query()/Mutation()
# methods are wiring, not fields) and generated executionContext methods.
_GQLGEN_RECEIVER = re.compile(r"^([a-z]\w*)Resolver$")
_GQL_OPERATIONS = {"Query": "query", "Mutation": "mutation", "Subscription": "subscription"}


def _gqlgen_resolvers(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol in x.symbols:
        if symbol.kind is not SymbolKind.METHOD or not symbol.is_exported:
            continue
        parent = (symbol.parent_qualified_name or "").rsplit(".", 1)[-1]
        m = _GQLGEN_RECEIVER.match(parent)
        if m is None:
            continue
        # every gqlgen resolver method takes ctx first — cheap precision guard
        if "ctx context.Context" not in (symbol.signature or ""):
            continue
        parent_type = m.group(1)[:1].upper() + m.group(1)[1:]
        field = symbol.name[:1].lower() + symbol.name[1:]  # gqlgen lowerCamels fields
        hints.append(
            EntrypointHint(
                rule_id="go.gqlgen.resolver",
                kind=EntrypointKind.GRAPHQL_RESOLVER,
                handler_qualified_name=symbol.qualified_name,
                route=f"{parent_type}.{field}",
                name=field,
                framework="gqlgen",
                span=symbol.span,
                metadata={
                    "operation": _GQL_OPERATIONS.get(parent_type, "field"),
                    "parent_type": parent_type,
                },
            )
        )
    return hints


register(EntrypointRule("go.core.main", "go", None, EntrypointKind.MAIN, _go_main))
register(
    EntrypointRule("go.nethttp.route", "go", "net/http", EntrypointKind.HTTP_ROUTE, _nethttp_routes)
)
register(EntrypointRule("go.gin.route", "go", "gin", EntrypointKind.HTTP_ROUTE, _gin_routes))
register(EntrypointRule("go.chi.route", "go", "chi", EntrypointKind.HTTP_ROUTE, _chi_routes))
register(
    EntrypointRule("go.fiber.route", "go", "fiber", EntrypointKind.HTTP_ROUTE, _gin_style("fiber"))
)
register(
    EntrypointRule(
        "go.gorilla-mux.route", "go", "gorilla-mux", EntrypointKind.HTTP_ROUTE, _gorilla_routes
    )
)
register(
    EntrypointRule("go.cobra.command", "go", "cobra", EntrypointKind.CLI_COMMAND, _cobra_commands)
)
register(
    EntrypointRule("go.grpc.service", "go", "grpc-go", EntrypointKind.RPC_HANDLER, _grpc_services)
)
register(
    EntrypointRule(
        "go.gqlgen.resolver", "go", "gqlgen", EntrypointKind.GRAPHQL_RESOLVER, _gqlgen_resolvers
    )
)
