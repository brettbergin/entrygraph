"""Ruby extractor: .scm queries harvest nodes, this shaper builds the IR.

Ruby resolution is intentionally loose. Files map to dotted module paths by
convention (path minus ``.rb``, common roots stripped), classes and modules
nest via ``constant`` names, and ``require`` calls are the closest thing to
imports. Because Ruby has no static type information, most cross-object method
calls resolve FUZZY (unique project name) or stay UNRESOLVED as ``rb:*.name``.
That is honest: the graph reports what it can prove and no more.
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

import os

_SRC_ROOTS = ("app", "lib", "src")
# scope-defining nodes whose names build a qualified-name chain
_SCOPE_TYPES = frozenset({"class", "module", "method", "singleton_method"})
_METHOD_SCOPES = frozenset({"method", "singleton_method"})
_REQUIRE_METHODS = frozenset({"require", "require_relative", "load"})


class RubyExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("ruby",)

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        parts = repo_relative_path.split("/")
        if parts and parts[0] in _SRC_ROOTS and len(parts) > 1:
            parts = parts[1:]
        parts[-1] = parts[-1].removesuffix(".rb")
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
        self._extract_definitions(root, ctx, out)
        self._extract_imports(root, ctx, out)
        self._extract_calls(root, ctx, out)
        return out

    # ---------------- definitions ----------------

    def _extract_definitions(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("ruby", "definitions"), root)

        for node in caps.get("def.module", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            qname, parent_q = self._qualify(node, name, ctx)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.MODULE,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=self._signature(node),
                )
            )

        for node in caps.get("def.class", []):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            qname, parent_q = self._qualify(node, name, ctx)
            bases = self._class_bases(node)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.CLASS,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=self._signature(node),
                    bases=bases,
                )
            )
            for base in bases:
                out.references.append(
                    RawReference(
                        kind="inherit",
                        callee_text=base,
                        callee_name=base.rsplit("::", 1)[-1].rsplit(".", 1)[-1],
                        receiver_text=None,
                        span=span_of(node),
                        caller_qualified_name=qname,
                    )
                )

        for capture in ("def.method", "def.singleton_method"):
            for node in caps.get(capture, []):
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = node_text(name_node)
                qname, parent_q = self._qualify(node, name, ctx)
                modifiers = ["self"] if node.type == "singleton_method" else []
                out.symbols.append(
                    RawSymbol(
                        kind=SymbolKind.METHOD,
                        name=name,
                        qualified_name=qname,
                        span=span_of(node),
                        parent_qualified_name=parent_q,
                        signature=self._signature(node),
                        modifiers=modifiers,
                    )
                )

        for node in caps.get("def.assign.constant", []):
            if self._nearest_scope_type(node) in _METHOD_SCOPES:
                continue  # local constant inside a method — skip
            left = node.child_by_field_name("left")
            if left is None:
                continue
            name = node_text(left)
            qname, parent_q = self._qualify(node, name, ctx)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.CONSTANT,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=truncate(node_text(node)),
                )
            )

        for node in caps.get("def.assign.variable", []):
            if self._nearest_scope_type(node) is not None:
                continue  # only top-level (global-ish) assignments become symbols
            left = node.child_by_field_name("left")
            if left is None:
                continue
            name = node_text(left)
            qname, parent_q = self._qualify(node, name, ctx)
            out.symbols.append(
                RawSymbol(
                    kind=SymbolKind.VARIABLE,
                    name=name,
                    qualified_name=qname,
                    span=span_of(node),
                    parent_qualified_name=parent_q,
                    signature=truncate(node_text(node)),
                )
            )

    # ---------------- imports ----------------

    def _extract_imports(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("ruby", "imports"), root)
        for node in caps.get("import.call", []):
            method_node = node.child_by_field_name("method")
            if method_node is None or node_text(method_node) not in _REQUIRE_METHODS:
                continue
            args = node.child_by_field_name("arguments")
            if args is None:
                continue
            spec = self._first_string_arg(args)
            if spec is None:
                continue
            is_relative = node_text(method_node) == "require_relative" or spec.startswith(".")
            if is_relative:
                # Pre-expand relative requires to a project-style dotted module so
                # the IMPORTS edge points at the required file. Ruby creates no
                # local name binding for a require, so we deliberately use the
                # full dotted path as the alias — a single-segment basename would
                # otherwise hijack same-named local-variable method calls in the
                # resolver's import map and defeat fuzzy resolution.
                module = self._expand_relative_require(spec, ctx)
                alias = module
            else:
                module = spec
                alias = os.path.basename(spec).removesuffix(".rb") or spec
            out.imports.append(
                RawImport(
                    module=module,
                    imported_name=None,
                    alias=alias,
                    span=span_of(node),
                    is_relative=False,  # already expanded; keep resolver logic simple
                )
            )
        # Ruby resolution is fuzzy; still surface require targets as framework
        # signals so specs like sinatra fire on `require 'sinatra'`.
        for imp in out.imports:
            top = imp.module.strip("./").replace("/", ".").split(".")[0]
            if top:
                out.framework_signals.append(("import", top))

    # ---------------- calls ----------------

    def _extract_calls(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("ruby", "calls"), root)
        for node in caps.get("call", []):
            method_node = node.child_by_field_name("method")
            if method_node is None:
                continue
            callee_name = node_text(method_node)
            receiver_node = node.child_by_field_name("receiver")
            if receiver_node is not None:
                receiver = node_text(receiver_node)
                callee_text = f"{receiver}.{callee_name}"
            elif self._caller(node, ctx) is not None:
                # Bare call inside a method is an implicit-self send in Ruby;
                # model it as a self-receiver so the resolver can bind it to a
                # sibling method on the enclosing class (fuzzy otherwise).
                receiver, callee_text = "self", callee_name
            else:
                # Bare call at module/top level (e.g. `get '/x'`, `require`).
                receiver, callee_text = None, callee_name
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

        for node in caps.get("element", []):
            self._emit_element_source(node, ctx, out)

    def _emit_element_source(self, node: Node, ctx: FileContext, out: FileExtraction) -> None:
        """`params[:id]` reads request input but is a subscript, not a call, so it
        yields no source edge. Synthesize a bare `params` accessor read carrying the
        key when the object is a request accessor root (#87C)."""
        kids = node.named_children
        if len(kids) < 2 or kids[0].type != "identifier":
            return
        root = node_text(kids[0])
        if root not in ACCESSOR_ROOTS:
            return
        key = subscript_key(node_text(kids[1]))  # params[:id] -> id
        out.references.append(
            RawReference(
                kind="call",
                callee_text=root,
                callee_name=root,
                receiver_text=None,
                span=span_of(node),
                caller_qualified_name=self._caller(node, ctx),
                arg_count=1,
                arg_preview=f'("{key}")' if key else None,
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

    def _nearest_scope_type(self, node: Node) -> str | None:
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
        """FQN of the enclosing method, or None for module/top level."""
        current = node.parent
        while current is not None:
            if current.type in _METHOD_SCOPES:
                name = current.child_by_field_name("name")
                if name is not None:
                    return self._qualify(current, node_text(name), ctx)[0]
            current = current.parent
        return None

    def _signature(self, node: Node) -> str:
        first_line = node_text(node).split("\n", 1)[0].rstrip()
        return truncate(first_line, 120)

    def _class_bases(self, node: Node) -> list[str]:
        supers = node.child_by_field_name("superclass")
        if supers is None:
            return []
        # superclass node is "< Base"; its named child is the actual type expr
        for child in supers.named_children:
            text = node_text(child).strip()
            if text:
                return [text]
        return []

    def _expand_relative_require(self, spec: str, ctx: FileContext) -> str:
        """`require_relative './services/runner'` from module `app` -> the
        project dotted module `services.runner`, mirroring module_path_for."""
        parts = ctx.module_path.split(".")[:-1]  # directory of the current file
        for segment in spec.split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                parts = parts[:-1]
            else:
                parts.append(segment.removesuffix(".rb"))
        return ".".join(p for p in parts if p) or "_root"

    def _first_string_arg(self, args: Node) -> str | None:
        for child in args.named_children:
            if child.type == "string":
                content = "".join(
                    node_text(c) for c in child.named_children if c.type == "string_content"
                )
                if content:
                    return content
                # empty or interpolated: fall back to stripped literal text
                return node_text(child).strip("'\"")
        return None
