"""Ruby entrypoint rules: Sinatra routes, Rails route DSL, and Rake tasks.

Ruby route DSLs are bare top-level method calls (``get '/x' do ... end``), not
decorators, so these rules match against extracted call references rather than
symbol decorators. Sinatra handlers are inline blocks with no named symbol, so
the handler qualified name is the enclosing method (usually None at file top
level) — the block body's calls still resolve as ordinary edges.
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

_SINATRA_VERBS = frozenset({"get", "post", "put", "delete", "patch"})
_RAILS_VERBS = frozenset({"get", "post", "put", "patch", "delete", "resources", "resource", "root"})


def _is_rails_routes_file(path: str) -> bool:
    """The main `config/routes.rb` plus split route files loaded via `draw(:x)`
    (`config/routes/api.rb`, `config/routes/admin.rb`, ...). Missing the split
    files hid most of the route surface on large Rails apps (mastodon/forem)."""
    return path.endswith("config/routes.rb") or "/config/routes/" in f"/{path}"


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
    if not _is_rails_routes_file(x.path):
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
                http_methods=["*"]
                if ref.callee_name in ("resources", "resource", "root")
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
                hints.append(
                    EntrypointHint(
                        rule_id="ruby.grape.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=ref.caller_qualified_name,
                        route=route,
                        http_methods=[ref.callee_name.upper()],
                        framework="grape",
                    )
                )
    return hints


def _sidekiq_workers(x: FileExtraction) -> list[EntrypointHint]:
    """Classes including Sidekiq::Worker/Job expose `perform` as a task handler."""
    worker_modules = {
        ref.caller_qualified_name
        for ref in x.references
        if ref.callee_name == "include" and ref.arg_preview and "Sidekiq" in ref.arg_preview
    }
    hints = []
    for symbol in x.symbols:
        if (
            symbol.name == "perform"
            and (symbol.parent_qualified_name in worker_modules or not worker_modules)
            and worker_modules
        ):
            hints.append(
                EntrypointHint(
                    rule_id="ruby.sidekiq.worker",
                    kind=EntrypointKind.TASK,
                    handler_qualified_name=symbol.qualified_name,
                    name=symbol.parent_qualified_name or symbol.name,
                    framework="sidekiq",
                )
            )
    return hints


# ---------------- graphql-ruby ----------------

# base-class shapes marking a graphql-ruby type / mutation / resolver class
_GQL_TYPE_BASE = re.compile(r"(::BaseObject$|^GraphQL::Schema::Object$|^Types::Base)")
_GQL_MUTATION_BASE = re.compile(r"(BaseMutation$|^GraphQL::Schema::(RelayClassic)?Mutation$)")
_GQL_RESOLVER_BASE = re.compile(r"(BaseResolver$|^GraphQL::Schema::Resolver$)")
_GQL_RESOLVER_OPT = re.compile(r"(?:resolver|mutation)\s*(?:=>|:)\s*([A-Za-z_][\w:]*)")
_GQL_ROOT_OPERATIONS = {
    "QueryType": "query",
    "MutationType": "mutation",
    "SubscriptionType": "subscription",
}


def _camelize(name: str) -> str:
    """graphql-ruby camelizes snake_case field names by default."""
    head, *rest = name.split("_")
    return head + "".join(s.title() for s in rest)


def _graphql_field_hints(x: FileExtraction) -> list[EntrypointHint]:
    """`field :posts, ...` declarations in graphql-ruby type classes.

    The convention handler is an instance method named after the field; a
    ``resolver:``/``mutation:`` option delegates to a class instead (recorded in
    metadata, handler left unbound for the linking pass / span fallback).
    """
    type_classes = [
        s
        for s in x.symbols
        if s.kind is SymbolKind.CLASS and any(_GQL_TYPE_BASE.search(b) for b in s.bases)
    ]
    if not type_classes:
        return []
    # innermost-first so a nested class wins span containment
    type_classes.sort(key=lambda s: s.span.start_line, reverse=True)
    hints = []
    for ref in x.references:
        if ref.kind != "call" or ref.receiver_text is not None or ref.callee_name != "field":
            continue
        if not ref.arg_preview:
            continue
        cls = next(
            (
                s
                for s in type_classes
                if s.span.start_line <= ref.span.start_line <= s.span.end_line
            ),
            None,
        )
        if cls is None:
            continue
        name = first_string_arg("(" + ref.arg_preview.lstrip("("))
        if name is None:
            # symbol first arg like `:posts` — same fallback the rake rule uses
            stripped = ref.arg_preview.strip("()").lstrip(":").split(",", 1)[0]
            name = stripped.split(" ", 1)[0].strip() or None
        if not name:
            continue
        operation = _GQL_ROOT_OPERATIONS.get(cls.name, "field")
        parent = cls.name.removesuffix("Type") or cls.name
        resolver_opt = _GQL_RESOLVER_OPT.search(ref.arg_preview)
        metadata: dict = {"operation": operation, "parent_type": parent}
        if resolver_opt:
            metadata["resolver_class"] = resolver_opt.group(1)
        hints.append(
            EntrypointHint(
                rule_id="ruby.graphql-ruby.field",
                kind=EntrypointKind.GRAPHQL_RESOLVER,
                handler_qualified_name=None if resolver_opt else f"{cls.qualified_name}.{name}",
                route=f"{parent}.{_camelize(name)}",
                name=name,
                framework="graphql-ruby",
                span=ref.span,
                metadata=metadata,
            )
        )
    return hints


def _graphql_resolver_classes(x: FileExtraction) -> list[EntrypointHint]:
    """Mutation/Resolver classes: `class CreateOrder < BaseMutation` with a
    `resolve` method. Mutations get a best-effort `Mutation.<camelized name>`
    route (the default when mounted as `field :create_order, mutation: ...`);
    plain resolvers leave the route to the mounting field's hint."""
    hints = []
    for cls in x.symbols:
        if cls.kind is not SymbolKind.CLASS:
            continue
        if any(_GQL_MUTATION_BASE.search(b) for b in cls.bases):
            operation, route = "mutation", f"Mutation.{cls.name[:1].lower()}{cls.name[1:]}"
        elif any(_GQL_RESOLVER_BASE.search(b) for b in cls.bases):
            operation, route = "field", None
        else:
            continue
        hints.append(
            EntrypointHint(
                rule_id="ruby.graphql-ruby.resolver",
                kind=EntrypointKind.GRAPHQL_RESOLVER,
                handler_qualified_name=f"{cls.qualified_name}.resolve",
                route=route,
                name=cls.name,
                framework="graphql-ruby",
                span=cls.span,
                metadata={"operation": operation, "parent_type": "Mutation" if route else None},
            )
        )
    return hints


register(
    EntrypointRule(
        "ruby.sinatra.route", "ruby", "sinatra", EntrypointKind.HTTP_ROUTE, _sinatra_routes
    )
)
register(
    EntrypointRule("ruby.grape.route", "ruby", "grape", EntrypointKind.HTTP_ROUTE, _grape_routes)
)
register(
    EntrypointRule("ruby.sidekiq.worker", "ruby", "sidekiq", EntrypointKind.TASK, _sidekiq_workers)
)
register(
    EntrypointRule("ruby.rails.routes", "ruby", "rails", EntrypointKind.HTTP_ROUTE, _rails_routes)
)
register(EntrypointRule("ruby.rake.task", "ruby", "rake", EntrypointKind.CLI_COMMAND, _rake_tasks))
register(
    EntrypointRule(
        "ruby.graphql-ruby.field",
        "ruby",
        "graphql-ruby",
        EntrypointKind.GRAPHQL_RESOLVER,
        _graphql_field_hints,
    )
)
register(
    EntrypointRule(
        "ruby.graphql-ruby.resolver",
        "ruby",
        "graphql-ruby",
        EntrypointKind.GRAPHQL_RESOLVER,
        _graphql_resolver_classes,
    )
)
