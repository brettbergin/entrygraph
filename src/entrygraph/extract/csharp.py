"""C# extractor: .scm queries harvest nodes, this shaper builds the IR.

Qualified names come from the enclosing **namespace**, not the file path — C#
decouples the two. Both classic block namespaces (``namespace Foo { ... }``)
and C# 10 file-scoped namespaces (``namespace Foo;``) are honoured by walking
ancestors for ``namespace_declaration`` / ``file_scoped_namespace_declaration``.
So ``MyApp.Controllers.UsersController.Get`` regardless of where the file lives.
Files with no namespace (top-level statements) fall back to a directory-derived
module path.

``module_path_for`` returns ``(dotted_dir_module, False)`` — it cannot see the
namespace (no parse), so it is only a fallback; ``extract`` overrides the
per-symbol qname prefix with the real namespace when one is present.

Attributes (``[HttpGet("/x")]``) are captured on ``RawSymbol.decorators`` as raw
source text and also emitted as ``RawReference(kind="decorator")`` (mirrors the
Java annotation handling). Base types / interfaces become
``RawReference(kind="inherit")`` — C# does not syntactically distinguish a base
class from implemented interfaces, so every entry in the ``base_list`` is emitted
as an ``inherit`` ref and stored on ``bases``. ``is_exported`` is ``"public" in
modifiers``.

Partial classes (``partial class Foo`` split across files/blocks) produce the
same qualified name more than once; the DB is last-writer-wins, which is
acceptable — members still qualify under the shared type qname.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from entrygraph.extract.base import FileContext, node_text, span_of, truncate
from entrygraph.extract.ir import FileExtraction, RawImport, RawReference, RawSymbol
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.queries import captures, load_query

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Node, Tree

_NAMESPACE_TYPES = frozenset(
    {"namespace_declaration", "file_scoped_namespace_declaration"}
)
_TYPE_SCOPES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "record_declaration",
    }
)
_CALLABLE_SCOPES = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "property_declaration",
    }
)


def _looks_like_type_ref(receiver: str) -> bool:
    """Heuristic: is a member-access receiver a *type* (static call) rather than
    an instance/variable? A type reference is a dotted chain of identifiers whose
    every segment starts with an uppercase letter (``Process``,
    ``System.Diagnostics.Process``). Instance receivers are conventionally
    lowercase (``service``, ``cmd``, ``_reports``) or use ``this``/``base``.
    Anything with call/index syntax is not a plain type reference.
    """
    if not receiver or any(c in receiver for c in "()[]?"):
        return False
    segments = receiver.split(".")
    return all(seg[:1].isupper() and seg[:1].isalpha() for seg in segments)


class CSharpExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("csharp",)

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        # Fallback only: the real qname prefix is the namespace, resolved per
        # symbol in extract(). Directory-derived dotted path, ".cs" dropped.
        path = repo_relative_path.removesuffix(".cs")
        dotted = ".".join(p for p in path.split("/") if p)
        return dotted or "_root", False

    def extract(self, tree: "Tree", ctx: FileContext) -> FileExtraction:
        root = tree.root_node
        out = FileExtraction(
            path=ctx.path,
            language=ctx.language,
            module_path=ctx.module_path,
            parse_ok=not root.has_error,
            error_count=1 if root.has_error else 0,
        )
        self._definitions(root, ctx, out)
        self._imports(root, ctx, out)
        self._calls(root, ctx, out)
        return out

    # ---------------- definitions ----------------

    def _definitions(self, root: "Node", ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("csharp", "definitions"), root)

        for node in caps.get("def.class", []):
            self._add_type(node, ctx, out, SymbolKind.CLASS)
        for node in caps.get("def.interface", []):
            self._add_type(node, ctx, out, SymbolKind.INTERFACE)
        for node in caps.get("def.struct", []):
            self._add_type(node, ctx, out, SymbolKind.STRUCT)
        for node in caps.get("def.record", []):
            # C# has no dedicated record kind in the IR; treat as a class.
            self._add_type(node, ctx, out, SymbolKind.CLASS)

        for kind_key, sym_kind in (
            ("def.method", SymbolKind.METHOD),
            ("def.constructor", SymbolKind.METHOD),
            ("def.local_function", SymbolKind.FUNCTION),
        ):
            for node in caps.get(kind_key, []):
                self._add_callable(node, ctx, out, sym_kind)

        for node in caps.get("def.property", []):
            self._add_member(node, ctx, out, SymbolKind.PROPERTY)

        for node in caps.get("def.field", []):
            self._add_field(node, ctx, out)

    def _add_type(
        self, node: "Node", ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        qname, parent_q = self._qualify(node, name, ctx)
        bases = self._base_types(node)
        modifiers = self._modifiers(node)
        out.symbols.append(
            RawSymbol(
                kind=kind,
                name=name,
                qualified_name=qname,
                span=span_of(node),
                parent_qualified_name=parent_q,
                signature=self._signature(node),
                decorators=self._attributes(node),
                bases=bases,
                modifiers=modifiers,
                is_exported="public" in modifiers,
            )
        )
        for base in bases:
            out.references.append(
                RawReference(
                    kind="inherit",
                    callee_text=base,
                    callee_name=base.rsplit(".", 1)[-1],
                    receiver_text=None,
                    span=span_of(node),
                    caller_qualified_name=qname,
                )
            )
        self._emit_attribute_refs(node, qname, out)

    def _add_callable(
        self, node: "Node", ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        qname, parent_q = self._qualify(node, name, ctx)
        modifiers = self._modifiers(node)
        out.symbols.append(
            RawSymbol(
                kind=kind,
                name=name,
                qualified_name=qname,
                span=span_of(node),
                parent_qualified_name=parent_q,
                signature=self._signature(node),
                decorators=self._attributes(node),
                modifiers=modifiers,
                is_exported="public" in modifiers,
            )
        )
        self._emit_attribute_refs(node, qname, out)

    def _add_member(
        self, node: "Node", ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        if self._nearest_type_scope(node) is None:
            return  # only type members are symbols
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        qname, parent_q = self._qualify(node, name, ctx)
        modifiers = self._modifiers(node)
        out.symbols.append(
            RawSymbol(
                kind=kind,
                name=name,
                qualified_name=qname,
                span=span_of(node),
                parent_qualified_name=parent_q,
                signature=truncate(node_text(node), 120),
                decorators=self._attributes(node),
                modifiers=modifiers,
                is_exported="public" in modifiers,
            )
        )
        self._emit_attribute_refs(node, qname, out)

    def _add_field(self, node: "Node", ctx: FileContext, out: FileExtraction) -> None:
        if self._nearest_type_scope(node) is None:
            return
        declaration = next(
            (c for c in node.named_children if c.type == "variable_declaration"), None
        )
        if declaration is None:
            return
        declarator = next(
            (c for c in declaration.named_children if c.type == "variable_declarator"),
            None,
        )
        if declarator is None:
            return
        name_node = declarator.child_by_field_name("name") or next(
            (c for c in declarator.named_children if c.type == "identifier"), None
        )
        if name_node is None:
            return
        name = node_text(name_node)
        qname, parent_q = self._qualify(node, name, ctx)
        modifiers = self._modifiers(node)
        is_const = "const" in modifiers or (
            "static" in modifiers and "readonly" in modifiers and name.isupper()
        )
        out.symbols.append(
            RawSymbol(
                kind=SymbolKind.CONSTANT if is_const else SymbolKind.FIELD,
                name=name,
                qualified_name=qname,
                span=span_of(node),
                parent_qualified_name=parent_q,
                signature=truncate(node_text(node), 120),
                decorators=self._attributes(node),
                modifiers=modifiers,
                is_exported="public" in modifiers,
            )
        )

    # ---------------- imports ----------------

    def _imports(self, root: "Node", ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("csharp", "imports"), root)
        for node in caps.get("import", []):
            self._add_using(node, out)
        for imp in out.imports:
            if imp.module:
                out.framework_signals.append(("import", imp.module))

    def _add_using(self, node: "Node", out: FileExtraction) -> None:
        # Named children in order; an `=` child marks an alias directive, a
        # `static` keyword marks `using static`.
        children = node.children
        has_alias = any(c.type == "=" for c in children)
        # The dotted namespace / type is the qualified_name or identifier child.
        name_nodes = [
            c
            for c in node.named_children
            if c.type in ("qualified_name", "identifier")
        ]
        if not name_nodes:
            return
        if has_alias and len(name_nodes) >= 2:
            alias = node_text(name_nodes[0])
            module = node_text(name_nodes[1])
            imported = module.rsplit(".", 1)[-1]
            out.imports.append(
                RawImport(
                    module=module,
                    imported_name=imported,
                    alias=alias,
                    span=span_of(node),
                )
            )
            return
        module = node_text(name_nodes[-1])
        # Plain / static using: a namespace wildcard import (any type in it is
        # brought into scope), mirror Java's star import.
        out.imports.append(
            RawImport(
                module=module,
                imported_name="*",
                alias="*",
                span=span_of(node),
            )
        )

    # ---------------- calls ----------------

    def _calls(self, root: "Node", ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("csharp", "calls"), root)

        for node in caps.get("call", []):
            self._add_invocation(node, ctx, out)
        for node in caps.get("new", []):
            self._add_object_creation(node, ctx, out)

    def _add_invocation(self, node: "Node", ctx: FileContext, out: FileExtraction) -> None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        if fn.type == "member_access_expression":
            name_node = fn.child_by_field_name("name")
            obj = fn.child_by_field_name("expression")
            if name_node is None:
                return
            callee_name = node_text(name_node)
            receiver = node_text(obj) if obj is not None else None
            callee_text = node_text(fn)
            # Static-type calls (`Process.Start`, `System.Diagnostics.Process.Start`)
            # cannot be told from instance calls by the grammar, but the receiver
            # of a static call is a *type* — a dotted chain of PascalCase
            # identifiers. Treat those as bare calls so the resolver keeps the
            # full dotted callee (`cs:Process.Start`) instead of collapsing to
            # `cs:*.Start`; instance receivers (`cmd`, `service`) stay set so
            # `cs:*.Method` sink matching still fires.
            if receiver is not None and _looks_like_type_ref(receiver):
                receiver = None
        else:
            callee_name = node_text(fn).rsplit(".", 1)[-1]
            receiver = None
            callee_text = node_text(fn)
        args = node.child_by_field_name("arguments")
        out.references.append(
            RawReference(
                kind="call",
                callee_text=callee_text,
                callee_name=callee_name,
                receiver_text=receiver,
                span=span_of(node),
                caller_qualified_name=self._caller(node, ctx),
                arg_count=len(args.named_children) if args is not None else 0,
                arg_preview=truncate(node_text(args)) if args is not None else None,
            )
        )

    def _add_object_creation(
        self, node: "Node", ctx: FileContext, out: FileExtraction
    ) -> None:
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return
        type_text = node_text(type_node).split("<", 1)[0].strip()
        callee_name = type_text.rsplit(".", 1)[-1]
        args = node.child_by_field_name("arguments")
        out.references.append(
            RawReference(
                kind="call",
                callee_text=type_text,
                callee_name=callee_name,
                receiver_text=None,
                span=span_of(node),
                caller_qualified_name=self._caller(node, ctx),
                arg_count=len(args.named_children) if args is not None else 0,
                arg_preview=truncate(node_text(args)) if args is not None else None,
            )
        )

    # ---------------- helpers ----------------

    def _namespace(self, node: "Node") -> str | None:
        # Block namespaces enclose their members; walk ancestors.
        current = node.parent
        while current is not None:
            if current.type == "namespace_declaration":
                name = current.child_by_field_name("name")
                if name is not None:
                    return node_text(name)
            current = current.parent
        # File-scoped namespaces (C# 10, `namespace Foo;`) do NOT nest their
        # members: the declaration is a sibling at compilation_unit level and
        # applies to everything after it. Find it at the root.
        root = node
        while root.parent is not None:
            root = root.parent
        for child in root.children:
            if child.type == "file_scoped_namespace_declaration":
                name = child.child_by_field_name("name")
                if name is not None:
                    return node_text(name)
        return None

    def _type_chain(self, node: "Node") -> list[str]:
        parts, current = [], node.parent
        while current is not None:
            if current.type in _TYPE_SCOPES:
                name = current.child_by_field_name("name")
                if name is not None:
                    parts.append(node_text(name))
            current = current.parent
        parts.reverse()
        return parts

    def _base_prefix(self, node: "Node", ctx: FileContext) -> str:
        ns = self._namespace(node)
        return ns if ns is not None else ctx.module_path

    def _qualify(
        self, node: "Node", name: str, ctx: FileContext
    ) -> tuple[str, str | None]:
        prefix = self._base_prefix(node, ctx)
        chain = self._type_chain(node)
        parent_parts = [prefix, *chain]
        parent_q = ".".join(p for p in parent_parts if p) or None
        qname = ".".join(p for p in [*parent_parts, name] if p)
        return qname, parent_q

    def _nearest_type_scope(self, node: "Node") -> "Node | None":
        current = node.parent
        while current is not None:
            if current.type in _TYPE_SCOPES:
                return current
            current = current.parent
        return None

    def _caller(self, node: "Node", ctx: FileContext) -> str | None:
        current = node.parent
        while current is not None:
            if current.type in _CALLABLE_SCOPES:
                name = current.child_by_field_name("name")
                if name is not None:
                    return self._qualify(current, node_text(name), ctx)[0]
            current = current.parent
        return None

    def _signature(self, node: "Node") -> str:
        text = node_text(node)
        for stop in ("{", "=>", ";"):
            text = text.split(stop, 1)[0]
        return truncate(text.strip(), 120)

    def _modifiers(self, node: "Node") -> list[str]:
        return [node_text(c) for c in node.children if c.type == "modifier"]

    def _attribute_lists(self, node: "Node") -> list["Node"]:
        return [c for c in node.children if c.type == "attribute_list"]

    def _attributes(self, node: "Node") -> list[str]:
        return [
            "[" + node_text(attr) + "]"
            for lst in self._attribute_lists(node)
            for attr in lst.named_children
            if attr.type == "attribute"
        ]

    def _emit_attribute_refs(
        self, node: "Node", owner_qname: str, out: FileExtraction
    ) -> None:
        for lst in self._attribute_lists(node):
            for attr in lst.named_children:
                if attr.type != "attribute":
                    continue
                name_node = attr.child_by_field_name("name") or next(
                    (
                        c
                        for c in attr.named_children
                        if c.type in ("identifier", "qualified_name")
                    ),
                    None,
                )
                if name_node is None:
                    continue
                callee_text = node_text(name_node)
                out.references.append(
                    RawReference(
                        kind="decorator",
                        callee_text=callee_text,
                        callee_name=callee_text.rsplit(".", 1)[-1],
                        receiver_text=callee_text.rsplit(".", 1)[0]
                        if "." in callee_text
                        else None,
                        span=span_of(attr),
                        caller_qualified_name=owner_qname,
                    )
                )

    def _base_types(self, node: "Node") -> list[str]:
        base_list = next(
            (c for c in node.named_children if c.type == "base_list"), None
        )
        if base_list is None:
            return []
        bases: list[str] = []
        for child in base_list.named_children:
            text = node_text(child).split("<", 1)[0].strip()
            if text:
                bases.append(text)
        return bases
