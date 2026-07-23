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
    route_path_params,
)
from entrygraph.extract.ir import EntrypointHint, FileExtraction, ParameterHint
from entrygraph.kinds import EntrypointKind, SymbolKind

_SINATRA_VERBS = frozenset({"get", "post", "put", "delete", "patch"})

# HTTP methods whose parameters conventionally arrive in the query string;
# anything else defaults to the form/body channel.
_QUERY_METHODS = frozenset({"GET", "DELETE", "HEAD"})


def _usage_location(methods: list[str]) -> str:
    return "query" if all(m in _QUERY_METHODS for m in methods) else "form"


def observed_params(
    x: FileExtraction,
    start_line: int,
    end_line: int,
    methods: list[str],
    exclude: set[str],
) -> list[ParameterHint]:
    """``params[:q]`` reads observed inside a handler span.

    The extractor synthesizes each accessor subscript as a bare ``params``
    reference carrying the key (#87C); any such read between start_line and
    end_line whose key isn't already a declared parameter becomes a
    provenance="usage" hint. Best-effort: required is unknowable, and the
    channel is guessed from the route's methods (query for GET-ish, form
    otherwise)."""
    out: list[ParameterHint] = []
    seen = set(exclude)
    location = _usage_location(methods)
    for ref in x.references:
        if (
            ref.kind != "call"
            or ref.receiver_text is not None
            or ref.callee_name != "params"
            or not ref.arg_preview
            or ref.span.end_line > ref.span.start_line  # a `params do` block, not a read
            or not (start_line <= ref.span.start_line <= end_line)
        ):
            continue
        key = first_string_arg("(" + ref.arg_preview.lstrip("("))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            ParameterHint(
                name=key,
                location=location,
                required=False,
                provenance="usage",
                line=ref.span.start_line,
            )
        )
    return out


def is_rails_routes_file(path: str) -> bool:
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
                methods = [ref.callee_name.upper()]
                params = route_path_params(route, line=ref.span.start_line)
                # the route call's span covers its do...end handler block
                params += observed_params(
                    x,
                    ref.span.start_line,
                    ref.span.end_line,
                    methods,
                    exclude={p.name for p in params},
                )
                hints.append(
                    EntrypointHint(
                        rule_id="ruby.sinatra.route",
                        kind=EntrypointKind.HTTP_ROUTE,
                        handler_qualified_name=ref.caller_qualified_name,
                        route=route,
                        http_methods=methods,
                        framework="sinatra",
                        parameters=params,
                    )
                )
    return hints


# -- rails routes DSL parsing helpers --

_FIRST_SYMBOL = re.compile(r"^\(?\s*:(\w+)")
# `to: 'users#show'` / `to => 'users#show'` and the hashrocket route form
# `get 'profile' => 'users#show'`.
_TO_TARGET = re.compile(r"\bto\s*(?:=>|:)\s*['\"]([\w/]+)#(\w+)['\"]")
_ARROW_TARGET = re.compile(r"['\"]\s*=>\s*['\"]([\w/]+)#(\w+)['\"]")
_ONLY_EXCEPT = re.compile(r"\b(only|except)\s*(?:=>|:)\s*(\[[^\]]*\]|:\w+)")
_VIA_OPT = re.compile(r"\bvia\s*(?:=>|:)\s*(\[[^\]]*\]|:\w+)")
# `module: :projects` (symbol), `module: 'admin/reports'` (string), `module => ...`
_MODULE_OPT = re.compile(r"\bmodule\s*(?:=>|:)\s*(?::|['\"])?([\w/]+)['\"]?")
_PATH_OPT = re.compile(r"\bpath\s*(?:=>|:)\s*['\"]([^'\"]*)['\"]")
_SYMBOLS = re.compile(r":(\w+)")

