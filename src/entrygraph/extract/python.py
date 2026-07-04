"""Python extractor: .scm queries harvest nodes, this shaper builds the IR.

This is the reference implementation the other language extractors copy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from entrygraph.extract.base import (
    ACCESSOR_ROOTS,
    FileContext,
    node_text,
    span_of,
    subscript_key,
    truncate,
)
from entrygraph.extract.ir import FileExtraction, RawImport, RawReference, RawSymbol
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.queries import captures, load_query

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Node, Tree

import re

_MAIN_GUARD = re.compile(rb"if\s+__name__\s*==")
_SCOPE_TYPES = frozenset({"class_definition", "function_definition"})
_SRC_ROOTS = ("src", "lib")


class PythonExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("python",)

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        parts = repo_relative_path.split("/")
        if parts[0] in _SRC_ROOTS and len(parts) > 1:
            parts = parts[1:]
        is_package = parts[-1] == "__init__.py"
        if is_package:
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1].removesuffix(".py").removesuffix(".pyi")
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
        self._extract_definitions(root, ctx, out)
        self._extract_imports(root, ctx, out)
        self._extract_calls(root, ctx, out)
        if _MAIN_GUARD.search(ctx.source):
            out.framework_signals.append(("main_guard", ctx.module_path))
        return out

    # ---------------- definitions ----------------

    def _extract_definitions(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("python", "definitions"), root)

        for node in caps.get("def.class", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            qname, parent_q = self._qualify(node, name, ctx)
            bases = self._class_bases(node)
            decorators = self._decorators(node)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.CLASS,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=self._signature(node),
                    decorators=decorators,
                    bases=bases,
                    docstring=self._docstring(node),
                    is_exported=not name.startswith("_"),
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

        for node in caps.get("def.function", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            qname, parent_q = self._qualify(node, name, ctx)
            kind = (
                SymbolKind.METHOD
                if self._nearest_scope(node) == "class_definition"
                else SymbolKind.FUNCTION
            )
            out.symbols.append(
                RawSymbol(
                    kind=kind,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=self._signature(node),
                    decorators=self._decorators(node),
                    docstring=self._docstring(node),
                    is_exported=not name.startswith("_"),
                )
            )

        for node in caps.get("def.assign", []):
            scope = self._nearest_scope(node)
            if scope == "function_definition":
                continue  # local variables are not symbols
            left = node.child_by_field_name("left")
            if left is None or left.type != "identifier":
                continue
            name = node_text(left)
            qname, parent_q = self._qualify(node, name, ctx)
            if scope == "class_definition":
                kind = SymbolKind.FIELD
            else:
                kind = SymbolKind.CONSTANT if name.isupper() else SymbolKind.VARIABLE
            out.symbols.append(
                RawSymbol(
                    kind=kind,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=truncate(node_text(node)),
                    is_exported=not name.startswith("_"),
                )
            )

    # ---------------- imports ----------------

    def _extract_imports(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("python", "imports"), root)

        for node in caps.get("import", []):
            for child in node.named_children:
                if child.type == "dotted_name":
                    module = node_text(child)
                    out.imports.append(
                        RawImport(
                            module=module,
                            imported_name=None,
                            alias=module.split(".")[0],
                            span=span_of(node),
                        )
                    )
                elif child.type == "aliased_import":
                    module_node = child.child_by_field_name("name")
                    alias_node = child.child_by_field_name("alias")
                    if module_node and alias_node:
                        out.imports.append(
                            RawImport(
                                module=node_text(module_node),
                                imported_name=None,
                                alias=node_text(alias_node),
                                span=span_of(node),
                            )
                        )

        for node in caps.get("import.from", []):
            module_node = node.child_by_field_name("module_name")
            if module_node is None:
                continue
            raw_module = node_text(module_node)
            level = len(raw_module) - len(raw_module.lstrip("."))
            module = raw_module.lstrip(".")
            for child in node.named_children[1:]:
                if child.type == "dotted_name":
                    name = node_text(child)
                    out.imports.append(
                        RawImport(
                            module=module,
                            imported_name=name,
                            alias=name.split(".")[0],
                            span=span_of(node),
                            is_relative=level > 0,
                            relative_level=level,
                        )
                    )
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    alias_node = child.child_by_field_name("alias")
                    if name_node and alias_node:
                        out.imports.append(
                            RawImport(
                                module=module,
                                imported_name=node_text(name_node),
                                alias=node_text(alias_node),
                                span=span_of(node),
                                is_relative=level > 0,
                                relative_level=level,
                            )
                        )
                elif child.type == "wildcard_import":
                    out.imports.append(
                        RawImport(
                            module=module,
                            imported_name="*",
                            alias="*",
                            span=span_of(node),
                            is_relative=level > 0,
                            relative_level=level,
                        )
                    )

        for imp in out.imports:
            if not imp.is_relative and imp.module:
                out.framework_signals.append(("import", imp.module.split(".")[0]))

    # ---------------- calls / decorators ----------------

    def _extract_calls(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("python", "calls"), root)

        for node in caps.get("call", []):
            fn = node.child_by_field_name("function")
            if fn is None:
                continue
            args = node.child_by_field_name("arguments")
            caller = self._caller(node, ctx)
            if fn.type == "identifier":
                callee_text, callee_name, receiver = node_text(fn), node_text(fn), None
            elif fn.type == "attribute":
                attr = fn.child_by_field_name("attribute")
                obj = fn.child_by_field_name("object")
                if attr is None or obj is None:
                    continue
                callee_text, callee_name, receiver = node_text(fn), node_text(attr), node_text(obj)
            else:
                # call of a call/subscript/etc. — target isn't statically knowable.
                # Emit a dynamic placeholder so reachability can flag "may continue".
                self._emit_dynamic_call(node, fn, caller, out)
                self._emit_callbacks(args, caller, out)
                continue
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

        for node in caps.get("subscript", []):
            self._emit_subscript_source(node, ctx, out)

        for node in caps.get("decorator", []):
            expr = node.named_children[0] if node.named_children else None
            if expr is None:
                continue
            if expr.type == "call":
                fn = expr.child_by_field_name("function")
                if fn is None:
                    continue
                target = fn
            elif expr.type in ("identifier", "attribute"):
                target = expr
            else:
                continue
            callee_text = node_text(target)
            out.references.append(
                RawReference(
                    kind="decorator",
                    callee_text=callee_text,
                    callee_name=callee_text.rsplit(".", 1)[-1],
                    receiver_text=callee_text.rsplit(".", 1)[0] if "." in callee_text else None,
                    span=span_of(node),
                    caller_qualified_name=self._caller(node, ctx),
                )
            )

    def _emit_subscript_source(self, node: Node, ctx: FileContext, out: FileExtraction) -> None:
        """`request.args["q"]` reads an input but is not a call, so it produces no
        source edge. Synthesize an accessor-read reference (matching the call form
        `request.args.get`) carrying the key, when rooted at a request accessor (#87C)."""
        value = node.child_by_field_name("value")
        index = node.child_by_field_name("subscript")
        if value is None or index is None or value.type != "attribute":
            return
        accessor = node_text(value)  # "request.args"
        root = accessor.split(".", 1)[0].split("[", 1)[0]
        if root not in ACCESSOR_ROOTS:
            return
        key = subscript_key(node_text(index))
        out.references.append(
            RawReference(
                kind="call",
                callee_text=accessor,
                callee_name=accessor.rsplit(".", 1)[-1],
                receiver_text=accessor.rsplit(".", 1)[0] if "." in accessor else None,
                span=span_of(node),
                caller_qualified_name=self._caller(node, ctx),
                arg_count=1,
                arg_preview=f'("{key}")' if key else None,
            )
        )

    def _emit_dynamic_call(
        self, node: Node, fn: Node, caller: str | None, out: FileExtraction
    ) -> None:
        """A call whose target is computed: getattr(...)(), handlers[name](), etc."""
        callee_name = "<dynamic>"
        if fn.type == "call":
            inner = fn.child_by_field_name("function")
            if inner is not None and inner.type == "identifier" and node_text(inner) == "getattr":
                callee_name = "getattr"
        args = node.child_by_field_name("arguments")
        out.references.append(
            RawReference(
                kind="dynamic_call",
                callee_text=node_text(fn),
                callee_name=callee_name,
                receiver_text=None,
                span=span_of(node),
                caller_qualified_name=caller,
                arg_count=len(args.named_children) if args is not None else 0,
                arg_preview=truncate(node_text(args)) if args is not None else None,
            )
        )

    def _emit_callbacks(self, args: Node | None, caller: str | None, out: FileExtraction) -> None:
        """Bare-identifier arguments (and identifier kwarg values) passed to a call.

        `schedule(task)` / `add(func=handler)` — the name may bind to a project
        function that is invoked later. Resolution decides whether it becomes an edge.
        """
        if args is None:
            return
        for arg in args.named_children:
            if arg.type == "identifier":
                target = arg
            elif arg.type == "keyword_argument":
                value = arg.child_by_field_name("value")
                if value is None or value.type != "identifier":
                    continue
                target = value
            else:
                continue
            name = node_text(target)
            out.references.append(
                RawReference(
                    kind="callback",
                    callee_text=name,
                    callee_name=name,
                    receiver_text=None,
                    span=span_of(target),
                    caller_qualified_name=caller,
                )
            )

    # ---------------- shared walking helpers ----------------

    def _scope_chain(self, node: Node) -> list[str]:
        parts: list[str] = []
        current = node.parent
        while current is not None:
            if current.type in _SCOPE_TYPES:
                name = current.child_by_field_name("name")
                if name is not None:
                    parts.append(node_text(name))
            current = current.parent
        parts.reverse()
        return parts

    def _nearest_scope(self, node: Node) -> str | None:
        current = node.parent
        while current is not None:
            if current.type in _SCOPE_TYPES:
                return current.type
            current = current.parent
        return None

    def _qualify(self, node: Node, name: str, ctx: FileContext) -> tuple[str, str | None]:
        chain = self._scope_chain(node)
        parent_q = ".".join([ctx.module_path, *chain]) if chain else None
        qname = ".".join([ctx.module_path, *chain, name])
        return qname, parent_q

    def _caller(self, node: Node, ctx: FileContext) -> str | None:
        """FQN of the enclosing def, or None for module level."""
        current = node.parent
        while current is not None:
            if current.type == "function_definition":
                name = current.child_by_field_name("name")
                if name is not None:
                    return self._qualify(current, node_text(name), ctx)[0]
            current = current.parent
        return None

    def _signature(self, node: Node) -> str:
        first_line = node_text(node).split("\n", 1)[0].rstrip(":")
        return truncate(first_line, 120)

    def _docstring(self, node: Node) -> str | None:
        body = node.child_by_field_name("body")
        if body is None or not body.named_children:
            return None
        first = body.named_children[0]
        if first.type == "string":  # class bodies: docstring is a bare string node
            expr = first
        elif first.type == "expression_statement" and first.named_children:
            expr = first.named_children[0]
        else:
            return None
        if expr.type != "string":
            return None
        text = node_text(expr).strip()
        for quote in ('"""', "'''", '"', "'"):
            if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
                return text[len(quote) : -len(quote)].strip()
        return text

    def _decorators(self, node: Node) -> list[str]:
        parent = node.parent
        if parent is None or parent.type != "decorated_definition":
            return []
        return [node_text(child) for child in parent.named_children if child.type == "decorator"]

    def _class_bases(self, node: Node) -> list[str]:
        supers = node.child_by_field_name("superclasses")
        if supers is None:
            return []
        bases: list[str] = []
        for child in supers.named_children:
            if child.type == "keyword_argument":  # metaclass=..., etc.
                continue
            text = node_text(child)
            base = text.split("[", 1)[0].strip()  # Generic[T] -> Generic
            if base:
                bases.append(base)
        return bases
