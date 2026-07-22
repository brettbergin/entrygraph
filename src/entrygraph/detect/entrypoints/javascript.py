"""JavaScript/TypeScript entrypoint rules: express/fastify routes, NestJS
decorators, and Next.js file-convention handlers."""

from __future__ import annotations

import re

from entrygraph.detect.entrypoints.base import (
    EntrypointRule,
    compose_route,
    first_string_arg,
    register,
)
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
    {
        "nock",
        "supertest",
        "request",
        "superagent",
        "agent",
        "chai",
        "axios",
        "got",
        "fetch",
        "http",
        "https",
        "fastify",
    }  # fastify.inject(...).get is a test call
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
                # a router registered on a var mounted under a path prefix (resolved
                # cross-file by the scanner) inherits that prefix (#36).
                prefix = x.route_prefixes.get(ref.receiver_text or "", "")
                full = compose_route(prefix, route) if prefix else route
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
                        route=full,
                        http_methods=[ref.callee_name.upper()],
                        framework=framework,
                        # registration line, so the scanner can bind a
                        # handler-passed-by-reference (router.get('/x', ctrl.fn))
                        # to the callback edge emitted at the same site.
                        span=ref.span,
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


_GQL_RESOLVER_CLASS = re.compile(r"^@Resolver\b")
_GQL_FIELD_DECORATOR = re.compile(r"^@(Query|Mutation|Subscription|ResolveField|FieldResolver)\b")
_GQL_NAME_OPT = re.compile(r"""name\s*:\s*['"]([\w.]+)['"]""")
_GQL_TYPE_THUNK = re.compile(r"=>\s*\[?\s*([A-Za-z_$][\w$]*)")
_GQL_OPERATIONS = {"Query": "query", "Mutation": "mutation", "Subscription": "subscription"}


def _gql_decorator_resolvers(framework: str):
    """@Resolver classes with @Query/@Mutation/@Subscription/@ResolveField methods.

    NestJS-GraphQL and TypeGraphQL share this shape (TypeGraphQL says
    @FieldResolver where Nest says @ResolveField). Requiring the enclosing class
    to carry @Resolver keeps other libraries' @Query decorators out; when both
    frameworks are detected the scanner's hint dedup keeps one row.
    """

    def matcher(x: FileExtraction) -> list[EntrypointHint]:
        # resolver class qname -> the parent type it resolves for:
        # @Resolver('User') / @Resolver(() => User) / bare -> class name - "Resolver"
        targets: dict[str, str] = {}
        for symbol in x.symbols:
            if symbol.kind is not SymbolKind.CLASS:
                continue
            for decorator in symbol.decorators:
                if _GQL_RESOLVER_CLASS.match(decorator):
                    thunk = _GQL_TYPE_THUNK.search(decorator)
                    targets[symbol.qualified_name] = (
                        first_string_arg(decorator)
                        or (thunk.group(1) if thunk else None)
                        or symbol.name.removesuffix("Resolver")
                        or symbol.name
                    )
        if not targets:
            return []
        hints = []
        for symbol in x.symbols:
            if symbol.kind is not SymbolKind.METHOD or symbol.parent_qualified_name not in targets:
                continue
            for decorator in symbol.decorators:
                m = _GQL_FIELD_DECORATOR.match(decorator)
                if not m:
                    continue
                kind = m.group(1)
                if kind in ("ResolveField", "FieldResolver"):
                    parent = targets[symbol.parent_qualified_name]
                    field = first_string_arg(decorator) or symbol.name
                    operation = "field"
                else:
                    parent, operation = kind, _GQL_OPERATIONS[kind]
                    named = _GQL_NAME_OPT.search(decorator)
                    field = named.group(1) if named else symbol.name
                hints.append(
                    EntrypointHint(
                        rule_id=f"javascript.{framework}.resolver",
                        kind=EntrypointKind.GRAPHQL_RESOLVER,
                        handler_qualified_name=symbol.qualified_name,
                        route=f"{parent}.{field}",
                        name=field,
                        framework=framework,
                        span=symbol.span,
                        metadata={"operation": operation, "parent_type": parent},
                    )
                )
        return hints

    return matcher


_RESOLVERS_VAR = re.compile(r"resolvers?$", re.IGNORECASE)


