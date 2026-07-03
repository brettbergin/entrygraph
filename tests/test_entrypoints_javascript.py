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
