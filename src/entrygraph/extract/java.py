"""Java extractor: .scm queries harvest nodes, this shaper builds the IR.

Module paths are the fully-qualified class path — a leading ``src/main/java/``
or ``src/test/java/`` root is stripped, ``.java`` dropped, ``/`` -> ``.`` — so
``src/main/java/com/example/UserController.java`` -> ``com.example.UserController``.
Java has no package concept in the IR sense (one type per module path), so
``is_package`` is always False.

Annotations are captured on ``RawSymbol.decorators`` as raw source text
(``@GetMapping("/users")``) and also emitted as ``RawReference(kind="decorator")``
so entrypoint rules and the resolver can match them. Supertypes/interfaces are
emitted as ``RawReference(kind="inherit")``. External calls with unknown
receivers (``Runtime.getRuntime().exec(...)``) resolve to ``java:*.exec``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from entrygraph.extract.base import FileContext, node_text, span_of, truncate
from entrygraph.extract.ir import FileExtraction, RawImport, RawReference, RawSymbol
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.queries import captures, load_query

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Node, Tree

_SCOPE_TYPES = frozenset({"class_declaration", "interface_declaration"})
_ANNOTATION_TYPES = frozenset({"marker_annotation", "annotation"})
_JAVA_ROOTS = ("src/main/java/", "src/test/java/")


class JavaExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("java",)

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        path = repo_relative_path
        for root in _JAVA_ROOTS:
            if path.startswith(root):
                path = path[len(root):]
                break
        path = path.removesuffix(".java")
        return ".".join(p for p in path.split("/") if p) or "_root", False

    def extract(self, tree: "Tree", ctx: FileContext) -> FileExtraction:
        root = tree.root_node
        out = FileExtraction(
            path=ctx.path, language=ctx.language, module_path=ctx.module_path,
            parse_ok=not root.has_error, error_count=1 if root.has_error else 0,
        )
        self._definitions(root, ctx, out)
        self._imports(root, ctx, out)
        self._calls(root, ctx, out)
        return out

    # ---------------- definitions ----------------

    def _definitions(self, root: "Node", ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("java", "definitions"), root)

        for node in caps.get("def.class", []):
            self._add_type(node, ctx, out, SymbolKind.CLASS)
        for node in caps.get("def.interface", []):
            self._add_type(node, ctx, out, SymbolKind.INTERFACE)

        for node in caps.get("def.method", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            qname, parent_q = self._qualify(node, name, ctx)
            modifiers = self._modifiers(node)
            annotations = self._annotations(node)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.METHOD, name=name, qualified_name=qname,
                    span=span_of(node), parent_qualified_name=parent_q,
                    signature=self._signature(node),
                    decorators=annotations,
                    modifiers=modifiers,
                    is_exported="public" in modifiers,
                )
            )
            self._emit_annotation_refs(node, qname, out)

        for node in caps.get("def.field", []):
            scope = self._nearest_scope(node)
            if scope is None:
                continue  # only class/interface members are symbols
            declarator = node.child_by_field_name("declarator")
            if declarator is None:
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            qname, parent_q = self._qualify(node, name, ctx)
            modifiers = self._modifiers(node)
            is_const = "static" in modifiers and "final" in modifiers and name.isupper()
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.CONSTANT if is_const else SymbolKind.FIELD,
                    name=name, qualified_name=qname, span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=truncate(node_text(node)),
                    decorators=self._annotations(node),
                    modifiers=modifiers,
                    is_exported="public" in modifiers,
                )
            )

    def _add_type(self, node: "Node", ctx: FileContext, out: FileExtraction, kind: SymbolKind) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        qname, parent_q = self._qualify(node, name, ctx)
        extends, interfaces = self._supertypes(node)
        modifiers = self._modifiers(node)
        out.symbols.append(
            RawSymbol(
                kind=kind, name=name, qualified_name=qname, span=span_of(node),
                parent_qualified_name=parent_q, signature=self._signature(node),
                decorators=self._annotations(node), bases=[*extends, *interfaces],
                modifiers=modifiers, is_exported="public" in modifiers,
            )
        )
        for base in extends:
            out.references.append(
                RawReference(
                    kind="inherit", callee_text=base, callee_name=base.rsplit(".", 1)[-1],
                    receiver_text=None, span=span_of(node), caller_qualified_name=qname,
                )
            )
        for iface in interfaces:
            out.references.append(
                RawReference(
                    kind="implement", callee_text=iface, callee_name=iface.rsplit(".", 1)[-1],
                    receiver_text=None, span=span_of(node), caller_qualified_name=qname,
                )
            )
        self._emit_annotation_refs(node, qname, out)

    # ---------------- imports ----------------

    def _imports(self, root: "Node", ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("java", "imports"), root)
        for node in caps.get("import", []):
            scoped = next((c for c in node.named_children if c.type == "scoped_identifier"), None)
            if scoped is None:
                continue
            full = node_text(scoped)
            wildcard = any(c.type == "asterisk" for c in node.named_children)
            if wildcard:
                out.imports.append(
                    RawImport(module=full, imported_name="*", alias="*", span=span_of(node))
                )
            else:
                last = full.rsplit(".", 1)[-1]
                out.imports.append(
                    RawImport(module=full, imported_name=last, alias=last, span=span_of(node))
                )
        for imp in out.imports:
            if imp.module:
                out.framework_signals.append(("import", imp.module))

    # ---------------- calls / annotations ----------------

    def _calls(self, root: "Node", ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("java", "calls"), root)

        for node in caps.get("call", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            callee_name = node_text(name_node)
            obj = node.child_by_field_name("object")
            if obj is not None:
                receiver = node_text(obj)
                callee_text = f"{receiver}.{callee_name}"
            else:
                receiver = None
                callee_text = callee_name
            args = node.child_by_field_name("arguments")
            out.references.append(
                RawReference(
                    kind="call", callee_text=callee_text, callee_name=callee_name,
                    receiver_text=receiver, span=span_of(node),
                    caller_qualified_name=self._caller(node, ctx),
                    arg_count=len(args.named_children) if args is not None else 0,
                    arg_preview=truncate(node_text(args)) if args is not None else None,
                )
            )

        for node in caps.get("new", []):
            type_node = node.child_by_field_name("type")
            if type_node is None:
                continue
            type_text = node_text(type_node).split("<", 1)[0].strip()
            callee_name = type_text.rsplit(".", 1)[-1]
            args = node.child_by_field_name("arguments")
            out.references.append(
                RawReference(
                    kind="call", callee_text=type_text, callee_name=callee_name,
                    receiver_text=None, span=span_of(node),
                    caller_qualified_name=self._caller(node, ctx),
                    arg_count=len(args.named_children) if args is not None else 0,
                    arg_preview=truncate(node_text(args)) if args is not None else None,
                )
            )

    # ---------------- helpers ----------------

    def _scope_chain(self, node: "Node", ctx: FileContext) -> list[str]:
        parts, current = [], node.parent
        while current is not None:
            if current.type in _SCOPE_TYPES:
                name = current.child_by_field_name("name")
                if name is not None:
                    parts.append(node_text(name))
            current = current.parent
        parts.reverse()
        # The conventional top-level public type shares the file's stem; the
        # module path already encodes it (com.example.UserController), so drop
        # the duplicate leading segment to avoid ...UserController.UserController.
        top = ctx.module_path.rsplit(".", 1)[-1]
        if parts and parts[0] == top:
            parts = parts[1:]
        return parts

    def _nearest_scope(self, node: "Node") -> str | None:
        current = node.parent
        while current is not None:
            if current.type in _SCOPE_TYPES:
                return current.type
            current = current.parent
        return None

    def _qualify(self, node: "Node", name: str, ctx: FileContext) -> tuple[str, str | None]:
        chain = self._scope_chain(node, ctx)
        top = ctx.module_path.rsplit(".", 1)[-1]
        # The top-level type named after the file collapses onto the module path
        # itself (com.example.UserController), so its members qualify cleanly as
        # com.example.UserController.getUser and parent to that same qname.
        if not chain and name == top:
            # this node *is* the top-level type
            return ctx.module_path, None
        enclosed_in_top = self._enclosed_in_top_type(node, top)
        parent_q = ".".join([ctx.module_path, *chain]) if (chain or enclosed_in_top) else None
        return ".".join([ctx.module_path, *chain, name]), parent_q

    def _enclosed_in_top_type(self, node: "Node", top: str) -> bool:
        current = node.parent
        while current is not None:
            if current.type in _SCOPE_TYPES:
                name = current.child_by_field_name("name")
                return name is not None and node_text(name) == top
            current = current.parent
        return False

    def _caller(self, node: "Node", ctx: FileContext) -> str | None:
        current = node.parent
        while current is not None:
            if current.type == "method_declaration":
                name = current.child_by_field_name("name")
                if name is not None:
                    return self._qualify(current, node_text(name), ctx)[0]
            current = current.parent
        return None

    def _signature(self, node: "Node") -> str:
        return truncate(node_text(node).split("{", 1)[0].strip(), 120)

    def _modifiers_node(self, node: "Node") -> "Node | None":
        return next((c for c in node.named_children if c.type == "modifiers"), None)

    def _modifiers(self, node: "Node") -> list[str]:
        mods = self._modifiers_node(node)
        if mods is None:
            return []
        return [
            node_text(c) for c in mods.children
            if c.type not in _ANNOTATION_TYPES and node_text(c).strip()
        ]

    def _annotations(self, node: "Node") -> list[str]:
        mods = self._modifiers_node(node)
        if mods is None:
            return []
        return [node_text(c) for c in mods.children if c.type in _ANNOTATION_TYPES]

    def _emit_annotation_refs(self, node: "Node", owner_qname: str, out: FileExtraction) -> None:
        mods = self._modifiers_node(node)
        if mods is None:
            return
        for child in mods.children:
            if child.type not in _ANNOTATION_TYPES:
                continue
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            callee_text = node_text(name_node)
            out.references.append(
                RawReference(
                    kind="decorator", callee_text=callee_text,
                    callee_name=callee_text.rsplit(".", 1)[-1],
                    receiver_text=callee_text.rsplit(".", 1)[0] if "." in callee_text else None,
                    span=span_of(child), caller_qualified_name=owner_qname,
                )
            )

    def _supertypes(self, node: "Node") -> tuple[list[str], list[str]]:
        """Return (extends supertypes, implements interfaces)."""
        extends: list[str] = []
        superclass = node.child_by_field_name("superclass")
        if superclass is not None:
            for child in superclass.named_children:
                text = node_text(child).split("<", 1)[0].strip()
                if text:
                    extends.append(text)
        interfaces: list[str] = []
        iface_node = node.child_by_field_name("interfaces")
        if iface_node is not None:
            for type_list in iface_node.named_children:
                for child in type_list.named_children:
                    text = node_text(child).split("<", 1)[0].strip()
                    if text:
                        interfaces.append(text)
        return extends, interfaces
