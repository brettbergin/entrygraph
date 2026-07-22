"""GraphQL SDL entrypoint rule.

Fields on a schema's root operation types (Query/Mutation/Subscription, or the
types bound in a ``schema {}`` block) are the externally callable surface of a
GraphQL API, so each becomes a ``graphql_resolver`` entrypoint bound to its SDL
field symbol. When code-side resolvers are detected too, the cross-file linking
pass rebinds/dedups these against them; until then an SDL-bound entrypoint has
no outgoing call edges, so reachability paths from it terminate at the schema.
"""

from __future__ import annotations

from entrygraph.detect.entrypoints.base import EntrypointRule, register
from entrygraph.extract.ir import EntrypointHint, FileExtraction
from entrygraph.kinds import EntrypointKind, SymbolKind

_OPERATION_PREFIX = "operation:"


def _sdl_fields(x: FileExtraction) -> list[EntrypointHint]:
    hints = []
    for sym in x.symbols:
        if sym.kind is not SymbolKind.FIELD or "resolver_root" not in sym.modifiers:
            continue
        operation = next(
            (m[len(_OPERATION_PREFIX) :] for m in sym.modifiers if m.startswith(_OPERATION_PREFIX)),
            None,
        )
        if operation is None:
            continue
        type_name, field = sym.qualified_name.rsplit(".", 2)[-2:]
        hints.append(
            EntrypointHint(
                rule_id="graphql.sdl.field",
                kind=EntrypointKind.GRAPHQL_RESOLVER,
                handler_qualified_name=sym.qualified_name,
                route=f"{type_name}.{field}",
                name=field,
                span=sym.span,
                framework="graphql-sdl",
                metadata={"operation": operation, "parent_type": type_name, "schema_file": x.path},
            )
        )
    return hints


register(
    EntrypointRule(
        "graphql.sdl.field",
        "graphql",
        None,
        EntrypointKind.GRAPHQL_RESOLVER,
        _sdl_fields,
    )
)
