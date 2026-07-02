"""Rust entrypoint rules: the crate ``main`` (and ``#[tokio::main]`` /
``#[actix_web::main]`` async mains), axum router routes, actix-web/rocket route
attribute macros, and clap ``#[derive(Parser)]`` CLI commands.

Rules are IR-driven: axum routes read ``route(...)`` call references (the same
low-fidelity tradeoff as the Express rule — the handler lives inside the second
argument, so ``handler_qualified_name`` is left None and the raw registration is
stashed in ``metadata``). actix/rocket/clap rules read attribute macros captured
on ``RawSymbol.decorators`` as raw source text (``#[get("/x")]``).
"""

from __future__ import annotations

import re

from entrygraph.detect.entrypoints.base import (
    EntrypointRule,
    first_string_arg,
    register,
)
from entrygraph.extract.ir import EntrypointHint, FileExtraction
from entrygraph.kinds import EntrypointKind, SymbolKind

# #[get("/x")] / #[post(..)] / ... on a handler function -> HTTP verb.
_ROUTE_ATTR = re.compile(r"^#\[\s*(get|post|put|delete|patch|head|route)\s*\(")
_VERB = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
}
# #[derive(.. Parser ..)] anywhere in the derive list.
_DERIVE_PARSER = re.compile(r"^#\[\s*derive\s*\(.*\bParser\b")
# #[tokio::main] / #[actix_web::main] async entrypoints.
_ASYNC_MAIN = re.compile(r"^#\[\s*(?:tokio|actix_web|async_std)::main\b")


def _rust_main(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol in x.symbols:
        if symbol.kind is not SymbolKind.FUNCTION:
            continue
        is_crate_main = symbol.name == "main" and symbol.parent_qualified_name is None
        is_async_main = any(_ASYNC_MAIN.match(d) for d in symbol.decorators)
        if is_crate_main or is_async_main:
            hints.append(
                EntrypointHint(
                    rule_id="rust.core.main",
                    kind=EntrypointKind.MAIN,
                    handler_qualified_name=symbol.qualified_name,
                    name=symbol.qualified_name,
                    span=symbol.span,
                    framework=None,
                )
            )
    return hints


def _axum_routes(x: FileExtraction) -> list[EntrypointHint]:
    """`Router::new().route("/x", post(handler))` — the handler is buried in the
    second argument, so we regex it out of the preview but can't statically bind
    it (the Express-rule fidelity tradeoff)."""
    hints = []
    for ref in x.references:
        if ref.kind != "call" or ref.callee_name != "route" or not ref.arg_preview:
            continue
        route = first_string_arg("(" + ref.arg_preview.lstrip("("))
        if route is None or not route.startswith("/"):
            continue
        method, handler = _axum_method_and_handler(ref.arg_preview)
        hints.append(
            EntrypointHint(
                rule_id="rust.axum.route",
                kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=None,
                route=route,
                http_methods=[method],
                framework="axum",
                metadata={"handler_text": handler, "registration": ref.arg_preview},
            )
        )
    return hints


_AXUM_HANDLER = re.compile(r"\b(get|post|put|delete|patch|head|options)\s*\(\s*([A-Za-z_]\w*)")


def _axum_method_and_handler(preview: str) -> tuple[str, str | None]:
    match = _AXUM_HANDLER.search(preview)
    if match:
        return match.group(1).upper(), match.group(2)
    return "*", None


def _route_attr_rule(framework: str):
    """actix-web and rocket both register routes with `#[get("/x")]`-style
    attribute macros on the handler function; one matcher, framework by param."""

    def matcher(x: FileExtraction) -> list[EntrypointHint]:
        hints = []
        for symbol in x.symbols:
            if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                continue
            for decorator in symbol.decorators:
                m = _ROUTE_ATTR.match(decorator)
                if not m:
                    continue
                verb = _VERB.get(m.group(1), "*")
                hints.append(
                    EntrypointHint(
                        rule_id=f"rust.{framework}.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=symbol.qualified_name,
                        route=first_string_arg(decorator) or "",
                        http_methods=[verb],
                        framework=framework,
                        span=symbol.span,
                    )
                )
        return hints

    return matcher


def _clap_commands(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol in x.symbols:
        if symbol.kind not in (SymbolKind.STRUCT, SymbolKind.CLASS):
            continue
        if any(_DERIVE_PARSER.match(d) for d in symbol.decorators):
            hints.append(
                EntrypointHint(
                    rule_id="rust.clap.command",
                    kind=EntrypointKind.CLI_COMMAND,
                    handler_qualified_name=symbol.qualified_name,
                    name=symbol.name,
                    framework="clap",
                    span=symbol.span,
                )
            )
    return hints


register(EntrypointRule("rust.core.main", "rust", None, EntrypointKind.MAIN, _rust_main))
register(EntrypointRule("rust.axum.route", "rust", "axum", EntrypointKind.HTTP_ROUTE, _axum_routes))
register(
    EntrypointRule(
        "rust.actix.route",
        "rust",
        "actix-web",
        EntrypointKind.HTTP_ROUTE,
        _route_attr_rule("actix-web"),
    )
)
register(
    EntrypointRule(
        "rust.rocket.route", "rust", "rocket", EntrypointKind.HTTP_ROUTE, _route_attr_rule("rocket")
    )
)
register(
    EntrypointRule("rust.clap.command", "rust", "clap", EntrypointKind.CLI_COMMAND, _clap_commands)
)
