"""Python entrypoint rules: flask, fastapi, django urls, click/typer, celery,
lambda handlers, and the language-core __main__ guard."""

from __future__ import annotations

import re

from entrygraph.detect.entrypoints.base import (
    EntrypointRule,
    first_string_arg,
    identifier_args,
    methods_kwarg,
    register,
    tainted_params,
)
from entrygraph.extract.ir import EntrypointHint, FileExtraction, RawSymbol
from entrygraph.kinds import EntrypointKind, SymbolKind

_FLASK_ROUTE = re.compile(r"^@(\w+)\.route\(")
_HTTP_VERB = re.compile(r"^@(\w+)\.(get|post|put|delete|patch|head|options)\(")
_CLICK_CMD = re.compile(r"^@(?:click\.(command|group)|(\w+)\.(command|group))\b")
_CELERY_TASK = re.compile(r"^@(?:shared_task\b|task\b|(\w+)\.task\b)")
_MIDDLEWARE_DECORATOR = re.compile(
    r"^@(\w+)\.(before_request|after_request|before_first_request|errorhandler|middleware|teardown_request)\b"
)
_DJANGO_PATH_CALL = frozenset({"path", "re_path", "url"})
_LAMBDA_NAMES = frozenset({"lambda_handler", "handler"})


def _decorated(x: FileExtraction) -> list[tuple[RawSymbol, str]]:
    return [(s, d) for s in x.symbols for d in s.decorators
            if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)]


def _taint_meta(symbol: RawSymbol, kind: str) -> dict:
    params = tainted_params(symbol.signature, kind)
    return {"tainted_params": params} if params else {}


