"""JavaScript / TypeScript / TSX extractor.

One extractor drives three grammars. Module paths are dotted (path minus
extension, ``/`` -> ``.``) and relative imports are pre-expanded to dotted
project modules here, so the shared (Python-oriented) resolver treats them
uniformly. ``this`` is handled as a self-receiver by the resolver.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from entrygraph.extract.base import FileContext, node_text, span_of, truncate
from entrygraph.extract.ir import (
    FileExtraction,
    RawImport,
    RawReexport,
    RawReference,
    RawSymbol,
)
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.queries import captures, load_query_for

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Tree

_SCOPE_TYPES = frozenset(
    {
        "function_declaration",
        "method_definition",
        "class_declaration",
        "function_expression",
        "arrow_function",
    }
)
_NAMED_SCOPES = frozenset({"function_declaration", "method_definition", "class_declaration"})
_SRC_ROOTS = ("src", "lib", "app")
_EXPORT_PARENTS = frozenset({"export_statement"})


class JavaScriptExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("javascript", "typescript", "tsx")

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        parts = repo_relative_path.split("/")
        if parts[0] in _SRC_ROOTS and len(parts) > 1:
            parts = parts[1:]
        stem = parts[-1]
        for ext in (".d.ts", ".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs", ".jsx"):
            if stem.endswith(ext):
                stem = stem[: -len(ext)]
                break
        is_package = stem in ("index", "mod")
        parts = parts[:-1] if is_package else [*parts[:-1], stem]
        return ".".join(p for p in parts if p) or "_root", is_package

    def extract(self, tree: Tree, ctx: FileContext) -> FileExtraction:
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

    def _definitions(self, root, ctx, out) -> None:
        caps = captures(load_query_for(ctx.language, "javascript", "definitions"), root)

        for node in caps.get("def.function", []):
            self._add_callable(node, ctx, out, SymbolKind.FUNCTION)
        for node in caps.get("def.method", []):
            self._add_callable(node, ctx, out, SymbolKind.METHOD)

        for node in caps.get("def.class", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            qname, parent_q = self._qualify(node, name, ctx)
            bases, interfaces = self._heritage(node)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.CLASS,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=self._signature(node),
                    bases=bases,
                    decorators=self._decorators(node),
                    is_exported=self._exported(node),
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
            for iface in interfaces:  # TS `implements C, D`
                out.references.append(
                    RawReference(
                        kind="implement",
                        callee_text=iface,
                        callee_name=iface.rsplit(".", 1)[-1],
                        receiver_text=None,
                        span=span_of(node),
                        caller_qualified_name=qname,
                    )
                )

        for node in caps.get("def.var", []):
            scope = self._nearest_named_scope(node)
            if scope in (
                "function_declaration",
                "method_definition",
                "function_expression",
                "arrow_function",
            ):
                continue  # locals aren't symbols
            name_node = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name_node is None:
                continue
            # a `const x = () => {}` / function expression is a callable symbol
            if value is not None and value.type in ("arrow_function", "function_expression"):
                self._add_named_callable(node, name_node, ctx, out)
                continue
            name = node_text(name_node)
            qname, parent_q = self._qualify(node, name, ctx)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.VARIABLE,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=truncate(node_text(node)),
                    is_exported=self._exported(node),
                )
            )

    def _add_callable(self, node, ctx, out, kind) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        self._add_named_callable(node, name_node, ctx, out, kind)

    def _add_named_callable(self, node, name_node, ctx, out, kind=SymbolKind.FUNCTION) -> None:
        name = node_text(name_node)
        qname, parent_q = self._qualify(node, name, ctx)
        if kind is SymbolKind.FUNCTION and self._nearest_named_scope(node) == "class_declaration":
            kind = SymbolKind.METHOD
        out.symbols.append(
            RawSymbol(
                kind=kind,
                name=name,
                qualified_name=qname,
                span=span_of(node),
                parent_qualified_name=parent_q,
                signature=self._signature(node),
                decorators=self._decorators(node),
                is_exported=self._exported(node),
            )
        )

    # ---------------- imports ----------------

    def _imports(self, root, ctx, out) -> None:
        caps = captures(load_query_for(ctx.language, "javascript", "imports"), root)
        for node in caps.get("import", []):
            source = node.child_by_field_name("source")
            if source is None:
                continue
            module = self._resolve_module(node_text(source).strip("'\"`"), ctx)
            clause = next((c for c in node.named_children if c.type == "import_clause"), None)
            if clause is None:
                out.imports.append(
                    RawImport(
                        module=module,
                        imported_name=None,
                        alias=module.split(".")[-1],
                        span=span_of(node),
                    )
                )
                continue
            for child in clause.named_children:
                if child.type == "identifier":  # default import
                    # Bind the alias to the module itself (CommonJS/esModuleInterop
                    # semantics) so `import cp from 'child_process'; cp.exec()`
                    # canonicalizes to child_process.exec, not .default.exec.
                    out.imports.append(
                        RawImport(
                            module=module,
                            imported_name=None,
                            alias=node_text(child),
                            span=span_of(node),
                        )
                    )
                elif child.type == "namespace_import":
                    ident = child.named_children[-1]
                    out.imports.append(
                        RawImport(
                            module=module,
                            imported_name="*",
                            alias=node_text(ident),
                            span=span_of(node),
                        )
                    )
                elif child.type == "named_imports":
                    for spec in child.named_children:
                        if spec.type != "import_specifier":
                            continue
                        name_node = spec.child_by_field_name("name")
                        alias_node = spec.child_by_field_name("alias")
                        if name_node is None:
                            continue
                        name = node_text(name_node)
                        out.imports.append(
                            RawImport(
                                module=module,
                                imported_name=name,
                                alias=node_text(alias_node) if alias_node else name,
                                span=span_of(node),
                            )
                        )
        for node in caps.get("export.from", []):
            self._reexport(node, ctx, out)

        for imp in out.imports:
            out.framework_signals.append(
                ("import", imp.module.split(".")[0] if "." in imp.module else imp.module)
            )

    def _reexport(self, node, ctx, out) -> None:
        """`export { X as Y } from "./m"` / `export * from "./m"` -> RawReexport."""
        source = node.child_by_field_name("source")
        if source is None:
            return
        spec = node_text(source).strip("'\"`")
        module = self._resolve_module(spec, ctx)
        is_relative = spec.startswith(".")
        clause = next((c for c in node.named_children if c.type == "export_clause"), None)
        if clause is None:  # `export * from "..."`
            out.reexports.append(
                RawReexport(
                    module=module,
                    exported_name=None,
                    alias=None,
                    span=span_of(node),
                    is_star=True,
                    is_relative=is_relative,
                )
            )
            return
        for spec_node in clause.named_children:
            if spec_node.type != "export_specifier":
                continue
            name_node = spec_node.child_by_field_name("name")
            alias_node = spec_node.child_by_field_name("alias")
            if name_node is None:
                continue
            out.reexports.append(
                RawReexport(
                    module=module,
                    exported_name=node_text(name_node),
                    alias=node_text(alias_node) if alias_node else None,
                    span=span_of(node),
                    is_relative=is_relative,
                )
            )

    def _resolve_module(self, spec: str, ctx: FileContext) -> str:
        if not spec.startswith("."):
            return spec  # package import: keep as written (external)
        parts = ctx.module_path.split(".")
        parts = parts if ctx.is_package else parts[:-1]
        for segment in spec.split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                parts = parts[:-1]
            else:
                seg = segment
                for ext in (".ts", ".tsx", ".js", ".jsx"):
                    if seg.endswith(ext):
                        seg = seg[: -len(ext)]
                if seg not in ("index", "mod"):
                    parts.append(seg)
        return ".".join(p for p in parts if p) or "_root"

    # ---------------- calls ----------------

    def _calls(self, root, ctx, out) -> None:
        caps = captures(load_query_for(ctx.language, "javascript", "calls"), root)
        for node in caps.get("call", []):
            fn = node.child_by_field_name("function")
            if fn is None:
                continue
            args = node.child_by_field_name("arguments")
            caller = self._caller(node, ctx)
            if fn.type == "identifier":
                callee_text, callee_name, receiver = node_text(fn), node_text(fn), None
            elif fn.type == "member_expression":
                obj = fn.child_by_field_name("object")
                prop = fn.child_by_field_name("property")
                if obj is None or prop is None:
                    continue
                callee_text, callee_name, receiver = node_text(fn), node_text(prop), node_text(obj)
            elif fn.type == "subscript_expression":
                # obj[name]() — computed member call, target unknowable
                self._emit_dynamic_call(node, fn, caller, out)
                self._emit_callbacks(args, caller, out)
                continue
            else:
                continue
            out.references.append(
                RawReference(
                    kind="call",
                    callee_text=callee_text,
                    callee_name=callee_name,
                    receiver_text=receiver,
                    span=span_of(node),
                    caller_qualified_name=caller,
                    arg_count=len(args.named_children) if args else 0,
                    arg_preview=truncate(node_text(args)) if args else None,
                )
            )
            self._emit_callbacks(args, caller, out)

    def _emit_dynamic_call(self, node, fn, caller, out) -> None:
        args = node.child_by_field_name("arguments")
        out.references.append(
            RawReference(
                kind="dynamic_call",
                callee_text=node_text(fn),
                callee_name="<computed>",
                receiver_text=None,
                span=span_of(node),
                caller_qualified_name=caller,
                arg_count=len(args.named_children) if args else 0,
                arg_preview=truncate(node_text(args)) if args else None,
            )
        )

    def _emit_callbacks(self, args, caller, out) -> None:
        """Bare identifier / simple member args passed to a call (`setTimeout(fn)`)."""
        if args is None:
            return
        for arg in args.named_children:
            if arg.type == "identifier":
                name = node_text(arg)
            elif arg.type == "member_expression":
                prop = arg.child_by_field_name("property")
                if prop is None:
                    continue
                name = node_text(prop)
            else:
                continue
            out.references.append(
                RawReference(
                    kind="callback",
                    callee_text=node_text(arg),
                    callee_name=name,
                    receiver_text=None,
                    span=span_of(arg),
                    caller_qualified_name=caller,
                )
            )

    # ---------------- helpers ----------------

    def _scope_chain(self, node) -> list[str]:
        parts, current = [], node.parent
        while current is not None:
            if current.type in _NAMED_SCOPES:
                name = current.child_by_field_name("name")
                if name is not None:
                    parts.append(node_text(name))
            current = current.parent
        parts.reverse()
        return parts

    def _nearest_named_scope(self, node) -> str | None:
        current = node.parent
        while current is not None:
            if current.type in _SCOPE_TYPES:
                return current.type
            current = current.parent
        return None

    def _qualify(self, node, name, ctx) -> tuple[str, str | None]:
        chain = self._scope_chain(node)
        parent_q = ".".join([ctx.module_path, *chain]) if chain else None
        return ".".join([ctx.module_path, *chain, name]), parent_q

    def _caller(self, node, ctx) -> str | None:
        current = node.parent
        while current is not None:
            if current.type in ("function_declaration", "method_definition"):
                name = current.child_by_field_name("name")
                if name is not None:
                    return self._qualify(current, node_text(name), ctx)[0]
            if current.type == "variable_declarator":
                value = current.child_by_field_name("value")
                if value is not None and value.type in ("arrow_function", "function_expression"):
                    name = current.child_by_field_name("name")
                    if name is not None:
                        return self._qualify(current, node_text(name), ctx)[0]
            current = current.parent
        return None

    def _signature(self, node) -> str:
        return truncate(node_text(node).split("\n", 1)[0].rstrip("{").strip(), 120)

    def _decorators(self, node) -> list[str]:
        """Raw text of `decorator` nodes immediately preceding `node`.

        Decorators are siblings that appear just before their target — class
        decorators inside the enclosing `export_statement`/`program`, method
        decorators inside the `class_body`. A non-decorator sibling resets the
        run, so each target only picks up its own decorators.
        """
        parent = node.parent
        if parent is None:
            return []
        decs: list[str] = []
        for child in parent.named_children:
            if child.start_byte == node.start_byte and child.end_byte == node.end_byte:
                return decs
            if child.type == "decorator":
                decs.append(node_text(child))
            else:
                decs = []
        return decs

    def _heritage(self, node) -> tuple[list[str], list[str]]:
        """Return (extends bases, implements interfaces).

        Plain JS puts identifiers directly under `class_heritage`; TS wraps them
        in `extends_clause` / `implements_clause`.
        """
        heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
        if heritage is None:
            return [], []
        bases: list[str] = []
        interfaces: list[str] = []

        def _names(container) -> list[str]:
            out = []
            for child in container.named_children:
                if child.type in (
                    "identifier",
                    "member_expression",
                    "type_identifier",
                    "generic_type",
                    "nested_type_identifier",
                ):
                    text = node_text(child).split("<", 1)[0].strip()
                    if text:
                        out.append(text)
            return out

        saw_clause = False
        for child in heritage.named_children:
            if child.type == "extends_clause":
                saw_clause = True
                bases.extend(_names(child))
            elif child.type == "implements_clause":
                saw_clause = True
                interfaces.extend(_names(child))
        if not saw_clause:  # plain JS: identifiers directly under class_heritage
            bases.extend(_names(heritage))
        return bases, interfaces

    def _exported(self, node) -> bool:
        current = node.parent
        while current is not None:
            if current.type in _EXPORT_PARENTS:
                return True
            if current.type in ("program", "statement_block", "class_body"):
                break
            current = current.parent
        return False
