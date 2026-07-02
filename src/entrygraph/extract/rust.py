"""Rust extractor: .scm queries harvest nodes, this shaper builds the IR.

Rust qualified names are module-path based. The module path is derived from the
file's location under ``src/``: ``src/main.rs``/``src/lib.rs`` collapse to the
crate root (``_root``); ``src/foo.rs`` and ``src/foo/mod.rs`` both map to module
``foo`` (``mod.rs`` behaves like Python's ``__init__.py`` -> ``is_package=True``).

``::`` path separators are normalized to ``.`` throughout qnames so externals
look like ``rs:std.process.Command.new`` and dot-based sink globs match.

``impl Foo { fn bar }`` yields a method ``<mod>.Foo.bar`` parented to
``<mod>.Foo``; ``impl Trait for Foo`` attaches the methods to ``Foo`` and emits
an ``inherit`` reference to ``Trait``. Free functions live at module scope.
Attributes preceding an item (``#[get("/x")]``, ``#[tokio::main]``,
``#[derive(Parser)]``) are captured as ``RawSymbol.decorators`` (raw text) and
also emitted as ``RawReference(kind="decorator")`` (the Java pattern).

``use`` declarations bind aliases into local scope and each import emits a
crate-root framework signal (``axum``, ``tokio``, ...) so detection fires. A
``macro_invocation`` (``sqlx::query!``) is emitted as a ``call`` reference with
the trailing ``!`` stripped so sink patterns can match the generated call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from entrygraph.extract.base import FileContext, node_text, span_of, truncate
from entrygraph.extract.ir import FileExtraction, RawImport, RawReference, RawSymbol
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.queries import captures, load_query

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Node, Tree

_SRC_ROOTS = ("src",)
_CRATE_ROOT = "_root"
# Attribute nodes precede an item as leading siblings.
_ATTRIBUTE = "attribute_item"


def _norm(text: str) -> str:
    """Normalize Rust path separators to dot-qname form."""
    return text.replace("::", ".")


class RustExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("rust",)

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        parts = repo_relative_path.split("/")
        if parts and parts[0] in _SRC_ROOTS and len(parts) > 1:
            parts = parts[1:]
        # crate roots collapse to _root
        if parts and parts[-1] in ("main.rs", "lib.rs") and len(parts) == 1:
            return _CRATE_ROOT, False
        is_package = parts[-1] == "mod.rs"
        if is_package:
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1].removesuffix(".rs")
        module = ".".join(p for p in parts if p)
        return module or _CRATE_ROOT, is_package

    def extract(self, tree: Tree, ctx: FileContext) -> FileExtraction:
        root = tree.root_node
        out = FileExtraction(
            path=ctx.path,
            language=ctx.language,
            module_path=ctx.module_path,
            parse_ok=not root.has_error,
            error_count=1 if root.has_error else 0,
        )
        self._extract_definitions(root, ctx, out)
        self._extract_imports(root, ctx, out)
        self._extract_calls(root, ctx, out)
        return out

    # ---------------- definitions ----------------

    def _extract_definitions(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("rust", "definitions"), root)

        for node in caps.get("def.impl", []):
            self._add_impl(node, ctx, out)

        for node in caps.get("def.function", []):
            if self._in_impl(node):
                continue  # impl methods are handled by _add_impl
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            decorators = self._decorators(node)
            qname = f"{ctx.module_path}.{name}"
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.FUNCTION,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=None,
                    signature=self._signature(node),
                    decorators=decorators,
                    is_exported=self._is_pub(node),
                )
            )
            self._emit_decorator_refs(node, qname, out)

        for node in caps.get("def.struct", []):
            self._add_type(node, ctx, out, SymbolKind.STRUCT)
        for node in caps.get("def.enum", []):
            self._add_type(node, ctx, out, SymbolKind.STRUCT)
        for node in caps.get("def.trait", []):
            self._add_type(node, ctx, out, SymbolKind.INTERFACE)

        for node in caps.get("def.const", []):
            self._add_value(node, ctx, out, SymbolKind.CONSTANT)
        for node in caps.get("def.static", []):
            self._add_value(node, ctx, out, SymbolKind.CONSTANT)
        for node in caps.get("def.mod", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.MODULE,
                    name=name,
                    qualified_name=f"{ctx.module_path}.{name}",
                    span=span_of(node),
                    parent_qualified_name=None,
                    signature=self._signature(node),
                    is_exported=self._is_pub(node),
                )
            )

    def _add_type(
        self, node: Node, ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        qname = f"{ctx.module_path}.{name}"
        out.symbols.append(
            RawSymbol(
                kind=kind,
                name=name,
                qualified_name=qname,
                span=span_of(node),
                parent_qualified_name=None,
                signature=self._signature(node),
                decorators=self._decorators(node),
                is_exported=self._is_pub(node),
            )
        )
        self._emit_decorator_refs(node, qname, out)

    def _add_value(
        self, node: Node, ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        out.symbols.append(
            RawSymbol(
                kind=kind,
                name=name,
                qualified_name=f"{ctx.module_path}.{name}",
                span=span_of(node),
                parent_qualified_name=None,
                signature=truncate(node_text(node)),
                is_exported=self._is_pub(node),
            )
        )

    def _add_impl(self, node: Node, ctx: FileContext, out: FileExtraction) -> None:
        """`impl Foo { .. }` or `impl Trait for Foo { .. }` -> methods on Foo."""
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return
        type_name = self._type_name(type_node)
        if type_name is None:
            return
        parent_q = f"{ctx.module_path}.{type_name}"

        trait_node = node.child_by_field_name("trait")
        if trait_node is not None:
            trait_name = self._type_name(trait_node)
            if trait_name:
                out.references.append(
                    RawReference(
                        kind="inherit",
                        callee_text=_norm(trait_name),
                        callee_name=_norm(trait_name).rsplit(".", 1)[-1],
                        receiver_text=None,
                        span=span_of(node),
                        caller_qualified_name=parent_q,
                    )
                )

        body = node.child_by_field_name("body")
        if body is None:
            return
        for child in body.named_children:
            if child.type != "function_item":
                continue
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            qname = f"{parent_q}.{name}"
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.METHOD,
                    name=name,
                    qualified_name=qname,
                    span=span_of(child),
                    parent_qualified_name=parent_q,
                    signature=self._signature(child),
                    decorators=self._decorators(child),
                    is_exported=self._is_pub(child),
                )
            )
            self._emit_decorator_refs(child, qname, out)

    # ---------------- imports ----------------

    def _extract_imports(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("rust", "imports"), root)
        for node in caps.get("import", []):
            arg = next((c for c in node.named_children if c.type != "visibility_modifier"), None)
            if arg is None:
                continue
            self._unroll_use(arg, "", node, out)
        for imp in out.imports:
            if imp.module:
                out.framework_signals.append(("import", imp.module.split(".")[0]))

    def _unroll_use(self, node: Node, prefix: str, decl: Node, out: FileExtraction) -> None:
        """Recursively flatten a use-tree into RawImport rows.

        `prefix` is the accumulated dotted module path to the left of `node`.
        """
        t = node.type
        if t == "scoped_identifier":
            full = self._join(prefix, _norm(node_text(node)))
            alias = full.rsplit(".", 1)[-1]
            out.imports.append(
                RawImport(module=full, imported_name=None, alias=alias, span=span_of(decl))
            )
        elif t == "identifier":
            full = self._join(prefix, node_text(node))
            alias = full.rsplit(".", 1)[-1]
            out.imports.append(
                RawImport(module=full, imported_name=None, alias=alias, span=span_of(decl))
            )
        elif t == "use_as_clause":
            path_node = node.child_by_field_name("path")
            alias_node = node.child_by_field_name("alias")
            if path_node is None:
                path_node = node.named_children[0] if node.named_children else None
            if alias_node is None and len(node.named_children) > 1:
                alias_node = node.named_children[-1]
            if path_node is None or alias_node is None:
                return
            full = self._join(prefix, _norm(node_text(path_node)))
            out.imports.append(
                RawImport(
                    module=full, imported_name=None, alias=node_text(alias_node), span=span_of(decl)
                )
            )
        elif t == "scoped_use_list":
            path_node = node.child_by_field_name("path")
            base = _norm(node_text(path_node)) if path_node is not None else ""
            new_prefix = self._join(prefix, base)
            list_node = next((c for c in node.named_children if c.type == "use_list"), None)
            if list_node is not None:
                for child in list_node.named_children:
                    self._unroll_use(child, new_prefix, decl, out)
        elif t == "use_list":
            for child in node.named_children:
                self._unroll_use(child, prefix, decl, out)
        elif t == "use_wildcard":
            path_node = next(
                (c for c in node.named_children if c.type in ("scoped_identifier", "identifier")),
                None,
            )
            base = _norm(node_text(path_node)) if path_node is not None else prefix
            module = self._join(prefix, base) if path_node is not None else prefix
            out.imports.append(
                RawImport(module=module, imported_name="*", alias="*", span=span_of(decl))
            )
        elif t == "self":
            # `use std::fs::{self, ..}` -> bind the module itself under its last segment
            alias = prefix.rsplit(".", 1)[-1] if prefix else "self"
            out.imports.append(
                RawImport(module=prefix, imported_name=None, alias=alias, span=span_of(decl))
            )

    @staticmethod
    def _join(prefix: str, rest: str) -> str:
        if not prefix:
            return rest
        if not rest:
            return prefix
        return f"{prefix}.{rest}"

    # ---------------- calls / macros ----------------

    def _extract_calls(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("rust", "calls"), root)

        for node in caps.get("call", []):
            fn = node.child_by_field_name("function")
            if fn is None:
                continue
            args = node.child_by_field_name("arguments")
            if fn.type == "identifier":
                callee_text = callee_name = node_text(fn)
                receiver = None
            elif fn.type == "scoped_identifier":
                text = _norm(node_text(fn))
                callee_text = text
                callee_name = text.rsplit(".", 1)[-1]
                receiver = text.rsplit(".", 1)[0] if "." in text else None
            elif fn.type == "field_expression":
                field = fn.child_by_field_name("field")
                value = fn.child_by_field_name("value")
                if field is None:
                    continue
                callee_name = node_text(field)
                receiver = _norm(node_text(value)) if value is not None else None
                callee_text = f"{receiver}.{callee_name}" if receiver else callee_name
            else:
                continue
            caller = self._caller(node, ctx)
            out.references.append(
                RawReference(
                    kind="call",
                    callee_text=callee_text,
                    callee_name=callee_name,
                    receiver_text=receiver,
                    span=span_of(node),
                    caller_qualified_name=caller,
                    arg_count=len(args.named_children) if args is not None else 0,
                    arg_preview=truncate(node_text(args)) if args is not None else None,
                )
            )
            self._emit_callbacks(args, caller, out)

        for node in caps.get("macro", []):
            path_node = node.child_by_field_name("macro")
            if path_node is None:
                path_node = next(
                    (
                        c
                        for c in node.named_children
                        if c.type in ("scoped_identifier", "identifier")
                    ),
                    None,
                )
            if path_node is None:
                continue
            text = _norm(node_text(path_node))  # `!` is not part of the path node
            callee_name = text.rsplit(".", 1)[-1]
            receiver = text.rsplit(".", 1)[0] if "." in text else None
            token_tree = next((c for c in node.named_children if c.type == "token_tree"), None)
            out.references.append(
                RawReference(
                    kind="call",
                    callee_text=text,
                    callee_name=callee_name,
                    receiver_text=receiver,
                    span=span_of(node),
                    caller_qualified_name=self._caller(node, ctx),
                    arg_count=len(list(token_tree.named_children)) if token_tree else 0,
                    arg_preview=truncate(node_text(token_tree)) if token_tree else None,
                )
            )

    def _emit_callbacks(self, args: Node | None, caller: str | None, out: FileExtraction) -> None:
        """Bare-identifier arguments passed to a call — a function value invoked
        later, e.g. ``axum::routing::post(handler)``. Resolution keeps only those
        binding to a project function, so a plain data value is a no-op edge."""
        if args is None:
            return
        for arg in args.named_children:
            if arg.type != "identifier":
                continue
            name = node_text(arg)
            out.references.append(
                RawReference(
                    kind="callback",
                    callee_text=name,
                    callee_name=name,
                    receiver_text=None,
                    span=span_of(arg),
                    caller_qualified_name=caller,
                )
            )

    # ---------------- attributes (decorators) ----------------

    def _attribute_nodes(self, node: Node) -> list[Node]:
        """Leading `attribute_item` siblings immediately preceding an item."""
        attrs: list[Node] = []
        prev = node.prev_named_sibling
        while prev is not None and prev.type == _ATTRIBUTE:
            attrs.append(prev)
            prev = prev.prev_named_sibling
        attrs.reverse()
        return attrs

    def _decorators(self, node: Node) -> list[str]:
        return [node_text(a) for a in self._attribute_nodes(node)]

    def _emit_decorator_refs(self, node: Node, owner_qname: str, out: FileExtraction) -> None:
        for attr in self._attribute_nodes(node):
            inner = next((c for c in attr.named_children if c.type == "attribute"), None)
            if inner is None:
                continue
            path_node = next(
                (c for c in inner.named_children if c.type in ("scoped_identifier", "identifier")),
                None,
            )
            if path_node is None:
                continue
            callee_text = _norm(node_text(path_node))
            out.references.append(
                RawReference(
                    kind="decorator",
                    callee_text=callee_text,
                    callee_name=callee_text.rsplit(".", 1)[-1],
                    receiver_text=callee_text.rsplit(".", 1)[0] if "." in callee_text else None,
                    span=span_of(attr),
                    caller_qualified_name=owner_qname,
                )
            )

    # ---------------- walking helpers ----------------

    def _in_impl(self, node: Node) -> bool:
        """True if this function_item is a direct member of an impl body.

        Only direct impl members are handled by _add_impl; nested functions
        inside a free function are still module-scope free functions.
        """
        parent = node.parent
        if parent is not None and parent.type == "declaration_list":
            grand = parent.parent
            return grand is not None and grand.type == "impl_item"
        return False

    def _type_name(self, node: Node | None) -> str | None:
        if node is None:
            return None
        if node.type in ("type_identifier", "identifier"):
            return node_text(node)
        if node.type in ("scoped_type_identifier", "scoped_identifier"):
            return node_text(node).rsplit("::", 1)[-1]
        if node.type == "generic_type":
            base = node.child_by_field_name("type") or (
                node.named_children[0] if node.named_children else None
            )
            return self._type_name(base)
        # fall back to raw text's last segment
        return node_text(node).split("<", 1)[0].rsplit("::", 1)[-1] or None

    def _is_pub(self, node: Node) -> bool:
        return any(c.type == "visibility_modifier" for c in node.named_children)

    def _caller(self, node: Node, ctx: FileContext) -> str | None:
        """FQN of the enclosing function/method, or None for module level."""
        current = node.parent
        while current is not None:
            if current.type == "function_item":
                name_node = current.child_by_field_name("name")
                if name_node is None:
                    current = current.parent
                    continue
                name = node_text(name_node)
                impl = self._enclosing_impl(current)
                if impl is not None:
                    type_node = impl.child_by_field_name("type")
                    type_name = self._type_name(type_node)
                    if type_name:
                        return f"{ctx.module_path}.{type_name}.{name}"
                return f"{ctx.module_path}.{name}"
            current = current.parent
        return None

    def _enclosing_impl(self, node: Node) -> Node | None:
        current = node.parent
        while current is not None:
            if current.type == "impl_item":
                return current
            if current.type == "function_item":
                return None  # nested fn inside a free fn
            current = current.parent
        return None

    def _signature(self, node: Node) -> str:
        first_line = node_text(node).split("\n", 1)[0].rstrip("{").strip()
        return truncate(first_line, 120)
