"""Cross-file GraphQL SDL -> code-resolver linking (grpc_expand-style pass).

The SDL rule binds each root field to its schema-file symbol, which has no
outgoing call edges — an honest location, but a dead end for reachability. This
pass runs in the scanner after symbols are registered (hints are consumed
later), and for each SDL field hint:

1. drops it when a code-side ``graphql_resolver`` hint already covers the same
   ``Type.field`` route (per-file dedup can't see this collision), else
2. rebinds ``handler_qualified_name`` to a code resolver found via the symbol
   table — suffix match on ``.{Type}.{field}`` (Apollo maps), the graphql-ruby
   ``{Type}Type`` / gqlgen ``{type}Resolver`` container shapes, then a
   unique-name match — so ``paths`` can traverse into the resolver body even
   when no code-side rule fired (e.g. the framework went undetected).

``metadata["schema_file"]`` keeps the SDL provenance either way.
"""

from __future__ import annotations

from entrygraph.extract.ir import FileExtraction
from entrygraph.kinds import EntrypointKind, SymbolKind
from entrygraph.resolve.symbol_table import SymbolTable

_CALLABLE_KINDS = (SymbolKind.FUNCTION, SymbolKind.METHOD)


def link_graphql(extractions: list[tuple[str, FileExtraction, bool]], table: SymbolTable) -> int:
    """Dedup/rebind SDL field hints against code resolvers. Returns rebind count."""
    code_routes = {
        hint.route
        for _path, x, _pkg in extractions
        if x.language != "graphql"
        for hint in x.entrypoint_hints
        if hint.kind is EntrypointKind.GRAPHQL_RESOLVER and hint.route
    }
    rebound = 0
    for _path, x, _pkg in extractions:
        if x.language != "graphql":
            continue
        kept = []
        for hint in x.entrypoint_hints:
            if hint.rule_id != "graphql.sdl.field" or not hint.route:
                kept.append(hint)
                continue
            if hint.route in code_routes:
                continue  # the code-side hint is the better row for this field
            target = _resolve_code_handler(hint.route, table)
            if target is not None:
                hint.handler_qualified_name = target
                rebound += 1
            kept.append(hint)
        x.entrypoint_hints = kept
    return rebound


def _resolve_code_handler(route: str, table: SymbolTable) -> str | None:
    type_name, field = route.rsplit(".", 1)
    capitalized = field[:1].upper() + field[1:]  # gqlgen exports fields as Go methods
    candidates: list[int] = []
    for name in dict.fromkeys((field, capitalized)):
        candidates += [
            sid
            for sid in table.by_name.get(name, [])
            if table.kinds.get(sid) in _CALLABLE_KINDS and table.lang.get(sid) != "graphql"
        ]
    if not candidates:
        return None
    # resolver-container shapes, most specific first
    lowered = type_name[:1].lower() + type_name[1:]
    for container in (type_name, f"{type_name}Type", f"{lowered}Resolver", f"{type_name}Resolver"):
        matches = [
            sid for sid in candidates if table.qname_of[sid].rsplit(".", 2)[-2:-1] == [container]
        ]
        if len(matches) == 1:
            return table.qname_of[matches[0]]
    return table.qname_of[candidates[0]] if len(candidates) == 1 else None
