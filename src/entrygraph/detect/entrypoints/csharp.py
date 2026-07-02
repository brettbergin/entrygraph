"""C# entrypoint rules: ASP.NET Core MVC/attribute-routed controllers, minimal
API route registrations, and the language-core ``static Main`` entrypoint.

Controller rules scan ``FileExtraction.symbols`` and their captured attributes
(stored on ``RawSymbol.decorators`` as raw source text, e.g.
``[HttpGet("/x")]``). A controller is a type carrying ``[ApiController]`` /
``[Controller]`` or whose base type name contains ``Controller``. Minimal-API
routes are recognised from ``MapGet``/``MapPost``/... call references.
"""

from __future__ import annotations

import re

from entrygraph.detect.entrypoints.base import (
    EntrypointRule,
    first_string_arg,
    register,
)
from entrygraph.extract.ir import EntrypointHint, FileExtraction, RawSymbol
from entrygraph.kinds import EntrypointKind, SymbolKind

# [HttpGet("/x")] / [HttpPost] / ... -> HTTP verb; [Route("...")] -> generic.
_HTTP_ATTR = re.compile(r"^\[Http(Get|Post|Put|Delete|Patch|Head|Options)\b")
_ROUTE_ATTR = re.compile(r"^\[Route\b")
_CONTROLLER_ATTR = re.compile(r"^\[(ApiController|Controller)\b")

_MINIMAL_MAP = {
    "MapGet": "GET",
    "MapPost": "POST",
    "MapPut": "PUT",
    "MapDelete": "DELETE",
    "MapPatch": "PATCH",
}


def _type_symbols(x: FileExtraction) -> list[RawSymbol]:
    return [s for s in x.symbols if s.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)]


def _methods(x: FileExtraction) -> list[RawSymbol]:
    return [s for s in x.symbols if s.kind is SymbolKind.METHOD]


def _is_controller(t: RawSymbol) -> bool:
    if any(_CONTROLLER_ATTR.match(d) for d in t.decorators):
        return True
    return any("Controller" in b for b in t.bases)


def _aspnet_controller_routes(x: FileExtraction) -> list[EntrypointHint]:
    controllers = {t.qualified_name for t in _type_symbols(x) if _is_controller(t)}
    if not controllers:
        return []
    hints: list[EntrypointHint] = []
    for method in _methods(x):
        if method.parent_qualified_name not in controllers:
            continue
        for decorator in method.decorators:
            m = _HTTP_ATTR.match(decorator)
            if m:
                hints.append(
                    EntrypointHint(
                        rule_id="csharp.aspnet.controller-route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=method.qualified_name,
                        route=first_string_arg(decorator) or "",
                        http_methods=[m.group(1).upper()],
                        framework="aspnetcore",
                    )
                )
                break
        else:
            # No Http* verb attribute; a bare [Route("...")] still exposes it.
            route_dec = next(
                (d for d in method.decorators if _ROUTE_ATTR.match(d)), None
            )
            if route_dec is not None:
                hints.append(
                    EntrypointHint(
                        rule_id="csharp.aspnet.controller-route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=method.qualified_name,
                        route=first_string_arg(route_dec) or "",
                        http_methods=["*"],
                        framework="aspnetcore",
                    )
                )
    return hints


def _aspnet_minimal_api(x: FileExtraction) -> list[EntrypointHint]:
    hints: list[EntrypointHint] = []
    for ref in x.references:
        if ref.kind != "call":
            continue
        verb = _MINIMAL_MAP.get(ref.callee_name)
        if verb is None:
            continue
        route = first_string_arg(ref.arg_preview or "")
        hints.append(
            EntrypointHint(
                rule_id="csharp.aspnet.minimal-api",
                kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=None,  # inline lambda handler
                route=route or "",
                http_methods=[verb],
                framework="aspnetcore",
            )
        )
    return hints


def _csharp_main(x: FileExtraction) -> list[EntrypointHint]:
    hints: list[EntrypointHint] = []
    for method in _methods(x):
        if method.name == "Main" and "static" in method.modifiers:
            hints.append(
                EntrypointHint(
                    rule_id="csharp.core.main",
                    kind=EntrypointKind.MAIN,
                    handler_qualified_name=method.qualified_name,
                    name=method.qualified_name,
                    framework=None,
                )
            )
    return hints


register(
    EntrypointRule(
        "csharp.aspnet.controller-route",
        "csharp",
        "aspnetcore",
        EntrypointKind.HTTP_ROUTE,
        _aspnet_controller_routes,
    )
)
register(
    EntrypointRule(
        "csharp.aspnet.minimal-api",
        "csharp",
        "aspnetcore",
        EntrypointKind.HTTP_ROUTE,
        _aspnet_minimal_api,
    )
)
register(
    EntrypointRule(
        "csharp.core.main",
        "csharp",
        None,
        EntrypointKind.MAIN,
        _csharp_main,
    )
)
