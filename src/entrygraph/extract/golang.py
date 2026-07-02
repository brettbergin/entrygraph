"""Go extractor: .scm queries harvest nodes, this shaper builds the IR.

Go qualified names are package-path based. The module path is the file's
directory (posix, ``/`` -> ``.``); the file stem is *not* part of the qname,
since Go symbols live at package scope. Methods carry their receiver type in
the qname (e.g. ``cmd.server.Server.Run``). Exported-ness in Go is the
capitalization of the first letter of the name.

Imports bind an alias into local scope: ``import "os/exec"`` binds ``exec``
(last path segment) -> module ``os/exec``; ``import f "fmt"`` binds ``f`` ->
``fmt``. All imports are treated as external for v1 (module kept as written),
and each import emits a framework signal so gin/echo/cobra/net-http detection
fires. A selector call ``exec.Command`` is then import-expanded by the shared
resolver to the external ``go:os/exec.Command`` that sink patterns match.
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


def _exported(name: str) -> bool:
    return bool(name) and name[0].isupper()


class GoExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("go",)

    def module_path_for(self, repo_relative_path: str) -> tuple[bool | str, bool]:
        parts = repo_relative_path.split("/")
        if parts and parts[0] in _SRC_ROOTS and len(parts) > 1:
            parts = parts[1:]
        parts = parts[:-1]  # drop the file name; Go symbols live at package scope
        module = ".".join(p for p in parts if p) or "_root"
        return module, False  # Go packages are never treated as is_package here

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
        caps = captures(load_query("go", "definitions"), root)

        for node in caps.get("def.function", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.FUNCTION,
                    name=name,
                    qualified_name=f"{ctx.module_path}.{name}",
                    span=span_of(node),
                    parent_qualified_name=None,
                    signature=self._signature(node),
                    is_exported=_exported(name),
                )
            )

        for node in caps.get("def.method", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            receiver = self._receiver_type(node)
            if receiver:
                parent_q = f"{ctx.module_path}.{receiver}"
                qname = f"{parent_q}.{name}"
            else:
                parent_q = None
                qname = f"{ctx.module_path}.{name}"
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.METHOD,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=self._signature(node),
                    is_exported=_exported(name),
                )
            )

        for node in caps.get("def.struct", []):
            self._add_type(node, ctx, out, SymbolKind.STRUCT)
        for node in caps.get("def.interface", []):
            self._add_type(node, ctx, out, SymbolKind.INTERFACE)

        self._add_type_members(caps, ctx, out)

        for node in caps.get("def.const", []):
            self._add_value(node, ctx, out, SymbolKind.CONSTANT)
        for node in caps.get("def.var", []):
            self._add_value(node, ctx, out, SymbolKind.VARIABLE)

    def _add_type(
        self, node: Node, ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        # node is the type_declaration; the name is on its type_spec child.
        spec = next((c for c in node.named_children if c.type == "type_spec"), None)
        if spec is None:
            return
        name_node = spec.child_by_field_name("name")
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
                signature=self._signature(node),
                is_exported=_exported(name),
            )
        )

    def _add_type_members(self, caps, ctx: FileContext, out: FileExtraction) -> None:
        for node in caps.get("def.field", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            owner = self._enclosing_type_name(node)
            if owner is None:
                continue
            parent_q = f"{ctx.module_path}.{owner}"
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.FIELD,
                    name=name,
                    qualified_name=f"{parent_q}.{name}",
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=truncate(node_text(node)),
                    is_exported=_exported(name),
                )
            )

    def _add_value(
        self, node: Node, ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        # node is the const_declaration / var_declaration; names live on specs.
        for spec in node.named_children:
            if spec.type not in ("const_spec", "var_spec"):
                continue
            name_node = spec.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            out.symbols.append(
                RawSymbol(
                    kind=kind,
                    name=name,
                    qualified_name=f"{ctx.module_path}.{name}",
                    span=span_of(spec),
                    parent_qualified_name=None,
                    signature=truncate(node_text(spec)),
                    is_exported=_exported(name),
                )
            )

    # ---------------- imports ----------------

    def _extract_imports(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("go", "imports"), root)
        for node in caps.get("import", []):
            for spec in self._import_specs(node):
                self._add_import(spec, node, out)
        for imp in out.imports:
            out.framework_signals.append(("import", imp.module))

    def _import_specs(self, node: Node) -> list[Node]:
        specs: list[Node] = []
        for child in node.named_children:
            if child.type == "import_spec":
                specs.append(child)
            elif child.type == "import_spec_list":
                specs.extend(c for c in child.named_children if c.type == "import_spec")
        return specs

    def _add_import(self, spec: Node, node: Node, out: FileExtraction) -> None:
        path_node = next(
            (c for c in spec.named_children if c.type == "interpreted_string_literal"), None
        )
        if path_node is None:
            return
        module = node_text(path_node).strip('"')
        alias_node = spec.child_by_field_name("name")
        if alias_node is None:
            alias_node = next(
                (
                    c
                    for c in spec.named_children
                    if c.type in ("package_identifier", "identifier", "dot", "blank_identifier")
                ),
                None,
            )
        if alias_node is not None:
            alias = node_text(alias_node)
        else:
            alias = module.rstrip("/").split("/")[-1]
        out.imports.append(
            RawImport(module=module, imported_name=None, alias=alias, span=span_of(spec))
        )

    # ---------------- calls / composite literals ----------------

    def _extract_calls(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("go", "calls"), root)

        for node in caps.get("call", []):
            fn = node.child_by_field_name("function")
            if fn is None:
                continue
            if fn.type == "identifier":
                callee_text, callee_name, receiver = node_text(fn), node_text(fn), None
            elif fn.type == "selector_expression":
                operand = fn.child_by_field_name("operand")
                field = fn.child_by_field_name("field")
                if operand is None or field is None:
                    continue
                callee_text, callee_name, receiver = (
                    node_text(fn),
                    node_text(field),
                    node_text(operand),
                )
            else:
                continue  # call of a call / index expr / etc. — not statically resolvable
            args = node.child_by_field_name("arguments")
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

        for node in caps.get("composite.type", []):
            text = node_text(node)  # e.g. "cobra.Command"
            out.references.append(
                RawReference(
                    kind="composite",
                    callee_text=text,
                    callee_name=text.rsplit(".", 1)[-1],
                    receiver_text=text.rsplit(".", 1)[0] if "." in text else None,
                    span=span_of(node),
                    caller_qualified_name=self._caller(node, ctx),
                )
            )

    def _emit_callbacks(self, args: Node | None, caller: str | None, out: FileExtraction) -> None:
        """Bare-identifier arguments passed to a call — a function value that may be
        invoked later, e.g. ``http.HandleFunc("/", handler)``. Resolution keeps only
        those binding to a project function, so passing a plain data value is a
        harmless no-op edge."""
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

    # ---------------- walking helpers ----------------

    def _receiver_type(self, method: Node) -> str | None:
        """The bare receiver type name of a method_declaration (deref pointers)."""
        receiver = method.child_by_field_name("receiver")
        if receiver is None:
            # receiver is the first parameter_list child of the method_declaration
            receiver = next((c for c in method.named_children if c.type == "parameter_list"), None)
        if receiver is None:
            return None
        decl = next((c for c in receiver.named_children if c.type == "parameter_declaration"), None)
        if decl is None:
            return None
        type_node = decl.child_by_field_name("type")
        if type_node is None:
            type_node = next(
                (
                    c
                    for c in decl.named_children
                    if c.type in ("type_identifier", "pointer_type", "generic_type")
                ),
                None,
            )
        return self._type_name(type_node)

    def _type_name(self, node: Node | None) -> str | None:
        if node is None:
            return None
        if node.type == "type_identifier":
            return node_text(node)
        if node.type == "pointer_type":
            inner = next(
                (c for c in node.named_children if c.type in ("type_identifier", "generic_type")),
                None,
            )
            return self._type_name(inner)
        if node.type == "generic_type":
            base = node.child_by_field_name("type") or (
                node.named_children[0] if node.named_children else None
            )
            return self._type_name(base)
        return None

    def _enclosing_type_name(self, node: Node) -> str | None:
        """Name of the type_spec that owns this field declaration."""
        current = node.parent
        while current is not None:
            if current.type == "type_spec":
                name = current.child_by_field_name("name")
                return node_text(name) if name is not None else None
            current = current.parent
        return None

    def _caller(self, node: Node, ctx: FileContext) -> str | None:
        """FQN of the enclosing func/method, or None for package level."""
        current = node.parent
        while current is not None:
            if current.type == "function_declaration":
                name = current.child_by_field_name("name")
                if name is not None:
                    return f"{ctx.module_path}.{node_text(name)}"
            elif current.type == "method_declaration":
                name = current.child_by_field_name("name")
                if name is not None:
                    receiver = self._receiver_type(current)
                    if receiver:
                        return f"{ctx.module_path}.{receiver}.{node_text(name)}"
                    return f"{ctx.module_path}.{node_text(name)}"
            current = current.parent
        return None

    def _signature(self, node: Node) -> str:
        first_line = node_text(node).split("\n", 1)[0].rstrip("{").strip()
        return truncate(first_line, 120)
