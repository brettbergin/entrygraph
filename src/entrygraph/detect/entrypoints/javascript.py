"""JavaScript/TypeScript entrypoint rules: express/fastify routes, NestJS
decorators, and Next.js file-convention handlers."""

from __future__ import annotations

import re

from entrygraph.detect.entrypoints.base import EntrypointRule, first_string_arg, register
from entrygraph.extract.ir import EntrypointHint, FileExtraction
from entrygraph.kinds import EntrypointKind, SymbolKind

_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "all", "options", "head"})
_NEST_DECORATOR = re.compile(r"^@(Get|Post|Put|Delete|Patch|All)\b")


_TRAILING_IDENT = re.compile(r",\s*([A-Za-z_$][\w$]*)\s*\)?\s*$")


def _express_routes(x: FileExtraction) -> list[EntrypointHint]:
    """app.get('/x', handler) / router.post('/y', ...) style registrations."""
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.receiver_text is not None
            and ref.callee_name in _HTTP_METHODS
            and ref.arg_preview
        ):
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if route is None or not route.startswith("/"):
                continue
            # link a named handler argument (app.post('/x', createReport)); an
            # inline function falls back to the enclosing scope / module.
            handler = ref.caller_qualified_name
            named = _TRAILING_IDENT.search(ref.arg_preview)
            if named:
                handler = f"{x.module_path}.{named.group(1)}"
            hints.append(
                EntrypointHint(
                    rule_id="javascript.express.route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=handler,
                    route=route,
                    http_methods=[ref.callee_name.upper()],
                    framework="express",
                )
            )
    return hints


def _nest_routes(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol in x.symbols:
        if symbol.kind is not SymbolKind.METHOD:
            continue
        for decorator in symbol.decorators:
            m = _NEST_DECORATOR.match(decorator)
            if m:
                hints.append(
                    EntrypointHint(
                        rule_id="javascript.nestjs.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=symbol.qualified_name,
                        route=first_string_arg(decorator) or "",
                        http_methods=[m.group(1).upper()],
                        framework="nestjs",
                    )
                )
    return hints


def _next_handlers(x: FileExtraction) -> list[EntrypointHint]:
    """Next.js app-router route handlers: exported GET/POST in app/**/route.{ts,js}."""
    if not (x.path.endswith(("route.ts", "route.js")) and "/app/" in f"/{x.path}"):
        return []
    hints = []
    for symbol in x.symbols:
        if symbol.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD) and symbol.name in _HTTP_METHODS_UPPER:
            hints.append(
                EntrypointHint(
                    rule_id="javascript.next.route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=symbol.qualified_name,
                    route="/" + x.path.split("/app/", 1)[1].rsplit("/", 1)[0],
                    http_methods=[symbol.name],
                    framework="next",
                )
            )
    return hints


_HTTP_METHODS_UPPER = frozenset(m.upper() for m in _HTTP_METHODS)


register(EntrypointRule("javascript.express.route", "javascript", "express",
                        EntrypointKind.HTTP_ROUTE, _express_routes))
register(EntrypointRule("javascript.fastify.route", "javascript", "fastify",
                        EntrypointKind.HTTP_ROUTE, _express_routes))
register(EntrypointRule("javascript.nestjs.route", "javascript", "nestjs",
                        EntrypointKind.HTTP_ROUTE, _nest_routes))
register(EntrypointRule("javascript.next.route", "javascript", "next",
                        EntrypointKind.HTTP_ROUTE, _next_handlers))