def _flask_routes(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol, decorator in _decorated(x):
        route = None
        methods: list[str] = []
        if _FLASK_ROUTE.match(decorator):
            route = first_string_arg(decorator)
            methods = methods_kwarg(decorator) or ["GET"]
        else:
            verb = _HTTP_VERB.match(decorator)
            if verb:
                route = first_string_arg(decorator)
                methods = [verb.group(2).upper()]
        if route is not None:
            hints.append(
                EntrypointHint(
                    rule_id="python.flask.route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=symbol.qualified_name,
                    route=route,
                    http_methods=methods,
                    framework="flask",
                    metadata=_taint_meta(symbol, "http_route"),
                )
            )
    return hints


def _flask_add_url_rule(x: FileExtraction) -> list[EntrypointHint]:
    """Call-based route registration: app.add_url_rule("/x", view_func=handler)."""
    hints = []
    for ref in x.references:
        if ref.kind != "call" or ref.callee_name != "add_url_rule" or not ref.arg_preview:
            continue
        route = first_string_arg("(" + ref.arg_preview.lstrip("("))
        handler = None
        for name in identifier_args(ref.arg_preview):
            candidate = f"{x.module_path}.{name}"
            if any(s.qualified_name == candidate for s in x.symbols):
                handler = candidate
                break
        hints.append(
            EntrypointHint(
                rule_id="python.flask.add_url_rule",
                kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=handler,
                route=route if route is not None else "",
                http_methods=["*"],
                framework="flask",
                metadata={"registration": ref.arg_preview},
            )
        )
    return hints


def _middleware(x: FileExtraction) -> list[EntrypointHint]:
    """before_request / after_request / errorhandler / app.middleware wrappers."""
    hints = []
    for symbol, decorator in _decorated(x):
        m = _MIDDLEWARE_DECORATOR.match(decorator)
        if m:
            framework = "fastapi" if m.group(2) == "middleware" else "flask"
            hints.append(
                EntrypointHint(
                    rule_id="python.web.middleware",
                    kind=EntrypointKind.MIDDLEWARE,
                    handler_qualified_name=symbol.qualified_name,
                    name=m.group(2),
                    framework=framework,
                )
            )
    return hints


def _fastapi_routes(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol, decorator in _decorated(x):
        verb = _HTTP_VERB.match(decorator)
        if verb:
            route = first_string_arg(decorator)
            if route is not None:
                hints.append(
                    EntrypointHint(
                        rule_id="python.fastapi.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=symbol.qualified_name,
                        route=route,
                        http_methods=[verb.group(2).upper()],
                        framework="fastapi",
                        metadata=_taint_meta(symbol, "http_route"),
                    )
                )
    return hints


def _django_urls(x: FileExtraction) -> list[EntrypointHint]:
    if not x.path.endswith("urls.py"):
        return []
    hints = []
    for ref in x.references:
        if ref.kind == "call" and ref.callee_name in _DJANGO_PATH_CALL and ref.arg_preview:
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            hints.append(
                EntrypointHint(
                    rule_id="python.django.urls",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=None,  # handler resolved as a normal call edge
                    route=route if route is not None else "",
                    http_methods=["*"],
                    framework="django",
                    metadata={"registration": ref.arg_preview},
                )
            )
    return hints


def _click_commands(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol, decorator in _decorated(x):
        if _CLICK_CMD.match(decorator):
            hints.append(
                EntrypointHint(
                    rule_id="python.click.command",
                    kind=EntrypointKind.CLI_COMMAND,
                    handler_qualified_name=symbol.qualified_name,
                    name=first_string_arg(decorator) or symbol.name,
                    framework="click",
                )
            )
    return hints


def _celery_tasks(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol, decorator in _decorated(x):
        if _CELERY_TASK.match(decorator):
            hints.append(
                EntrypointHint(
                    rule_id="python.celery.task",
                    kind=EntrypointKind.TASK,
                    handler_qualified_name=symbol.qualified_name,
                    name=symbol.name,
                    framework="celery",
                )
            )
    return hints


def _lambda_handlers(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for symbol in x.symbols:
        if (
            symbol.kind is SymbolKind.FUNCTION
            and symbol.name in _LAMBDA_NAMES
            and symbol.signature
            and "event" in symbol.signature
            and "context" in symbol.signature
        ):
            hints.append(
                EntrypointHint(
                    rule_id="python.lambda.handler",
                    kind=EntrypointKind.LAMBDA_HANDLER,
                    handler_qualified_name=symbol.qualified_name,
                    name=symbol.name,
                    framework="aws-lambda",
                    metadata=_taint_meta(symbol, "lambda_handler"),
                )
            )
    return hints


_VIEW_CONFIG = re.compile(r"^@view_config\b")
_DRAMATIQ_ACTOR = re.compile(r"^@(?:dramatiq\.actor|actor)\b")
_AIRFLOW_TASK = re.compile(r"^@(?:task|dag)\b")
_AIOHTTP_ADD = frozenset({"add_get", "add_post", "add_put", "add_delete", "add_route"})


def _sanic_bottle_routes(framework: str):
    """@app.route / @app.get style routes, framework-labelled (sanic, bottle)."""

    def matcher(x: FileExtraction) -> list[EntrypointHint]:
        hints = []
        for symbol, decorator in _decorated(x):
            route = methods = None
            if _FLASK_ROUTE.match(decorator):
                route, methods = first_string_arg(decorator), methods_kwarg(decorator) or ["GET"]
            else:
                verb = _HTTP_VERB.match(decorator)
                if verb:
                    route, methods = first_string_arg(decorator), [verb.group(2).upper()]
            if route is not None:
                hints.append(EntrypointHint(
                    rule_id=f"python.{framework}.route", kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=symbol.qualified_name, route=route,
                    http_methods=methods, framework=framework,
                    metadata=_taint_meta(symbol, "http_route")))
        return hints

    return matcher


def _decorator_rule(pattern, rule_id, kind, framework):
    """Simple decorator-matched entrypoint (pyramid view, dramatiq actor, airflow task)."""

    def matcher(x: FileExtraction) -> list[EntrypointHint]:
        hints = []
        for symbol, decorator in _decorated(x):
            if pattern.match(decorator):
                hints.append(EntrypointHint(
                    rule_id=rule_id, kind=kind,
                    handler_qualified_name=symbol.qualified_name,
                    name=symbol.name, framework=framework,
                    route=first_string_arg(decorator) if kind is EntrypointKind.HTTP_ROUTE else None,
                    metadata=_taint_meta(symbol, kind.value)))
        return hints

    return matcher


def _aiohttp_routes(x: FileExtraction) -> list[EntrypointHint]:
    """router.add_get('/x', handler) / @routes.get('/x') registrations."""
    hints = []
    for ref in x.references:
        if ref.kind == "call" and ref.callee_name in _AIOHTTP_ADD and ref.arg_preview:
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            hints.append(EntrypointHint(
                rule_id="python.aiohttp.route", kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=None, route=route or "",
                http_methods=[ref.callee_name.replace("add_", "").upper()], framework="aiohttp",
                metadata={"registration": ref.arg_preview}))
    for symbol, decorator in _decorated(x):
        verb = _HTTP_VERB.match(decorator.replace("@routes.", "@r."))
        if decorator.startswith("@routes.") and verb:
            route = first_string_arg(decorator)
            if route is not None:
                hints.append(EntrypointHint(
                    rule_id="python.aiohttp.route", kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=symbol.qualified_name, route=route,
                    http_methods=[verb.group(2).upper()], framework="aiohttp",
                    metadata=_taint_meta(symbol, "http_route")))
    return hints


def _main_guard(x: FileExtraction) -> list[EntrypointHint]:
    if ("main_guard", x.module_path) not in x.framework_signals:
        return []
    return [
        EntrypointHint(
            rule_id="python.core.main",
            kind=EntrypointKind.MAIN,
            handler_qualified_name=None,  # the module itself
            name=x.module_path,
        )
    ]


register(EntrypointRule("python.flask.route", "python", "flask",
                        EntrypointKind.HTTP_ROUTE, _flask_routes))
register(EntrypointRule("python.flask.add_url_rule", "python", "flask",
                        EntrypointKind.HTTP_ROUTE, _flask_add_url_rule))
register(EntrypointRule("python.flask.middleware", "python", "flask",
                        EntrypointKind.MIDDLEWARE, _middleware))
register(EntrypointRule("python.fastapi.middleware", "python", "fastapi",
                        EntrypointKind.MIDDLEWARE, _middleware))
register(EntrypointRule("python.fastapi.route", "python", "fastapi",
                        EntrypointKind.HTTP_ROUTE, _fastapi_routes))
register(EntrypointRule("python.django.urls", "python", "django",
                        EntrypointKind.HTTP_ROUTE, _django_urls))
register(EntrypointRule("python.click.command", "python", "click",
                        EntrypointKind.CLI_COMMAND, _click_commands))
register(EntrypointRule("python.typer.command", "python", "typer",
                        EntrypointKind.CLI_COMMAND, _click_commands))
register(EntrypointRule("python.celery.task", "python", "celery",
                        EntrypointKind.TASK, _celery_tasks))
register(EntrypointRule("python.lambda.handler", "python", "aws-lambda",
                        EntrypointKind.LAMBDA_HANDLER, _lambda_handlers))
register(EntrypointRule("python.sanic.route", "python", "sanic",
                        EntrypointKind.HTTP_ROUTE, _sanic_bottle_routes("sanic")))
register(EntrypointRule("python.bottle.route", "python", "bottle",
                        EntrypointKind.HTTP_ROUTE, _sanic_bottle_routes("bottle")))
register(EntrypointRule("python.aiohttp.route", "python", "aiohttp",
                        EntrypointKind.HTTP_ROUTE, _aiohttp_routes))
register(EntrypointRule("python.pyramid.view", "python", "pyramid",
                        EntrypointKind.HTTP_ROUTE,
                        _decorator_rule(_VIEW_CONFIG, "python.pyramid.view",
                                        EntrypointKind.HTTP_ROUTE, "pyramid")))
register(EntrypointRule("python.dramatiq.actor", "python", "dramatiq",
                        EntrypointKind.TASK,
                        _decorator_rule(_DRAMATIQ_ACTOR, "python.dramatiq.actor",
                                        EntrypointKind.TASK, "dramatiq")))
register(EntrypointRule("python.airflow.task", "python", "airflow",
                        EntrypointKind.TASK,
                        _decorator_rule(_AIRFLOW_TASK, "python.airflow.task",
                                        EntrypointKind.TASK, "airflow")))
register(EntrypointRule("python.core.main", "python", None,
                        EntrypointKind.MAIN, _main_guard))