# Conventional RESTful expansions: (action, methods, path suffix). Rails routes
# update as both PATCH and PUT; a singular `resource` has no index and no :id.
_RESOURCES_ACTIONS = (
    ("index", ("GET",), ""),
    ("create", ("POST",), ""),
    ("new", ("GET",), "/new"),
    ("edit", ("GET",), "/:id/edit"),
    ("show", ("GET",), "/:id"),
    ("update", ("PATCH", "PUT"), "/:id"),
    ("destroy", ("DELETE",), "/:id"),
)
_RESOURCE_ACTIONS = (
    ("show", ("GET",), ""),
    ("create", ("POST",), ""),
    ("new", ("GET",), "/new"),
    ("edit", ("GET",), "/edit"),
    ("update", ("PATCH", "PUT"), ""),
    ("destroy", ("DELETE",), ""),
)


def _camelize_scope(name: str) -> str:
    return "".join(part.title() for part in name.split("_"))


def _singularize(name: str) -> str:
    """Best-effort English singular for nested-resource param names
    (posts -> :post_id). Rails runs a full inflector; the common regular
    plurals below cover real route files, and a miss only mislabels the
    param name, never the route itself."""
    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if any(name.endswith(s) for s in ("ses", "xes", "zes", "ches", "shes")):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _join_path(*segments: str | None) -> str:
    parts = [s.strip("/") for s in segments if s and s.strip("/")]
    return "/" + "/".join(parts)


def first_symbol_or_string(arg_preview: str | None) -> str | None:
    if not arg_preview:
        return None
    m = _FIRST_SYMBOL.match(arg_preview.lstrip("("))
    if m:
        return m.group(1)
    return first_string_arg("(" + arg_preview.lstrip("("))


def _has_block(ref) -> bool:
    # a `do ... end` block makes the call node span multiple lines
    return ref.span.end_line > ref.span.start_line


def _restful_actions(kind: str, arg_preview: str | None):
    actions = _RESOURCES_ACTIONS if kind == "resources" else _RESOURCE_ACTIONS
    m = _ONLY_EXCEPT.search(arg_preview or "")
    if not m:
        return actions
    names = set(_SYMBOLS.findall(m.group(2)))
    if m.group(1) == "only":
        return tuple(a for a in actions if a[0] in names)
    return tuple(a for a in actions if a[0] not in names)


def _route_target(arg_preview: str | None) -> tuple[str, str] | None:
    """The `controller#action` a verb route points at, if stated."""
    m = _TO_TARGET.search(arg_preview or "") or _ARROW_TARGET.search(arg_preview or "")
    return (m.group(1), m.group(2)) if m else None


def _infer_target(route: str) -> tuple[str, str] | None:
    """Rails maps `get 'welcome/index'` to welcome#index when no `to:` is given.
    Only infer for plain multi-segment paths — no params, no globs."""
    segments = [s for s in route.strip("/").split("/") if s]
    if len(segments) < 2 or any(c in route for c in ":*(."):
        return None
    return "/".join(segments[:-1]), segments[-1]


# A scope frame inherited from a parent routes file outlives every line of this
# one, so it must never be popped by the span walk.
_UNBOUNDED = 1 << 30

# (path segment, controller-module segment) contributed by an enclosing block
ScopeFrame = tuple[str | None, str | None]


def _block_frame(ref) -> ScopeFrame | None:
    """The scope frame a `namespace`/`scope`/nested-`resources` block opens, or
    None when the call opens no block scope."""
    if not _has_block(ref):
        return None
    name, preview = ref.callee_name, ref.arg_preview
    if name == "namespace":
        seg = first_symbol_or_string(preview)
        return (seg, seg)
    if name == "scope":
        path_seg = first_string_arg("(" + (preview or "").lstrip("(")) or (
            m.group(1) if (m := _PATH_OPT.search(preview or "")) else None
        )
        module_m = _MODULE_OPT.search(preview or "")
        return (path_seg, module_m.group(1) if module_m else None)
    if name in ("resources", "resource"):
        res = first_symbol_or_string(preview)
        if not res:
            return None
        path_m = _PATH_OPT.search(preview or "")
        res_path = path_m.group(1) if path_m else res
        # nested routes hang off the parent's member id
        return (res_path if name == "resource" else f"{res_path}/:{_singularize(res)}_id", None)
    return None


