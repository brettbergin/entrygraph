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
_RECEIVER_ROOT = re.compile(r"^\s*([A-Za-z_$][\w$]*)")
# HTTP-client and test-harness roots whose .get()/.post() are *requests*, not
# route registrations: supertest/nock/chai/superagent and Node/browser clients.
# Excludes them so test suites don't flood the route table (ghost 72%, strapi 99%).
_CLIENT_ROOTS = frozenset(
    {"nock", "supertest", "request", "superagent", "agent", "chai", "axios", "got",
     "fetch", "http", "https", "fastify"}  # fastify.inject(...).get is a test call
)


def _receiver_root(receiver_text: str) -> str | None:
    m = _RECEIVER_ROOT.match(receiver_text)
    return m.group(1) if m else None


def _router_routes(framework: str):
    """Factory for app.get('/x', handler) / router.post(...) style matchers.

    express, koa, and hono all register routes this way; the framework label is
    bound per rule so detection stays accurate.
    """

    def matcher(x: FileExtraction) -> list[EntrypointHint]:
        hints = []
        for ref in x.references:
            if (
                ref.kind == "call"
                and ref.receiver_text is not None
                and _receiver_root(ref.receiver_text) not in _CLIENT_ROOTS
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
                        rule_id=f"javascript.{framework}.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=handler,
                        route=route,
                        http_methods=[ref.callee_name.upper()],
                        framework=framework,
                    )
                )
        return hints

    return matcher


_express_routes = _router_routes("express")


def _event_handlers(
    x: FileExtraction, receivers: frozenset[str], framework: str
) -> list[EntrypointHint]:
    """socket.io / electron style `emitter.on('event', handler)` registrations."""
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.callee_name in ("on", "handle")
            and ref.receiver_text in receivers
            and ref.arg_preview
        ):
            hints.append(
                EntrypointHint(
                    rule_id=f"javascript.{framework}.event",
                    kind=EntrypointKind.EVENT_HANDLER,
                    handler_qualified_name=ref.caller_qualified_name,
                    name=first_string_arg("(" + ref.arg_preview.lstrip("(")),
                    framework=framework,
                )
            )
    return hints


def _socketio_events(x: FileExtraction) -> list[EntrypointHint]:
    return _event_handlers(x, frozenset({"io", "socket"}), "socket.io")


def _electron_ipc(x: FileExtraction) -> list[EntrypointHint]:
    return _event_handlers(x, frozenset({"ipcMain"}), "electron")


def _lambda_js_handlers(x: FileExtraction) -> list[EntrypointHint]:
    """An exported `handler` function is the AWS Lambda entrypoint convention."""
    hints = []
    for symbol in x.symbols:
        if (
            symbol.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
            and symbol.name == "handler"
            and symbol.is_exported
        ):
            hints.append(
                EntrypointHint(
                    rule_id="javascript.aws-lambda.handler",
                    kind=EntrypointKind.LAMBDA_HANDLER,
                    handler_qualified_name=symbol.qualified_name,
                    name=symbol.name,
                    framework="aws-lambda-js",
                )
            )
    return hints


def _express_middleware(x: FileExtraction) -> list[EntrypointHint]:
    """app.use(mw) / router.use('/path', mw) -> middleware registration."""
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.callee_name == "use"
            and ref.receiver_text in ("app", "router")
        ):
            hints.append(
                EntrypointHint(
                    rule_id="javascript.express.middleware",
                    kind=EntrypointKind.MIDDLEWARE,
                    handler_qualified_name=ref.caller_qualified_name,
                    name="use",
                    framework="express",
                    metadata={"registration": ref.arg_preview} if ref.arg_preview else {},
                )
            )
    return hints


_NEST_CONTROLLER = re.compile(r"^@Controller\b")


def _nest_routes(x: FileExtraction) -> list[EntrypointHint]:
    # controller class qname -> its @Controller('prefix') path prefix
    prefixes: dict[str, str] = {}
    for symbol in x.symbols:
        if symbol.kind is SymbolKind.CLASS:
            for decorator in symbol.decorators:
                if _NEST_CONTROLLER.match(decorator):
                    prefixes[symbol.qualified_name] = (first_string_arg(decorator) or "").strip("/")
    hints = []
    for symbol in x.symbols:
        if symbol.kind is not SymbolKind.METHOD:
            continue
        for decorator in symbol.decorators:
            m = _NEST_DECORATOR.match(decorator)
            if m:
                prefix = prefixes.get(symbol.parent_qualified_name or "", "")
                path = (first_string_arg(decorator) or "").strip("/")
                route = "/" + "/".join(p for p in (prefix, path) if p)
                hints.append(
                    EntrypointHint(
                        rule_id="javascript.nestjs.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=symbol.qualified_name,
                        route=route,
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
        if (
            symbol.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
            and symbol.name in _HTTP_METHODS_UPPER
        ):
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


register(
    EntrypointRule(
        "javascript.express.route",
        "javascript",
        "express",
        EntrypointKind.HTTP_ROUTE,
        _express_routes,
    )
)
register(
    EntrypointRule(
        "javascript.express.middleware",
        "javascript",
        "express",
        EntrypointKind.MIDDLEWARE,
        _express_middleware,
    )
)
register(
    EntrypointRule(
        "javascript.fastify.route",
        "javascript",
        "fastify",
        EntrypointKind.HTTP_ROUTE,
        _router_routes("fastify"),
    )
)
register(
    EntrypointRule(
        "javascript.koa.route",
        "javascript",
        "koa",
        EntrypointKind.HTTP_ROUTE,
        _router_routes("koa"),
    )
)
register(
    EntrypointRule(
        "javascript.hono.route",
        "javascript",
        "hono",
        EntrypointKind.HTTP_ROUTE,
        _router_routes("hono"),
    )
)
register(
    EntrypointRule(
        "javascript.socketio.event",
        "javascript",
        "socket.io",
        EntrypointKind.EVENT_HANDLER,
        _socketio_events,
    )
)
register(
    EntrypointRule(
        "javascript.electron.ipc",
        "javascript",
        "electron",
        EntrypointKind.EVENT_HANDLER,
        _electron_ipc,
    )
)
register(
    EntrypointRule(
        "javascript.aws-lambda.handler",
        "javascript",
        "aws-lambda-js",
        EntrypointKind.LAMBDA_HANDLER,
        _lambda_js_handlers,
    )
)
register(
    EntrypointRule(
        "javascript.nestjs.route", "javascript", "nestjs", EntrypointKind.HTTP_ROUTE, _nest_routes
    )
)
register(
    EntrypointRule(
        "javascript.next.route", "javascript", "next", EntrypointKind.HTTP_ROUTE, _next_handlers
    )
)
