"""Python entrypoint rules: flask, fastapi, django urls, click/typer, celery,
lambda handlers, and the language-core __main__ guard."""

from __future__ import annotations

import re

from entrygraph.detect.entrypoints.base import (
    EntrypointRule,
    first_string_arg,
    methods_kwarg,
    register,
)
from entrygraph.extract.ir import EntrypointHint, FileExtraction, RawSymbol
from entrygraph.kinds import EntrypointKind, SymbolKind

_FLASK_ROUTE = re.compile(r"^@(\w+)\.route\(")
_HTTP_VERB = re.compile(r"^@(\w+)\.(get|post|put|delete|patch|head|options)\(")
_CLICK_CMD = re.compile(r"^@(?:click\.(command|group)|(\w+)\.(command|group))\b")
_CELERY_TASK = re.compile(r"^@(?:shared_task\b|task\b|(\w+)\.task\b)")
_DJANGO_PATH_CALL = frozenset({"path", "re_path", "url"})
_LAMBDA_NAMES = frozenset({"lambda_handler", "handler"})


def _decorated(x: FileExtraction) -> list[tuple[RawSymbol, str]]:
    return [(s, d) for s in x.symbols for d in s.decorators
            if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)]


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
                )
            )
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
register(EntrypointRule("python.core.main", "python", None,
                        EntrypointKind.MAIN, _main_guard))