def walk_routes(x: FileExtraction, seed: list[ScopeFrame] | None = None):
    """Yield `(call, path prefixes, controller-module prefixes)` for every bare
    call in a routes file, span-aware: a call node's span covers its `do ... end`
    block, so `namespace`/`scope`/nested `resources` frames stay active for the
    calls inside them. A call is yielded with the prefixes enclosing it, before
    its own frame (if any) is pushed.

    ``seed`` are frames inherited from a parent routes file that `draw`s this one
    (see detect.rails_draw); they enclose the whole file."""
    calls = sorted(
        (r for r in x.references if r.kind == "call" and r.receiver_text is None),
        key=lambda r: (r.span.start_line, r.span.start_col),
    )
    # active enclosing blocks: (end_line, path segment | None, module segment | None)
    scopes: list[tuple[int, str | None, str | None]] = [(_UNBOUNDED, p, m) for p, m in (seed or ())]
    for ref in calls:
        while scopes and ref.span.start_line > scopes[-1][0]:
            scopes.pop()
        yield (
            ref,
            [p for _e, p, _m in scopes if p],
            [m for _e, _p, m in scopes if m],
        )
        frame = _block_frame(ref)
        if frame is not None:
            scopes.append((ref.span.end_line, *frame))


def _rails_routes(x: FileExtraction) -> list[EntrypointHint]:
    """Span-aware walk of the routes DSL: `namespace`/`scope`/nested `resources`
    blocks stack path and controller-module prefixes, `resources`/`resource`
    expand to their conventional RESTful set, and verb routes pick up
    `to:`/hashrocket/inferred targets. The controller#action lands in metadata;
    the cross-file link pass binds it to the controller symbol after all files'
    symbols are registered."""
    if not is_rails_routes_file(x.path):
        return []
    hints: list[EntrypointHint] = []

    def _emit(route, methods, controller, action, ref, module_segs):
        metadata = {"registration": ref.arg_preview or ref.callee_name}
        if controller and action:
            metadata["controller"] = "/".join([*module_segs, controller])
            metadata["action"] = action
        hints.append(
            EntrypointHint(
                rule_id="ruby.rails.routes",
                kind=EntrypointKind.HTTP_ROUTE,
                handler_qualified_name=None,  # bound by the rails link pass
                route=route,
                http_methods=list(methods),
                framework="rails",
                span=ref.span,
                metadata=metadata,
                parameters=route_path_params(route, line=ref.span.start_line),
            )
        )

    for ref, path_segs, module_segs in walk_routes(x, x.rails_draw_scopes):
        name = ref.callee_name
        preview = ref.arg_preview
        if name in ("resources", "resource"):
            res = first_symbol_or_string(preview)
            if not res:
                continue
            path_m = _PATH_OPT.search(preview or "")
            res_path = path_m.group(1) if path_m else res
            for action, methods, suffix in _restful_actions(name, preview):
                _emit(
                    _join_path(*path_segs, res_path + suffix),
                    methods,
                    res,
                    action,
                    ref,
                    module_segs,
                )
        elif name == "root":
            target = _route_target(preview)
            if target is None and preview:
                arg = first_string_arg("(" + preview.lstrip("("))
                if arg and "#" in arg:
                    ctrl, _sep, act = arg.partition("#")
                    target = (ctrl, act)
            controller, action = target if target else (None, None)
            _emit(_join_path(*path_segs), ["GET"], controller, action, ref, module_segs)
        elif name in ("get", "post", "put", "patch", "delete", "match"):
            route = first_string_arg("(" + (preview or "").lstrip("(")) if preview else None
            if route is None:
                continue
            if name == "match":
                via = _VIA_OPT.search(preview or "")
                verbs = [v.upper() for v in _SYMBOLS.findall(via.group(1))] if via else []
                methods = [v for v in verbs if v != "ALL"] or ["*"]
            else:
                methods = [name.upper()]
            target = _route_target(preview) or _infer_target(route)
            controller, action = target if target else (None, None)
            _emit(_join_path(*path_segs, route), methods, controller, action, ref, module_segs)
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


