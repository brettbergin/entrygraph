r"""PHP extractor: .scm queries harvest nodes, this shaper builds the IR.

Module paths are the file's PHP namespace, ``\`` normalized to ``.`` (so
``namespace App\Http\Controllers;`` -> ``App.Http.Controllers``). A
namespace-less script (a plain ``index.php`` with no ``namespace`` line) falls
back to its directory-derived dotted path. PHP has no package concept in the IR
sense, so ``is_package`` is always False.

Qualified names are namespace-scoped and class-nested:
``App.Http.Controllers.ReportController.store``. The ``\`` separator is
normalized to ``.`` *everywhere* — qnames, base expressions, and import-map
values — so externals look like ``php:App.Services.Runner.run`` and the sink
globs stay dot-based.

PHP 8 attributes (``#[Route('/x')]``) are captured on ``RawSymbol.decorators``
as raw source text and emitted as ``RawReference(kind="decorator")`` — the same
shape Java uses for annotations — so entrypoint rules can match them.
``Foo::bar()`` yields receiver ``Foo`` / callee ``bar``; ``$obj->m()`` yields
receiver ``$obj``. ``include``/``require`` of a variable is modeled as a call
ref with ``callee_name="include"`` so it can be a sink.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from entrygraph.extract.base import FileContext, node_text, span_of, truncate
from entrygraph.extract.ir import FileExtraction, RawImport, RawReference, RawSymbol
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.queries import captures, load_query

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Node, Tree

_SCOPE_TYPES = frozenset({"class_declaration", "interface_declaration", "trait_declaration"})
_FUNC_SCOPES = frozenset({"function_definition", "method_declaration"})
_SRC_ROOTS = ("src", "lib", "app")


def _norm(text: str) -> str:
    r"""Normalize PHP's ``\`` namespace separator to ``.`` and strip leads."""
    return text.replace("\\", ".").strip(".")


class PhpExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("php",)

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        # The authoritative module path is the file's PHP namespace, computed in
        # extract(); this directory-derived fallback is only used for
        # namespace-less scripts. The scanner calls this before parse, so we
        # return the directory form and let extract() override via ctx.
        parts = repo_relative_path.split("/")
        if parts and parts[0] in _SRC_ROOTS and len(parts) > 1:
            parts = parts[1:]
        if parts:
            parts[-1] = parts[-1].removesuffix(".php").removesuffix(".phtml")
        return ".".join(p for p in parts if p) or "_root", False

    def extract(self, tree: Tree, ctx: FileContext) -> FileExtraction:
        root = tree.root_node
        namespace = self._namespace(root)
        # The namespace (when present) is the real module path — override the
        # directory-derived ctx.module_path so qnames are namespace-scoped.
        module_path = namespace or ctx.module_path
        out = FileExtraction(
            path=ctx.path,
            language=ctx.language,
            module_path=module_path,
            parse_ok=not root.has_error,
            error_count=1 if root.has_error else 0,
        )
        self._namespace_prefix = module_path
        self._definitions(root, ctx, out)
        self._imports(root, ctx, out)
        self._calls(root, ctx, out)
        return out

    # ---------------- namespace ----------------

    def _namespace(self, root: Node) -> str | None:
        caps = captures(load_query("php", "definitions"), root)
        for node in caps.get("namespace", []):
            name = next((c for c in node.named_children if c.type == "namespace_name"), None)
            if name is not None:
                return _norm(node_text(name))
        return None

    # ---------------- definitions ----------------

    def _definitions(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("php", "definitions"), root)

        for node in caps.get("def.class", []):
            self._add_type(node, ctx, out, SymbolKind.CLASS)
        for node in caps.get("def.interface", []):
            self._add_type(node, ctx, out, SymbolKind.INTERFACE)
        for node in caps.get("def.trait", []):
            self._add_type(node, ctx, out, SymbolKind.CLASS)

        for node in caps.get("def.function", []):
            self._add_callable(node, ctx, out, SymbolKind.FUNCTION)
        for node in caps.get("def.method", []):
            self._add_callable(node, ctx, out, SymbolKind.METHOD)

        for node in caps.get("def.const", []):
            for element in node.named_children:
                if element.type != "const_element":
                    continue
                name_node = element.child_by_field_name("name") or next(
                    (c for c in element.named_children if c.type == "name"), None
                )
                if name_node is None:
                    continue
                name = node_text(name_node)
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

        for node in caps.get("def.property", []):
            if self._nearest_scope(node) is None:
                continue
            modifiers = self._prop_modifiers(node)
            for element in node.named_children:
                if element.type != "property_element":
                    continue
                var = next((c for c in element.named_children if c.type == "variable_name"), None)
                name_node = var.child_by_field_name("name") if var else None
                if name_node is None and var is not None:
                    name_node = next((c for c in var.named_children if c.type == "name"), None)
                if name_node is None:
                    continue
                name = node_text(name_node)
                qname, parent_q = self._qualify(node, name, ctx)
                out.symbols.append(
                    RawSymbol(
                        kind=SymbolKind.FIELD,
                        name=name,
                        qualified_name=qname,
                        span=span_of(node),
                        parent_qualified_name=parent_q,
                        signature=truncate(node_text(node)),
                        modifiers=modifiers,
                        is_exported="public" in modifiers or not modifiers,
                    )
                )

    def _add_type(
        self, node: Node, ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        qname, parent_q = self._qualify(node, name, ctx)
        extends, interfaces = self._supertypes(node)
        out.symbols.append(
            RawSymbol(
                kind=kind,
                name=name,
                qualified_name=qname,
                span=span_of(node),
                parent_qualified_name=parent_q,
                signature=self._signature(node),
                decorators=self._attributes(node),
                bases=[*extends, *interfaces],
            )
        )
        for base in extends:
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
        for iface in interfaces:
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
        self._emit_attribute_refs(node, qname, out)

    def _add_callable(
        self, node: Node, ctx: FileContext, out: FileExtraction, kind: SymbolKind
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        qname, parent_q = self._qualify(node, name, ctx)
        modifiers = self._method_modifiers(node)
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
                is_exported="private" not in modifiers and "protected" not in modifiers,
            )
        )
        self._emit_attribute_refs(node, qname, out)

    # ---------------- imports ----------------

    def _imports(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("php", "imports"), root)
        for node in caps.get("import", []):
            for clause in node.named_children:
                if clause.type != "namespace_use_clause":
                    continue
                qualified = next(
                    (c for c in clause.named_children if c.type in ("qualified_name", "name")),
                    None,
                )
                if qualified is None:
                    continue
                full = _norm(node_text(qualified))
                alias_node = clause.child_by_field_name("alias")
                last = full.rsplit(".", 1)[-1]
                alias = node_text(alias_node) if alias_node is not None else last
                out.imports.append(
                    RawImport(module=full, imported_name=last, alias=alias, span=span_of(node))
                )
        for imp in out.imports:
            if imp.module:
                out.framework_signals.append(("import", imp.module))

    # ---------------- calls / attributes ----------------

    def _calls(self, root: Node, ctx: FileContext, out: FileExtraction) -> None:
        caps = captures(load_query("php", "calls"), root)

        for node in caps.get("call", []):
            fn = node.child_by_field_name("function")
            if fn is None:
                continue
            callee_text = _norm(node_text(fn))
            callee_name = callee_text.rsplit(".", 1)[-1]
            receiver = callee_text.rsplit(".", 1)[0] if "." in callee_text else None
            self._emit_call(node, callee_text, callee_name, receiver, ctx, out)

        for node in caps.get("member_call", []):
            name_node = node.child_by_field_name("name")
            obj = node.child_by_field_name("object")
            if name_node is None or obj is None:
                continue
            callee_name = node_text(name_node)
            receiver = node_text(obj)
            self._emit_call(node, f"{receiver}.{callee_name}", callee_name, receiver, ctx, out)

        for node in caps.get("scoped_call", []):
            name_node = node.child_by_field_name("name")
            scope = node.child_by_field_name("scope")
            if name_node is None or scope is None:
                continue
            callee_name = node_text(name_node)
            receiver = _norm(node_text(scope))
            self._emit_call(node, f"{receiver}.{callee_name}", callee_name, receiver, ctx, out)

        for node in caps.get("new", []):
            type_node = next((c for c in node.named_children if c.type != "arguments"), None)
            if type_node is None:
                continue
            type_text = _norm(node_text(type_node))
            callee_name = type_text.rsplit(".", 1)[-1]
            self._emit_call(node, type_text, callee_name, None, ctx, out)

        for node in caps.get("include", []):
            # include/require of anything: model as a bare call so it can be a
            # sink. `include $var` -> callee_name="include" with the variable as
            # arg_preview.
            arg = node.named_children[0] if node.named_children else None
            out.references.append(
                RawReference(
                    kind="call",
                    callee_text="include",
                    callee_name="include",
                    receiver_text=None,
                    span=span_of(node),
                    caller_qualified_name=self._caller(node, ctx),
                    arg_count=1 if arg is not None else 0,
                    arg_preview=truncate(node_text(arg)) if arg is not None else None,
                )
            )

    def _emit_call(
        self,
        node: Node,
        callee_text: str,
        callee_name: str,
        receiver: str | None,
        ctx: FileContext,
        out: FileExtraction,
    ) -> None:
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

    # ---------------- helpers ----------------

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
        base = self._namespace_prefix
        prefix_parts = [base, *chain] if base else [*chain]
        parent_q = ".".join(prefix_parts) if prefix_parts else None
        qname = ".".join([*prefix_parts, name])
        return qname, parent_q

    def _caller(self, node: Node, ctx: FileContext) -> str | None:
        current = node.parent
        while current is not None:
            if current.type in _FUNC_SCOPES:
                name = current.child_by_field_name("name")
                if name is not None:
                    return self._qualify(current, node_text(name), ctx)[0]
            current = current.parent
        return None

    def _signature(self, node: Node) -> str:
        return truncate(node_text(node).split("{", 1)[0].strip(), 120)

    def _method_modifiers(self, node: Node) -> list[str]:
        mods: list[str] = []
        for child in node.children:
            if child.type in (
                "visibility_modifier",
                "static_modifier",
                "abstract_modifier",
                "final_modifier",
            ):
                mods.append(node_text(child))
        return mods

    def _prop_modifiers(self, node: Node) -> list[str]:
        return [
            node_text(c)
            for c in node.named_children
            if c.type
            in ("visibility_modifier", "static_modifier", "readonly_modifier", "final_modifier")
        ]

    def _attribute_lists(self, node: Node) -> list[Node]:
        return [c for c in node.named_children if c.type == "attribute_list"]

    def _attributes(self, node: Node) -> list[str]:
        # The attribute_list node text already includes the leading `#[ ... ]`.
        return [node_text(attr_list) for attr_list in self._attribute_lists(node)]

    def _emit_attribute_refs(self, node: Node, owner_qname: str, out: FileExtraction) -> None:
        for attr_list in self._attribute_lists(node):
            for group in attr_list.named_children:
                if group.type != "attribute_group":
                    continue
                for attr in group.named_children:
                    if attr.type != "attribute":
                        continue
                    name_node = attr.child_by_field_name("name") or next(
                        (c for c in attr.named_children if c.type in ("name", "qualified_name")),
                        None,
                    )
                    if name_node is None:
                        continue
                    callee_text = _norm(node_text(name_node))
                    out.references.append(
                        RawReference(
                            kind="decorator",
                            callee_text=callee_text,
                            callee_name=callee_text.rsplit(".", 1)[-1],
                            receiver_text=(
                                callee_text.rsplit(".", 1)[0] if "." in callee_text else None
                            ),
                            span=span_of(attr),
                            caller_qualified_name=owner_qname,
                        )
                    )

    def _supertypes(self, node: Node) -> tuple[list[str], list[str]]:
        """Return (extends supertypes, implements interfaces)."""
        extends: list[str] = []
        interfaces: list[str] = []
        for child in node.named_children:
            if child.type == "base_clause":
                for name in child.named_children:
                    if name.type in ("name", "qualified_name"):
                        extends.append(_norm(node_text(name)))
            elif child.type == "class_interface_clause":
                for name in child.named_children:
                    if name.type in ("name", "qualified_name"):
                        interfaces.append(_norm(node_text(name)))
        return extends, interfaces
