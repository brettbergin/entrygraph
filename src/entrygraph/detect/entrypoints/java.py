"""Java entrypoint rules: Spring MVC routes, JAX-RS resources, and the
language-core ``public static void main`` entrypoint.

Rules scan ``FileExtraction.symbols`` and their captured annotations (stored on
``RawSymbol.decorators`` as raw source text, e.g. ``@GetMapping("/users")``).
A Spring route requires the enclosing type to carry ``@RestController`` or
``@Controller``; a JAX-RS resource requires a class-level ``@Path``.
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

# @GetMapping / @PostMapping / ... -> HTTP verb; @RequestMapping -> generic.
_SPRING_MAPPING = re.compile(r"^@(Get|Post|Put|Delete|Patch|Request)Mapping\b")
_MAPPING_VERB = {
    "Get": "GET",
    "Post": "POST",
    "Put": "PUT",
    "Delete": "DELETE",
    "Patch": "PATCH",
}
_SPRING_CONTROLLER = re.compile(r"^@(RestController|Controller)\b")

_JAXRS_HTTP = re.compile(r"^@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b")
_JAXRS_PATH = re.compile(r"^@Path\b")


def _annotation_names(decorators: list[str], pattern: re.Pattern) -> bool:
    return any(pattern.match(d) for d in decorators)


def _type_symbols(x: FileExtraction) -> list[RawSymbol]:
    return [s for s in x.symbols if s.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)]


def _methods(x: FileExtraction) -> list[RawSymbol]:
    return [s for s in x.symbols if s.kind is SymbolKind.METHOD]


def _enclosing_type(method: RawSymbol, types: list[RawSymbol]) -> RawSymbol | None:
    return next((t for t in types if t.qualified_name == method.parent_qualified_name), None)


def _spring_routes(x: FileExtraction) -> list[EntrypointHint]:
    types = _type_symbols(x)
    controllers = {
        t.qualified_name for t in types if _annotation_names(t.decorators, _SPRING_CONTROLLER)
    }
    if not controllers:
        return []
    hints = []
    for method in _methods(x):
        if method.parent_qualified_name not in controllers:
            continue
        for decorator in method.decorators:
            match = _SPRING_MAPPING.match(decorator)
            if not match:
                continue
            verb = _MAPPING_VERB.get(match.group(1))
            methods = [verb] if verb else ["*"]
            hints.append(
                EntrypointHint(
                    rule_id="java.spring.route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=method.qualified_name,
                    route=first_string_arg(decorator) or "",
                    http_methods=methods,
                    framework="spring-boot",
                )
            )
    return hints


def _jaxrs_routes(x: FileExtraction) -> list[EntrypointHint]:
    types = _type_symbols(x)
    resources = {t.qualified_name for t in types if _annotation_names(t.decorators, _JAXRS_PATH)}
    if not resources:
        return []
    hints = []
    for method in _methods(x):
        if method.parent_qualified_name not in resources:
            continue
        verb = next(
            (m.group(1) for d in method.decorators if (m := _JAXRS_HTTP.match(d))),
            None,
        )
        if verb is None:
            continue
        route = next((first_string_arg(d) for d in method.decorators if _JAXRS_PATH.match(d)), None)
        hints.append(
            EntrypointHint(
                rule_id="java.jaxrs",
                kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=method.qualified_name,
                route=route or "",
                http_methods=[verb],
                framework="jax-rs",
            )
        )
    return hints


_MICRONAUT_CONTROLLER = re.compile(r"^@Controller\b")
_MICRONAUT_MAPPING = re.compile(r"^@(Get|Post|Put|Delete|Patch)\b")
_SERVLET_METHODS = frozenset({"doGet", "doPost", "doPut", "doDelete"})


def _micronaut_routes(x: FileExtraction) -> list[EntrypointHint]:
    types = _type_symbols(x)
    controllers = {
        t.qualified_name for t in types if _annotation_names(t.decorators, _MICRONAUT_CONTROLLER)
    }
    if not controllers:
        return []
    hints = []
    for method in _methods(x):
        if method.parent_qualified_name not in controllers:
            continue
        for decorator in method.decorators:
            m = _MICRONAUT_MAPPING.match(decorator)
            if m:
                hints.append(
                    EntrypointHint(
                        rule_id="java.micronaut.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=method.qualified_name,
                        route=first_string_arg(decorator) or "",
                        http_methods=[m.group(1).upper()],
                        framework="micronaut",
                    )
                )
    return hints


def _servlet_routes(x: FileExtraction) -> list[EntrypointHint]:
    """Classes extending HttpServlet expose doGet/doPost/... as HTTP handlers."""
    servlets = {
        t.qualified_name for t in _type_symbols(x) if any("HttpServlet" in b for b in t.bases)
    }
    if not servlets:
        return []
    hints = []
    for method in _methods(x):
        if method.parent_qualified_name in servlets and method.name in _SERVLET_METHODS:
            hints.append(
                EntrypointHint(
                    rule_id="java.servlet.route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=method.qualified_name,
                    route="",
                    http_methods=[method.name.replace("do", "").upper()],
                    framework="servlet-api",
                )
            )
    return hints


def _java_main(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for method in _methods(x):
        if (
            method.name == "main"
            and "static" in method.modifiers
            and "public" in method.modifiers
            and method.signature
            and "String[]" in method.signature
        ):
            hints.append(
                EntrypointHint(
                    rule_id="java.core.main",
                    kind=EntrypointKind.MAIN,
                    handler_qualified_name=method.qualified_name,
                    name=method.qualified_name,
                    framework=None,
                )
            )
    return hints


register(
    EntrypointRule(
        "java.spring.route", "java", "spring-boot", EntrypointKind.HTTP_ROUTE, _spring_routes
    )
)
register(EntrypointRule("java.jaxrs", "java", "jax-rs", EntrypointKind.HTTP_ROUTE, _jaxrs_routes))
register(
    EntrypointRule(
        "java.micronaut.route", "java", "micronaut", EntrypointKind.HTTP_ROUTE, _micronaut_routes
    )
)
register(
    EntrypointRule(
        "java.servlet.route", "java", "servlet-api", EntrypointKind.HTTP_ROUTE, _servlet_routes
    )
)
register(EntrypointRule("java.core.main", "java", None, EntrypointKind.MAIN, _java_main))
