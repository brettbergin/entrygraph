"""PHP entrypoint rules: Laravel routes, Symfony route attributes, WordPress
hooks, and the language-core namespace-less script entrypoint.

Laravel route DSLs are ``Route::get('/x', ...)`` scoped calls — extracted as
call references with ``receiver_text == "Route"`` — so those rules match against
references rather than symbol decorators (like the Ruby route rules). Symfony
uses PHP 8 ``#[Route('/x')]`` attributes, which the extractor emits as symbol
decorators (like Java annotations), so that rule matches decorators.
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

_LARAVEL_VERBS = frozenset({"get", "post", "put", "delete", "patch", "any", "match"})
_LARAVEL_RESOURCE = frozenset({"resource", "apiResource"})
_SYMFONY_ROUTE = re.compile(r"#\[\s*Route\s*\(")
_WORDPRESS_HOOKS = frozenset({"add_action", "add_filter"})


def _resource_param(name: str) -> str:
    """The Laravel resource param: singular of the last (dot-nested) segment —
    `photos` -> `{photo}`, `blog.comments` -> `{comment}`."""
    seg = name.strip("/").split(".")[-1]
    singular = seg[:-1] if seg.endswith("s") and not seg.endswith("ss") else seg
    return "{" + (singular or "id") + "}"


def _resource_routes(name: str, is_api: bool) -> list[tuple[str, str]]:
    """Expand Route::resource/apiResource into its REST (method, path) routes.

    apiResource omits the HTML create/edit forms that resource adds.
    """
    base = "/" + name.strip("/").replace(".", "/")
    param = _resource_param(name)
    routes = [
        ("GET", base),  # index
        ("POST", base),  # store
        ("GET", f"{base}/{param}"),  # show
        ("PUT", f"{base}/{param}"),  # update
        ("DELETE", f"{base}/{param}"),  # destroy
    ]
    if not is_api:
        routes += [
            ("GET", f"{base}/create"),  # create form
            ("GET", f"{base}/{param}/edit"),  # edit form
        ]
    return routes


def _laravel_routes(x: FileExtraction) -> list[EntrypointHint]:
    if not re.search(r"(^|/)routes/[^/]+\.php$", x.path):
        return []
    hints = []
    for ref in x.references:
        if ref.kind != "call" or ref.receiver_text != "Route" or not ref.arg_preview:
            continue
        if ref.callee_name in _LARAVEL_VERBS:
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            hints.append(
                EntrypointHint(
                    rule_id="php.laravel.route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=None,  # handler resolved as a call edge
                    route=route if route is not None else "",
                    http_methods=["*"]
                    if ref.callee_name in ("any", "match")
                    else [ref.callee_name.upper()],
                    framework="laravel",
                    metadata={"registration": ref.arg_preview},
                )
            )
        elif ref.callee_name in _LARAVEL_RESOURCE:
            # Route::resource('photos', Ctrl) / apiResource(...) registers a fixed
            # set of REST routes at once; expand them so they aren't all missed.
            name = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if not name:
                continue
            for method, route in _resource_routes(name, ref.callee_name == "apiResource"):
                hints.append(
                    EntrypointHint(
                        rule_id="php.laravel.resource",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=None,
                        route=route,
                        http_methods=[method],
                        framework="laravel",
                        metadata={"registration": ref.arg_preview},
                    )
                )
    return hints


def _symfony_routes(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol in x.symbols:
        if symbol.kind not in (SymbolKind.METHOD, SymbolKind.FUNCTION):
            continue
        for decorator in symbol.decorators:
            if _SYMFONY_ROUTE.search(decorator):
                hints.append(
                    EntrypointHint(
                        rule_id="php.symfony.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=symbol.qualified_name,
                        route=first_string_arg(decorator) or "",
                        http_methods=["*"],
                        framework="symfony",
                    )
                )
    return hints


def _wordpress_hooks(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.receiver_text is None
            and ref.callee_name in _WORDPRESS_HOOKS
            and ref.arg_preview
        ):
            hints.append(
                EntrypointHint(
                    rule_id="php.wordpress.hook",
                    kind=EntrypointKind.EVENT_HANDLER,
                    handler_qualified_name=None,  # callback resolved as call edge
                    name=first_string_arg("(" + ref.arg_preview.lstrip("(")),
                    framework="wordpress",
                    metadata={"registration": ref.arg_preview},
                )
            )
    return hints


def _script(x: FileExtraction) -> list[EntrypointHint]:
    """A namespace-less top-level ``index.php`` with module-level references.

    The extractor sets ``module_path`` to the file's PHP namespace when one is
    present, otherwise to the directory-derived path (which for ``index.php``
    ends in ``.index`` or equals ``index``). A namespaced ``index.php`` is a
    library file, not a script entrypoint, so we require the directory-derived
    (namespace-less) form. We also require at least one module-level reference
    (a ``caller_qualified_name is None`` call) so an empty file is not flagged.
    """
    if not x.path.endswith("index.php"):
        return []
    if not (x.module_path == "index" or x.module_path.endswith(".index")):
        return []  # namespaced -> not a bare script
    if not any(ref.caller_qualified_name is None for ref in x.references):
        return []
    return [
        EntrypointHint(
            rule_id="php.core.script",
            kind=EntrypointKind.MAIN,
            handler_qualified_name=None,  # the module itself
            name=x.module_path,
            framework=None,
        )
    ]


register(
    EntrypointRule(
        "php.laravel.route", "php", "laravel", EntrypointKind.HTTP_ROUTE, _laravel_routes
    )
)
register(
    EntrypointRule(
        "php.symfony.route", "php", "symfony", EntrypointKind.HTTP_ROUTE, _symfony_routes
    )
)
register(
    EntrypointRule(
        "php.wordpress.hook", "php", "wordpress", EntrypointKind.EVENT_HANDLER, _wordpress_hooks
    )
)
register(EntrypointRule("php.core.script", "php", None, EntrypointKind.MAIN, _script))
