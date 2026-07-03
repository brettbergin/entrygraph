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


def _gin_routes(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.receiver_text is not None
            and ref.callee_name in _GIN_METHODS
            and ref.arg_preview
        ):
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if route is not None and route.startswith("/"):
                verb = ref.callee_name.upper()
                method = verb if verb in _HTTP_VERBS else "*"
                hints.append(
                    EntrypointHint(
                        rule_id="go.gin.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=ref.caller_qualified_name,
                        route=route,
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


register(EntrypointRule("go.core.main", "go", None, EntrypointKind.MAIN, _go_main))
register(
    EntrypointRule("go.nethttp.route", "go", "net/http", EntrypointKind.HTTP_ROUTE, _nethttp_routes)
)
register(EntrypointRule("go.gin.route", "go", "gin", EntrypointKind.HTTP_ROUTE, _gin_routes))
register(EntrypointRule("go.chi.route", "go", "chi", EntrypointKind.HTTP_ROUTE, _gin_style("chi")))
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
