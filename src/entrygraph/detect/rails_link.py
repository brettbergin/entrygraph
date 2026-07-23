"""Cross-file Rails route -> controller-action binding (graphql_link-style pass).

The routes DSL names its handler indirectly (`resources :posts`,
`to: 'admin/reports#show'`); the routes file itself contains no callable, so
without this pass every Rails route binds to the routes.rb module symbol — an
honest location but a dead end for reachability. This pass runs in the scanner
after all files' symbols are registered and, for each rails hint whose metadata
carries ``controller``/``action``, rebinds ``handler_qualified_name`` to the
matching ``FooController#action`` method found via the symbol table. No or
ambiguous match leaves the hint unbound (module fallback) — never guess wrong.
"""

from __future__ import annotations

from entrygraph.extract.ir import FileExtraction
from entrygraph.kinds import SymbolKind
from entrygraph.resolve.symbol_table import SymbolTable

_CALLABLE_KINDS = (SymbolKind.METHOD, SymbolKind.FUNCTION)


def link_rails(extractions: list[tuple[str, FileExtraction, bool]], table: SymbolTable) -> int:
    """Bind rails route hints to controller actions. Returns the rebind count."""
    rebound = 0
    for _path, x, _pkg in extractions:
        if x.language != "ruby":
            continue
        for hint in x.entrypoint_hints:
            if hint.rule_id != "ruby.rails.routes" or hint.handler_qualified_name:
                continue
            controller = hint.metadata.get("controller")
            action = hint.metadata.get("action")
            if not controller or not action:
                continue
            target = _resolve_action(controller, action, table)
            if target is not None:
                hint.handler_qualified_name = target
                rebound += 1
    return rebound


def _camelize(name: str) -> str:
    return "".join(part.title() for part in name.split("_"))


def _resolve_action(controller: str, action: str, table: SymbolTable) -> str | None:
    """``admin/posts`` + ``show`` -> the qname of ``Admin::PostsController#show``.

    Candidates are callable ruby symbols named ``action`` whose immediate
    container is the controller class — either the bare class name (nested in
    ``module Admin``) or the scope-operator form (``class Admin::PostsController``,
    which the extractor keeps as one symbol name). Multiple matches narrow by the
    namespace segments appearing anywhere in the qname (module or path casing);
    anything still ambiguous stays unbound."""
    *namespace, base = controller.split("/")
    class_names = {_camelize(base) + "Controller"}
    if namespace:
        class_names.add("::".join(_camelize(s) for s in (*namespace, base)) + "Controller")
    candidates = [
        sid
        for sid in table.by_name.get(action, [])
        if table.kinds.get(sid) in _CALLABLE_KINDS and table.lang.get(sid) == "ruby"
    ]
    matches = []
    for sid in candidates:
        parts = table.qname_of[sid].split(".")
        if len(parts) >= 2 and parts[-2] in class_names:
            matches.append(sid)
    if len(matches) > 1 and namespace:
        needles = {s.lower() for s in namespace}
        matches = [
            sid
            for sid in matches
            if needles <= {part.lower() for part in table.qname_of[sid].split(".")}
        ]
    return table.qname_of[matches[0]] if len(matches) == 1 else None
