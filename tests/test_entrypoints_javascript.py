"""JavaScript/TypeScript entrypoint-rule tests (kept per-language so PRs touching
different languages don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.extract.ir import FileExtraction, RawSymbol, Span
from entrygraph.kinds import EntrypointKind, SymbolKind


def _js_ext(symbols=(), path="apps/web/app/api/me/route.ts"):
    return FileExtraction(
        path=path,
        language="typescript",
        module_path=path.rsplit("/", 1)[0].replace("/", "."),
        parse_ok=True,
        error_count=0,
        symbols=list(symbols),
    )


def _sym(name, kind, line=1):
    qname = f"apps.web.app.api.me.route.{name}"
    return RawSymbol(kind=kind, name=name, qualified_name=qname, span=Span(line, 0, line, 40))


def _next_rule():
    return {r.id: r for r in rules_for("javascript", {"next"})}["javascript.next.route"]


def test_next_wrapper_const_handlers_detected():
    # calcom's dominant App Router idiom: `export const GET = wrap(getHandler)` —
    # the export is a VARIABLE, not a function, and was previously missed (#37).
    symbols = [
        _sym("getHandler", SymbolKind.FUNCTION, line=1),
        _sym("postHandler", SymbolKind.FUNCTION, line=2),
        _sym("GET", SymbolKind.VARIABLE, line=10),
        _sym("POST", SymbolKind.VARIABLE, line=11),
    ]
    hints = _next_rule().match(_js_ext(symbols))
    by_method = {h.http_methods[0]: h for h in hints}
    assert set(by_method) == {"GET", "POST"}
    assert all(h.route == "/api/me" for h in hints)
    # handler left unbound + span set to the declaration line, so the scanner
    # binds the route to the handler passed by reference into the wrapper.
    assert by_method["GET"].handler_qualified_name is None
    assert by_method["GET"].span is not None
    assert by_method["GET"].span.start_line == 10


def test_next_plain_function_handler_still_bound_directly():
    # `export async function GET() {}` — the function IS the handler, so its qname
    # is bound directly (no callback indirection needed).
    hint = _next_rule().match(_js_ext([_sym("GET", SymbolKind.FUNCTION)]))[0]
    assert hint.kind is EntrypointKind.HTTP_ROUTE
    assert hint.handler_qualified_name == "apps.web.app.api.me.route.GET"
    assert hint.span is None
    assert hint.route == "/api/me"


def test_next_route_at_top_level_app_dir():
    # A repo whose app-router lives at the top level yields paths like
    # `app/api/me/route.ts` (no leading slash before `app`); route derivation must
    # still resolve rather than IndexError.
    ext = _js_ext([_sym("GET", SymbolKind.FUNCTION)], path="app/api/me/route.ts")
    hint = _next_rule().match(ext)[0]
    assert hint.route == "/api/me"


def test_next_ignores_non_method_symbols_and_non_route_files():
    # A non-method export in a route file, and a method-named export outside a
    # route file, must both be ignored.
    assert _next_rule().match(_js_ext([_sym("helper", SymbolKind.VARIABLE)])) == []
    off_route = _js_ext([_sym("GET", SymbolKind.VARIABLE)], path="apps/web/app/api/me/page.ts")
    assert _next_rule().match(off_route) == []


def _pages_rule():
    return {r.id: r for r in rules_for("javascript", {"next"})}["javascript.next.pages-route"]


def _handler_sym(qname="apps.web.pages.api.book.event.handler"):
    return RawSymbol(
        kind=SymbolKind.FUNCTION, name="handler", qualified_name=qname, span=Span(1, 0, 1, 40)
    )


def test_next_pages_route_binds_named_handler():
    # Pages Router is file-based: pages/api/book/event.ts is the route /api/book/event,
    # and the conventional `export default function handler` binds it (#37).
    ext = _js_ext([_handler_sym()], path="apps/web/pages/api/book/event.ts")
    hints = _pages_rule().match(ext)
    assert len(hints) == 1
    h = hints[0]
    assert h.kind is EntrypointKind.HTTP_ROUTE
    assert h.route == "/api/book/event"
    assert h.http_methods == []  # method dispatched inside the handler
    assert h.handler_qualified_name == "apps.web.pages.api.book.event.handler"


def test_next_pages_route_without_handler_falls_back_to_module():
    # `export default createNextApiHandler(router)` (tRPC) defines no handler symbol;
    # the route is still detected, left unbound for the scanner to anchor on module.
    ext = _js_ext(path="apps/web/pages/api/trpc/[trpc].ts")
    hint = _pages_rule().match(ext)[0]
    assert hint.route == "/api/trpc/[trpc]"
    assert hint.handler_qualified_name is None


def test_next_pages_index_files_map_to_parent():
    foo = _pages_rule().match(_js_ext(path="apps/web/pages/api/foo/index.ts"))[0]
    assert foo.route == "/api/foo"
    assert _pages_rule().match(_js_ext(path="pages/api/index.ts"))[0].route == "/api"


def test_next_pages_excludes_type_decls():
    # Colocated tests are excluded at walk time (fs/testfiles, #94); the rule
    # keeps only the .d.ts guard, which is not a test-file concern.
    from entrygraph.fs.testfiles import is_test_path

    assert _pages_rule().match(_js_ext(path="apps/web/pages/api/types.d.ts")) == []
    for path in (
        "apps/web/pages/api/book/recurring-event.test.ts",
        "apps/web/pages/api/foo.spec.ts",
    ):
        assert is_test_path(path)


def test_next_pages_ignores_non_api_pages():
    # A React page under pages/ (not pages/api/) is not an API route.
    assert _pages_rule().match(_js_ext(path="apps/web/pages/about.tsx")) == []


# ---------------- Express cross-file mount prefixes (#36) ----------------


def _mount_ext(module, references=(), imports=(), default_export=None, path=None):
    from entrygraph.extract.ir import FileExtraction

    return FileExtraction(
        path=path or f"src/{module.replace('.', '/')}.ts",
        language="typescript",
        module_path=module,
        parse_ok=True,
        error_count=0,
        references=list(references),
        imports=list(imports),
        default_export=default_export,
    )


def _use(arg, receiver=None, assign=None):
    from entrygraph.extract.ir import RawReference, Span

    return RawReference(
        kind="call",
        callee_text=f"{receiver}.use" if receiver else "use",
        callee_name="use",
        receiver_text=receiver,
        span=Span(1, 0, 1, 40),
        caller_qualified_name=None,
        arg_preview=arg,
        assign_target=assign,
    )


def _imp(module, alias):
    from entrygraph.extract.ir import RawImport, Span

    return RawImport(module=module, imported_name=None, alias=alias, span=Span(1, 0, 1, 10))


def test_resolve_mount_prefixes_transitive_chain():
    # main: app.use(routes); routes: const api = Router().use(auth);
    # export default Router().use('/api', api); auth: export default router.
    from entrygraph.detect.express_mounts import resolve_mount_prefixes

    main = _mount_ext("main", [_use("(routes)", receiver="app")], [_imp("routes", "routes")])
    routes = _mount_ext(
        "routes",
        [_use("(auth)", assign="api"), _use("('/api', api)", assign="<default>")],
        [_imp("auth", "auth")],
    )
    auth = _mount_ext("auth", default_export="router")
    prefixes = resolve_mount_prefixes(
        [("main.ts", main, False), ("routes.ts", routes, False), ("auth.ts", auth, False)]
    )
    assert prefixes.get("auth", {}).get("router") == "/api"


def test_express_mount_prefix_end_to_end(tmp_path):
    from entrygraph import CodeGraph

    src = tmp_path / "src"
    (src / "app" / "routes" / "auth").mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"name":"app","dependencies":{"express":"^4"}}')
    (src / "main.ts").write_text(
        "import express from 'express';\nimport routes from './app/routes/routes';\n"
        "const app = express();\napp.use(routes);\n"
    )
    (src / "app" / "routes" / "routes.ts").write_text(
        "import { Router } from 'express';\nimport authController from './auth/auth.controller';\n"
        "const api = Router().use(authController);\n"
        "export default Router().use('/api', api);\n"
    )
    (src / "app" / "routes" / "auth" / "auth.controller.ts").write_text(
        "import { Router } from 'express';\nconst router = Router();\n"
        "router.post('/users', createUser);\nrouter.get('/user', getUser);\n"
        "export default router;\n"
    )
    graph = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    routes = {(e.http_method, e.route) for e in graph.entrypoints(framework="express")}
    graph.close()
    assert ("POST", "/api/users") in routes
    assert ("GET", "/api/user") in routes
