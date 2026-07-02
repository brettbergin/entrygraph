"""Ruby entrypoint rules: Sinatra routes, Rails route DSL, and Rake tasks.

Ruby route DSLs are bare top-level method calls (``get '/x' do ... end``), not
decorators, so these rules match against extracted call references rather than
symbol decorators. Sinatra handlers are inline blocks with no named symbol, so
the handler qualified name is the enclosing method (usually None at file top
level) — the block body's calls still resolve as ordinary edges.
"""

from __future__ import annotations

from entrygraph.detect.entrypoints.base import (
    EntrypointRule,
    first_string_arg,
    register,
)
from entrygraph.extract.ir import EntrypointHint, FileExtraction
from entrygraph.kinds import EntrypointKind

_SINATRA_VERBS = frozenset({"get", "post", "put", "delete", "patch"})
_RAILS_VERBS = frozenset({"get", "post", "put", "patch", "delete", "resources", "resource", "root"})


def _sinatra_routes(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.receiver_text is None
            and ref.callee_name in _SINATRA_VERBS
            and ref.arg_preview
        ):
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if route is not None and route.startswith("/"):
                hints.append(
                    EntrypointHint(
                        rule_id="ruby.sinatra.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=ref.caller_qualified_name,
                        route=route,
                        http_methods=[ref.callee_name.upper()],
                        framework="sinatra",
                    )
                )
    return hints


def _rails_routes(x: FileExtraction) -> list[EntrypointHint]:
    if not x.path.endswith("config/routes.rb"):
        return []
    hints = []
    for ref in x.references:
        if ref.kind != "call" or ref.receiver_text is not None:
            continue
        if ref.callee_name not in _RAILS_VERBS:
            continue
        route = None
        if ref.arg_preview:
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
        hints.append(
            EntrypointHint(
                rule_id="ruby.rails.routes",
                kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=None,  # handler resolved as a normal call edge
                route=route if route is not None else "",
                http_methods=["*"] if ref.callee_name in ("resources", "resource", "root")
                else [ref.callee_name.upper()],
                framework="rails",
                metadata={"registration": ref.arg_preview or ref.callee_name},
            )
        )
    return hints


def _rake_tasks(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for ref in x.references:
        if ref.kind == "call" and ref.receiver_text is None and ref.callee_name == "task":
            name = None
            if ref.arg_preview:
                name = first_string_arg("(" + ref.arg_preview.lstrip("("))
                if name is None:
                    # symbol arg like `:build` — strip the leading colon
                    stripped = ref.arg_preview.strip("()").lstrip(":").split(" ", 1)[0]
                    stripped = stripped.split("=", 1)[0].strip().rstrip(",")
                    name = stripped or None
            hints.append(
                EntrypointHint(
                    rule_id="ruby.rake.task",
                    kind=EntrypointKind.CLI_COMMAND,
                    handler_qualified_name=ref.caller_qualified_name,
                    name=name or "task",
                    framework="rake",
                )
            )
    return hints


def _grape_routes(x: FileExtraction) -> list[EntrypointHint]:
    """Grape API classes: class-body `get '/x'` / `post '/y'` declarations."""
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.receiver_text is None
            and ref.callee_name in _SINATRA_VERBS
            and ref.arg_preview
        ):
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if route is not None:
                hints.append(EntrypointHint(
                    rule_id="ruby.grape.route", kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=ref.caller_qualified_name,
                    route=route, http_methods=[ref.callee_name.upper()], framework="grape"))
    return hints


def _sidekiq_workers(x: FileExtraction) -> list[EntrypointHint]:
    """Classes including Sidekiq::Worker/Job expose `perform` as a task handler."""
    worker_modules = {
        ref.caller_qualified_name
        for ref in x.references
        if ref.callee_name == "include" and ref.arg_preview
        and "Sidekiq" in ref.arg_preview
    }
    hints = []
    for symbol in x.symbols:
        if symbol.name == "perform" and (
            symbol.parent_qualified_name in worker_modules or not worker_modules
        ) and worker_modules:
            hints.append(EntrypointHint(
                rule_id="ruby.sidekiq.worker", kind=EntrypointKind.TASK,
                handler_qualified_name=symbol.qualified_name,
                name=symbol.parent_qualified_name or symbol.name, framework="sidekiq"))
    return hints


register(EntrypointRule("ruby.sinatra.route", "ruby", "sinatra",
                        EntrypointKind.HTTP_ROUTE, _sinatra_routes))
register(EntrypointRule("ruby.grape.route", "ruby", "grape",
                        EntrypointKind.HTTP_ROUTE, _grape_routes))
register(EntrypointRule("ruby.sidekiq.worker", "ruby", "sidekiq",
                        EntrypointKind.TASK, _sidekiq_workers))
register(EntrypointRule("ruby.rails.routes", "ruby", "rails",
                        EntrypointKind.HTTP_ROUTE, _rails_routes))
register(EntrypointRule("ruby.rake.task", "ruby", "rake",
                        EntrypointKind.CLI_COMMAND, _rake_tasks))