_GRAPE_PARAM_DECLS = frozenset({"requires", "optional"})
_TYPE_OPT = re.compile(r"\btype\s*(?:=>|:)\s*([A-Z]\w*)")

# A `params do ... end` block annotates the route declared right below it;
# allow one blank/comment line between the block's `end` and the verb.
_PARAMS_BLOCK_GAP = 2


def _grape_param_blocks(x: FileExtraction) -> list[tuple[int, int, list[ParameterHint]]]:
    """`params do requires :name, type: String ... end` blocks, as
    (start_line, end_line, declared params). Location is left empty here and
    filled in when a block attaches to a route (it depends on the verb)."""
    blocks = [
        (ref.span.start_line, ref.span.end_line)
        for ref in x.references
        if ref.kind == "call"
        and ref.receiver_text is None
        and ref.callee_name == "params"
        and ref.span.end_line > ref.span.start_line
    ]
    declared: dict[tuple[int, int], list[ParameterHint]] = {b: [] for b in blocks}
    for ref in x.references:
        if (
            ref.kind != "call"
            or ref.receiver_text is not None
            or ref.callee_name not in _GRAPE_PARAM_DECLS
            or not ref.arg_preview
        ):
            continue
        block = next(
            (b for b in blocks if b[0] <= ref.span.start_line <= b[1]),
            None,
        )
        if block is None:
            continue
        name_m = _FIRST_SYMBOL.match(ref.arg_preview.lstrip("("))
        if not name_m:
            continue
        type_m = _TYPE_OPT.search(ref.arg_preview)
        declared[block].append(
            ParameterHint(
                name=name_m.group(1),
                location="",  # filled at attach time
                required=ref.callee_name == "requires",
                type_ref=type_m.group(1) if type_m else None,
                provenance="dsl",
                line=ref.span.start_line,
            )
        )
    return sorted((s, e, ps) for (s, e), ps in declared.items())


def _grape_routes(x: FileExtraction) -> list[EntrypointHint]:
    """Grape API classes: class-body `get '/x'` / `post '/y'` declarations, with
    the preceding `params do ... end` block's requires/optional declarations."""
    blocks = _grape_param_blocks(x)
    hints = []
    for ref in x.references:
        if (
            ref.kind == "call"
            and ref.receiver_text is None
            and ref.callee_name in _SINATRA_VERBS
            and ref.arg_preview
        ):
            route = first_string_arg("(" + ref.arg_preview.lstrip("("))
            if route is None:
                continue
            methods = [ref.callee_name.upper()]
            params = route_path_params(route, line=ref.span.start_line)
            names = {p.name for p in params}
            block = next(
                (
                    b
                    for b in reversed(blocks)
                    if b[1] < ref.span.start_line <= b[1] + _PARAMS_BLOCK_GAP
                ),
                None,
            )
            if block is not None:
                for p in block[2]:
                    if p.name in names:
                        continue
                    names.add(p.name)
                    params.append(
                        ParameterHint(
                            name=p.name,
                            location="body" if methods[0] in ("POST", "PUT", "PATCH") else "query",
                            required=p.required,
                            type_ref=p.type_ref,
                            provenance="dsl",
                            line=p.line,
                        )
                    )
            params += observed_params(
                x, ref.span.start_line, ref.span.end_line, methods, exclude=names
            )
            hints.append(
                EntrypointHint(
                    rule_id="ruby.grape.route",
                    kind=EntrypointKind.HTTP_ROUTE,
                    handler_qualified_name=ref.caller_qualified_name,
                    route=route,
                    http_methods=methods,
                    framework="grape",
                    parameters=params,
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