def _apollo_resolvers(x: FileExtraction) -> list[EntrypointHint]:
    """Apollo resolver-map objects: `const resolvers = { Query: { user() {} } }`.

    Keys off the object-literal method symbols the JS extractor emits with
    key-path qnames. Query/Mutation/Subscription parents match anywhere; other
    TitleCase parents (type-field resolvers like `User: { posts }`) only inside
    a *resolvers*-named or `Resolvers`-typed container. Shorthand *references*
    (`Query: { user }` where `user` is imported) emit no symbol and are a known
    gap.
    """
    typed_vars = {
        b.name for b in x.bindings if b.kind == "declared" and b.type_text.endswith("Resolvers")
    }
    hints = []
    for symbol in x.symbols:
        if symbol.kind is not SymbolKind.METHOD or not symbol.parent_qualified_name:
            continue
        parts = symbol.parent_qualified_name.split(".")
        parent_type = parts[-1]
        container = parts[-2] if len(parts) >= 2 else ""
        operation = _GQL_OPERATIONS.get(parent_type)
        if operation is None:
            in_map = bool(_RESOLVERS_VAR.search(container)) or container in typed_vars
            if not in_map or not parent_type[:1].isupper():
                continue
            operation = "field"
        hints.append(
            EntrypointHint(
                rule_id="javascript.apollo.resolver",
                kind=EntrypointKind.GRAPHQL_RESOLVER,
                handler_qualified_name=symbol.qualified_name,
                route=f"{parent_type}.{symbol.name}",
                name=symbol.name,
                framework="apollo",
                span=symbol.span,
                metadata={"operation": operation, "parent_type": parent_type},
            )
        )
    return hints


def _next_handlers(x: FileExtraction) -> list[EntrypointHint]:
    """Next.js App Router route handlers in app/**/route.{ts,js}.

    Matches both the plain `export async function GET() {}` form and the
    wrapper-const form `export const GET = withAuth(getHandler)` — calcom's
    dominant idiom, where nearly every method export wraps the real handler in
    a responder. In the wrapper form the exported symbol is a VARIABLE whose own
    qname is not the handler body, so we leave the handler unbound and set
    `span` to the declaration line: the scanner then binds the route to the
    handler passed by reference into the wrapper (the callback edge emitted at
    that same line), falling back to the module when nothing was passed inline.
    """
    normalized = f"/{x.path}"  # leading slash so a top-level `app/` dir also matches
    if not (x.path.endswith(("route.ts", "route.js")) and "/app/" in normalized):
        return []
    route = "/" + normalized.split("/app/", 1)[1].rsplit("/", 1)[0]
    hints = []
    for symbol in x.symbols:
        if symbol.name not in _HTTP_METHODS_UPPER:
            continue
        if symbol.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            handler, span = symbol.qualified_name, None
        elif symbol.kind is SymbolKind.VARIABLE:
            handler, span = None, symbol.span
        else:
            continue
        hints.append(
            EntrypointHint(
                rule_id="javascript.next.route",
                kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=handler,
                route=route,
                http_methods=[symbol.name],
                framework="next",
                span=span,
            )
        )
    return hints


_HTTP_METHODS_UPPER = frozenset(m.upper() for m in _HTTP_METHODS)
_NEXT_PAGES_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _next_pages_handlers(x: FileExtraction) -> list[EntrypointHint]:
    """Next.js Pages Router API routes: every file under `pages/api/**` is a route
    (file-based routing), whatever the default-export shape — a named `handler`
    function, a `createNextApiHandler(router)` wrapper, or a bare arrow. The IR has
    no `export default` marker to key on, so we rely on the file convention itself.

    The handler dispatches on `req.method` internally, so the route carries no
    specific verb. Bind to a `handler`-named function when present (the Pages
    Router convention) so taint reaches the real body; otherwise the module.
    """
    normalized = f"/{x.path}"  # leading slash so a top-level `pages/` dir also matches
    if "/pages/api/" not in normalized or not x.path.endswith(_NEXT_PAGES_EXTS):
        return []
    if x.path.endswith(".d.ts"):
        return []  # type declarations aren't routes (#33)
    tail = normalized.split("/pages/api/", 1)[1].rsplit(".", 1)[0]  # after prefix, no ext
    if tail == "index":
        tail = ""
    elif tail.endswith("/index"):
        tail = tail[: -len("/index")]
    route = "/api" + (f"/{tail}" if tail else "")
    handler = next(
        (
            s.qualified_name
            for s in x.symbols
            if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD) and s.name == "handler"
        ),
        None,
    )
    return [
        EntrypointHint(
            rule_id="javascript.next.pages-route",
            kind=EntrypointKind.HTTP_ROUTE,
            handler_qualified_name=handler,
            route=route,
            framework="next",
        )
    ]


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
register(
    EntrypointRule(
        "javascript.next.pages-route",
        "javascript",
        "next",
        EntrypointKind.HTTP_ROUTE,
        _next_pages_handlers,
    )
)
register(
    EntrypointRule(
        "javascript.apollo.resolver",
        "javascript",
        "apollo",
        EntrypointKind.GRAPHQL_RESOLVER,
        _apollo_resolvers,
    )
)
register(
    EntrypointRule(
        "javascript.nestjs-graphql.resolver",
        "javascript",
        "nestjs-graphql",
        EntrypointKind.GRAPHQL_RESOLVER,
        _gql_decorator_resolvers("nestjs-graphql"),
    )
)
register(
    EntrypointRule(
        "javascript.type-graphql.resolver",
        "javascript",
        "type-graphql",
        EntrypointKind.GRAPHQL_RESOLVER,
        _gql_decorator_resolvers("type-graphql"),
    )
)
