"""GraphQL SDL extractor: schema files become symbols so fields can be entrypoints.

Type definitions map to CLASS symbols and their fields to FIELD symbols — these
are declarations, not code, but giving them symbols lets schema-first repos
surface field-level entrypoints (and later rebind them to code resolvers).

Root-type detection is per-file: names bound in a ``schema { query: ... }``
block, defaulting to Query/Mutation/Subscription when no schema block exists.
A ``schema {}`` living in a different file than its root type definitions is a
known limitation — the default names cover the dominant convention. Fields on a
root type carry ``resolver_root`` and ``operation:<op>`` modifiers so the SDL
entrypoint rule stays IR-only.

The module path keeps the extension as a name segment (``schema/user.graphql``
-> ``schema.user_graphql``): JS strips extensions, so a sibling ``user.ts``
would otherwise collide in the global qname map and misbind handlers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from entrygraph.extract.base import FileContext, node_text, span_of, truncate
from entrygraph.extract.ir import FileExtraction, RawSymbol
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.queries import captures, load_query

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Node, Tree

_DEFAULT_ROOTS = {"Query": "query", "Mutation": "mutation", "Subscription": "subscription"}

# capture name -> (emit a CLASS symbol, emit FIELD symbols). Extensions add
# fields to an already-defined type, so they contribute fields only — a second
# CLASS symbol would duplicate the qname.
_TYPE_CAPTURES = {
    "def.object_type": (True, True),
    "def.object_type_ext": (False, True),
    "def.interface": (True, True),
    "def.input": (True, False),
    "def.enum": (True, False),
    "def.union": (True, False),
}


class GraphQLExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("graphql",)

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        parts = repo_relative_path.split("/")
        parts[-1] = parts[-1].replace(".", "_")
        return ".".join(p for p in parts if p) or "_root", False

    def extract(self, tree: Tree, ctx: FileContext) -> FileExtraction:
        root = tree.root_node
        out = FileExtraction(
            path=ctx.path,
            language=ctx.language,
            module_path=ctx.module_path,
            parse_ok=not root.has_error,
            error_count=1 if root.has_error else 0,
        )
        caps = captures(load_query("graphql", "definitions"), root)
        roots = self._root_types(caps.get("def.root_op", []))
        # extensions of a root type (`extend type Query`) contribute root fields too
        for capture, (with_class, with_fields) in _TYPE_CAPTURES.items():
            for node in caps.get(capture, []):
                self._emit_type(node, ctx, out, roots, with_class, with_fields)
        return out

    def _root_types(self, root_ops: list[Node]) -> dict[str, str]:
        """Type name -> operation, from `schema { query: RootQuery ... }` blocks."""
        roots = dict(_DEFAULT_ROOTS)
        for node in root_ops:
            op = next(
                (node_text(c) for c in node.named_children if c.type == "operation_type"), None
            )
            named = next((c for c in node.named_children if c.type == "named_type"), None)
            if op and named is not None:
                roots[node_text(named)] = op
        return roots

    def _emit_type(
        self,
        node: Node,
        ctx: FileContext,
        out: FileExtraction,
        roots: dict[str, str],
        with_class: bool,
        with_fields: bool,
    ) -> None:
        name_node = next((c for c in node.named_children if c.type == "name"), None)
        if name_node is None:
            return
        type_name = node_text(name_node)
        qname = f"{ctx.module_path}.{type_name}"
        if with_class:
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.CLASS,
                    name=type_name,
                    qualified_name=qname,
                    span=span_of(node),
                    signature=self._signature(node),
                    bases=self._interfaces(node),
                    docstring=self._description(node),
                )
            )
        if not with_fields:
            return
        operation = roots.get(type_name)
        modifiers = ["resolver_root", f"operation:{operation}"] if operation else []
        fields = next((c for c in node.named_children if c.type == "fields_definition"), None)
        for field in fields.named_children if fields is not None else []:
            if field.type != "field_definition":
                continue
            field_name_node = next((c for c in field.named_children if c.type == "name"), None)
            if field_name_node is None:
                continue
            field_name = node_text(field_name_node)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.FIELD,
                    name=field_name,
                    qualified_name=f"{qname}.{field_name}",
                    span=span_of(field),
                    parent_qualified_name=qname,
                    signature=self._signature(field),
                    modifiers=list(modifiers),
                    docstring=self._description(field),
                )
            )

    def _signature(self, node: Node) -> str:
        """First line of the definition, skipping any leading description string."""
        text = node_text(node)
        desc = next((c for c in node.named_children if c.type == "description"), None)
        if desc is not None:
            text = text[desc.end_byte - node.start_byte :].lstrip()
        return truncate(text.split("\n", 1)[0].rstrip())

    def _description(self, node: Node) -> str | None:
        desc = next((c for c in node.named_children if c.type == "description"), None)
        if desc is None:
            return None
        text = node_text(desc).strip()
        for quote in ('"""', '"'):
            if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
                text = text[len(quote) : -len(quote)]
                break
        return truncate(text.strip(), 200) or None

    def _interfaces(self, node: Node) -> list[str]:
        """`type User implements Node & Other` -> ["Node", "Other"] (nested grammar)."""
        names: list[str] = []
        stack = [c for c in node.named_children if c.type == "implements_interfaces"]
        while stack:
            current = stack.pop()
            for child in current.named_children:
                if child.type == "implements_interfaces":
                    stack.append(child)
                elif child.type == "named_type":
                    names.append(node_text(child))
        names.reverse()
        return names
