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
    compose_route,
    first_string_arg,
    register,
)
from entrygraph.extract.ir import EntrypointHint, FileExtraction, RawSymbol
from entrygraph.kinds import EntrypointKind, SymbolKind

# [HttpGet("/x")] / [HttpPost] / ... -> HTTP verb; [Route("...")] -> generic.
_HTTP_ATTR = re.compile(r"^\[Http(Get|Post|Put|Delete|Patch|Head|Options)\b")
_ROUTE_ATTR = re.compile(r"^\[Route\b")
_CONTROLLER_ATTR = re.compile(r"^\[(ApiController|Controller)\b")
_APICONTROLLER_ATTR = re.compile(r"^\[ApiController\b")
_AREA_ATTR = re.compile(r"^\[Area\b")
_NONACTION_ATTR = re.compile(r"^\[NonAction\b")

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


def _controller_token(qname: str) -> str:
    """The `[controller]` route-token value: the class name minus a Controller suffix."""
    name = qname.rsplit(".", 1)[-1]
    return name[: -len("Controller")] if name.endswith("Controller") else name


def _class_route_prefix(t: RawSymbol) -> str:
    """A controller's class-level [Route("...")] prefix, with [controller] expanded."""
    route_dec = next((d for d in t.decorators if _ROUTE_ATTR.match(d)), None)
    prefix = first_string_arg(route_dec) if route_dec else None
    if not prefix:
        return ""
    return prefix.replace("[controller]", _controller_token(t.qualified_name))


def _is_conventional_controller(t: RawSymbol) -> bool:
    """A classic MVC controller reached by convention (`{controller}/{action}`),
    not attribute routing. `[ApiController]` mandates attribute routing, and a
    class-level `[Route]` opts the whole controller into attribute routing, so
    neither can be convention-routed."""
    return not any(_APICONTROLLER_ATTR.match(d) or _ROUTE_ATTR.match(d) for d in t.decorators)


def _is_action_method(m: RawSymbol, ctor_name: str) -> bool:
    """A public instance method that MVC exposes as a convention-routed action.

    Excludes constructors, static/override members (framework overrides like
    `OnActionExecuting`), `[NonAction]`, and anything already carrying an
    `[Http*]`/`[Route]` attribute (handled by the attribute-routing branches)."""
    if "public" not in m.modifiers or "static" in m.modifiers or "override" in m.modifiers:
        return False
    if m.name == ctor_name:
        return False
    return not any(
        _NONACTION_ATTR.match(d) or _HTTP_ATTR.match(d) or _ROUTE_ATTR.match(d)
        for d in m.decorators
    )


def _conventional_route(t: RawSymbol, action: str) -> str:
    """`[Area("Admin")]` + FooController + Bar -> /Admin/Foo/Bar."""
    area_dec = next((d for d in t.decorators if _AREA_ATTR.match(d)), None)
    area = (first_string_arg(area_dec) if area_dec else None) or ""
    parts = [area, _controller_token(t.qualified_name), action]
    return "/" + "/".join(p.strip("/") for p in parts if p and p.strip("/"))


def _aspnet_controller_routes(x: FileExtraction) -> list[EntrypointHint]:
    controller_syms = [t for t in _type_symbols(x) if _is_controller(t)]
    controllers = {t.qualified_name for t in controller_syms}
    if not controllers:
        return []
    prefixes = {t.qualified_name: _class_route_prefix(t) for t in controller_syms}
    # controllers reached by convention -> their symbol, for building /controller/action
    conventional = {t.qualified_name: t for t in controller_syms if _is_conventional_controller(t)}
    hints: list[EntrypointHint] = []
    for method in _methods(x):
        parent = method.parent_qualified_name or ""
        if parent not in controllers:
            continue
        prefix = prefixes.get(parent, "")
        verb_dec = next((d for d in method.decorators if _HTTP_ATTR.match(d)), None)
        if verb_dec is not None:
            m = _HTTP_ATTR.match(verb_dec)
            assert m is not None
            hints.append(
                EntrypointHint(
                    rule_id="csharp.aspnet.controller-route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=method.qualified_name,
                    route=compose_route(prefix, first_string_arg(verb_dec)),
                    http_methods=[m.group(1).upper()],
                    framework="aspnetcore",
                )
            )
            continue
        # No Http* verb attribute; a bare [Route("...")] still exposes it.
        route_dec = next((d for d in method.decorators if _ROUTE_ATTR.match(d)), None)
        if route_dec is not None:
            hints.append(
                EntrypointHint(
                    rule_id="csharp.aspnet.controller-route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=method.qualified_name,
                    route=compose_route(prefix, first_string_arg(route_dec)),
                    http_methods=["*"],
                    framework="aspnetcore",
                )
            )
            continue
        # No attribute at all: a convention-routed MVC action (/controller/action),
        # reachable via GET. Only for non-attribute controllers (#37).
        controller = conventional.get(parent)
        if controller is not None and _is_action_method(method, controller.name):
            hints.append(
                EntrypointHint(
                    rule_id="csharp.aspnet.mvc-conventional",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=method.qualified_name,
                    route=_conventional_route(controller, method.name),
                    http_methods=["GET"],
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
