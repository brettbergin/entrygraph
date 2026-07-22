"""JavaScript / TypeScript / TSX extractor.

One extractor drives three grammars. Module paths are dotted (path minus
extension, ``/`` -> ``.``) and relative imports are pre-expanded to dotted
project modules here, so the shared (Python-oriented) resolver treats them
uniformly. ``this`` is handled as a self-receiver by the resolver.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from entrygraph.extract.base import (
    ACCESSOR_ROOTS,
    REQUEST_ACCESSOR_PROPS,
    FileContext,
    member_key,
    node_text,
    span_of,
    subscript_key,
    truncate,
)
from entrygraph.extract.ir import (
    FileExtraction,
    RawBinding,
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
_JS_EXTS = (".d.ts", ".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs", ".jsx")


def _atomic_segment(filename: str) -> str:
    """A filename -> one module-path segment: strip a JS/TS extension, then map any
    remaining internal dots to `_`.

    A dotted stem like ``article.service`` must NOT become two dotted segments, or
    ``.split(".")`` in relative-import resolution mis-counts package depth and
    duplicates a directory name (``article.article.article.service``) — issue #42.
    """
    for ext in _JS_EXTS:
        if filename.endswith(ext):
            filename = filename[: -len(ext)]
            break
    return filename.replace(".", "_")


def resolve_relative_module(spec: str, module_path: str, is_package: bool) -> str:
    """Resolve an import/require spec to a repo module path, relative to the file
    at ``module_path``. Non-relative specs (package imports) are returned as-is.

    Shared with cross-file Express mount resolution, which resolves an inline
    ``require('./router')`` mount target the same way an import would (#133)."""
    if not spec.startswith("."):
        return spec  # package import: keep as written (external)
    parts = module_path.split(".")
    parts = parts if is_package else parts[:-1]
    for segment in spec.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            parts = parts[:-1]
        else:
            seg = _atomic_segment(segment)
            if seg not in ("index", "mod"):
                parts.append(seg)
    return ".".join(p for p in parts if p) or "_root"


class JavaScriptExtractor:
    language_ids: ClassVar[tuple[str, ...]] = ("javascript", "typescript", "tsx")

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        parts = repo_relative_path.split("/")
        if parts[0] in _SRC_ROOTS and len(parts) > 1:
            parts = parts[1:]
        stem = _atomic_segment(parts[-1])
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
        self._object_literal_callables(root, ctx, out)
        self._imports(root, ctx, out)
        self._calls(root, ctx, out)
        self._bindings(root, ctx, out)
        out.default_export = self._default_export_ident(root) or self._commonjs_default_export(root)
        return out

    # ---------------- bindings (#98) ----------------

    def _bindings(self, root, ctx, out) -> None:
        """`const x = new Foo()` / `const x = express()` / TS `const x: Foo = ...`."""
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "variable_declarator":
                self._emit_declarator_binding(n, ctx, out)
            stack.extend(n.children)

    def _emit_declarator_binding(self, node, ctx, out) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            return
        name = node_text(name_node)
        scope = self._caller(node, ctx)
        type_ann = node.child_by_field_name("type")
        # TS `const x: Foo = ...` — the declared annotation type wins.
        if type_ann is not None:
            type_text = self._annotation_type(type_ann)
            if type_text:
                out.bindings.append(
                    RawBinding(
                        name=name,
                        type_text=type_text,
                        span=span_of(node),
                        scope=scope,
                        kind="declared",
                    )
                )
            return
        value = node.child_by_field_name("value")
        if value is None:
            return
        if value.type == "new_expression":
            ctor = value.child_by_field_name("constructor")
            if ctor is not None and ctor.type in ("identifier", "member_expression"):
                out.bindings.append(
                    RawBinding(
                        name=name,
                        type_text=node_text(ctor),
                        span=span_of(node),
                        scope=scope,
                        kind="constructor",
                    )
                )
        elif value.type == "call_expression":
            fn = value.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                callee = node_text(fn)
                # `require`/`import` bind a *module*, not a typed value — a
                # call_result here would shadow the correct import-based sink
                # resolution (`cp = require('child_process'); cp.exec`), so skip it.
                if callee in ("require", "import", "__require"):
                    return
                out.bindings.append(
                    RawBinding(
                        name=name,
                        type_text=callee,
                        span=span_of(node),
                        scope=scope,
                        kind="call_result",
                    )
                )

    def _annotation_type(self, type_ann) -> str | None:
        """The written type of a TS `type_annotation` (`: Foo` -> `Foo`), taking the
        base of a generic and skipping non-nominal types (unions, literals)."""
        inner = next(
            (c for c in type_ann.named_children if c.type in ("type_identifier", "generic_type")),
            None,
        )
        if inner is None:
            return None
        if inner.type == "generic_type":
            base = (
                inner.child_by_field_name("name")
                or inner.child_by_field_name("type")
                or (inner.named_children[0] if inner.named_children else None)
            )
            return node_text(base) if base is not None else None
        return node_text(inner)

    def _default_export_ident(self, root) -> str | None:
        """`export default <identifier>` -> that identifier (an Express router that
        routes were registered on and other files import + mount) — #36."""
        for child in root.named_children:
            if child.type != "export_statement" or not any(
                c.type == "default" for c in child.children
            ):
                continue
            value = child.child_by_field_name("value") or (
                child.named_children[-1] if child.named_children else None
            )
            if value is not None and value.type == "identifier":
                return node_text(value)
        return None

    def _commonjs_default_export(self, root) -> str | None:
        """CommonJS `module.exports = <identifier>` / `exports = <identifier>` -> that
        identifier, the module's default export (the Express router pattern, mirroring
        `export default router` for require()-based apps) — #113 QA follow-up."""
        for child in root.named_children:
            if child.type != "expression_statement" or not child.named_children:
                continue
            expr = child.named_children[0]
            if expr.type != "assignment_expression":
                continue
            left = expr.child_by_field_name("left")
            right = expr.child_by_field_name("right")
            if left is None or right is None or right.type != "identifier":
                continue
            if node_text(left) in ("module.exports", "exports"):
                return node_text(right)
        return None

    # ---------------- definitions ----------------

    def _definitions(self, root, ctx, out) -> None:
        caps = captures(load_query_for(ctx.language, "javascript", "definitions"), root)

        for node in caps.get("def.function", []):
            self._add_callable(node, ctx, out, SymbolKind.FUNCTION)
        for node in caps.get("def.method", []):
            if node.parent is not None and node.parent.type == "object":
                continue  # object-literal shorthand: emitted with a key-path qname below
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
                return_type_text=self._return_type_text(node),
            )
        )

    # ---------------- object-literal callables ----------------

    def _object_literal_callables(self, root, ctx, out) -> None:
        """Function-valued object-literal properties become METHOD symbols with
        key-path qnames: `const resolvers = { Query: { user: () => {} } }` ->
        ``mod.resolvers.Query.user``. Covers shorthand methods (`user() {}`) and
        arrow/function-expression pairs; makes Apollo-style resolver maps (and any
        handler-map idiom) addressable/bindable. Members with no stable path
        anchor (`module.exports = { run() {} }`) keep their previous flat qname.
        """
        stack = [root]
        while stack:
            n = stack.pop()
            stack.extend(n.named_children)
            if n.type == "method_definition" and n.parent is not None and n.parent.type == "object":
                name = self._key_name(n.child_by_field_name("name"))
                if name is not None:
                    self._emit_object_member(n, name, ctx, out)
            elif n.type == "pair":
                value = n.child_by_field_name("value")
                if value is None or value.type not in ("arrow_function", "function_expression"):
                    continue
                name = self._key_name(n.child_by_field_name("key"))
                if name is not None:
                    self._emit_object_member(n, name, ctx, out)

    def _emit_object_member(self, node, name, ctx, out) -> None:
        parent_path = self._object_parent_path(node, ctx)
        if parent_path is None:
            qname, parent_q = self._qualify(node, name, ctx)
        else:
            qname, parent_q = f"{parent_path}.{name}", parent_path
        out.symbols.append(
            RawSymbol(
                kind=SymbolKind.METHOD,
                name=name,
                qualified_name=qname,
                span=span_of(node),
                parent_qualified_name=parent_q,
                signature=self._signature(node),
                is_exported=self._exported(node),
                return_type_text=self._return_type_text(node),
            )
        )

    def _key_name(self, key_node) -> str | None:
        if key_node is None:
            return None
        if key_node.type == "property_identifier":
            return node_text(key_node)
        if key_node.type == "string":
            text = node_text(key_node).strip("'\"`")
            return text or None
        return None  # computed / numeric keys carry no stable name

    # An object member's path stops at 4 key segments — deeper nesting is config
    # data, not a handler map.
    _OBJECT_PATH_MAX_KEYS = 4

    def _object_parent_path(self, node, ctx) -> str | None:
        """Dotted parent path of an object-literal member: enclosing pair keys up
        to a variable/assignment anchor (`const resolvers = ...` -> resolvers).
        An object passed inline as a call argument uses its keys alone
        (`new ApolloServer({resolvers: {Query: ...}})` -> mod.resolvers.Query).
        None when there is neither anchor nor keys (flat-qname fallback).
        """
        keys: list[str] = []
        anchor: str | None = None
        current = node.parent
        while current is not None:
            t = current.type
            if t in ("object", "parenthesized_expression"):
                current = current.parent
                continue
            if t == "pair":
                key = self._key_name(current.child_by_field_name("key"))
                if key is None or len(keys) >= self._OBJECT_PATH_MAX_KEYS:
                    return None
                keys.append(key)
                current = current.parent
                continue
            if t == "variable_declarator":
                name = current.child_by_field_name("name")
                if name is not None and name.type == "identifier":
                    anchor = node_text(name)
                break
            if t == "assignment_expression":
                left = current.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    anchor = node_text(left)
                break
            break  # arguments / export_statement / return_statement / program ...
        if not keys and anchor is None:
            return None
        segments = ([anchor] if anchor else []) + list(reversed(keys))
        chain = self._scope_chain(node)
        return ".".join([ctx.module_path, *chain, *segments])

    # value-preserving generic wrappers: `Promise<T>` (awaited) and `Awaited<T>`
    # still denote a T; `Array<T>`/`Readonly<T>` do not, so they don't unwrap.
    _RETURN_TYPE_UNWRAP = frozenset({"Promise", "Awaited"})

    def _return_type_text(self, node):
        """Single resolvable type of a TS return annotation (`function f(): T`,
        `method(): T`, `(): T =>`), or None. JS has no return types, so the field
        is absent and this yields None (#132)."""
        target = node
        if node.child_by_field_name("return_type") is None:
            value = node.child_by_field_name("value")
            if value is not None:  # `const f = (): T => ...`: annotation on the arrow
                target = value
        annotation = target.child_by_field_name("return_type")
        if annotation is None:
            return None
        inner = annotation.named_children[0] if annotation.named_children else None
        return self._ts_type_name(inner) if inner is not None else None

    def _ts_type_name(self, n):
        t = n.type
        if t in ("type_identifier", "nested_type_identifier"):
            return node_text(n)
        if t == "generic_type":
            base = n.named_children[0] if n.named_children else None
            if base is None or node_text(base) not in self._RETURN_TYPE_UNWRAP:
                return None
            args = next((c for c in n.named_children if c.type == "type_arguments"), None)
            for arg in args.named_children if args is not None else ():
                name = self._ts_type_name(arg)
                if name:
                    return name
            return None
        return None  # predefined_type (number/string/void), union, etc.

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
        for node in caps.get("require", []):
            self._commonjs_require(node, ctx, out)

        for imp in out.imports:
            out.framework_signals.append(
                ("import", imp.module.split(".")[0] if "." in imp.module else imp.module)
            )

    def _commonjs_require(self, call, ctx, out) -> None:
        """CommonJS `require('m')` bindings -> RawImport, mirroring ESM handling.

        ``const cp = require('m')``          -> whole module bound to ``cp``
        ``const { x, y: z } = require('m')`` -> named imports ``x`` and ``y`` (as ``z``)
        bare / chained / reassigned          -> module-only (side-effect) import,
        which still yields the framework signal even when no alias can be bound.
        """
        args = call.child_by_field_name("arguments")
        spec = None
        if args is not None:
            for child in args.named_children:
                if child.type == "string":
                    spec = node_text(child).strip("'\"`")
                    break
        if not spec:
            return  # dynamic require (variable/template arg) — target unknowable
        module = self._resolve_module(spec, ctx)
        span = span_of(call)

        parent = call.parent
        name_node = None
        if parent is not None and parent.type == "variable_declarator":
            name_node = parent.child_by_field_name("name")
        elif parent is not None and parent.type == "assignment_expression":
            name_node = parent.child_by_field_name("left")

        if name_node is not None and name_node.type == "identifier":
            # Bind the alias to the module itself (same as an ESM default import),
            # so `const cp = require('child_process'); cp.exec()` canonicalizes to
            # child_process.exec.
            out.imports.append(
                RawImport(module=module, imported_name=None, alias=node_text(name_node), span=span)
            )
            return
        if name_node is not None and name_node.type == "object_pattern":
            for child in name_node.named_children:
                if child.type == "shorthand_property_identifier_pattern":  # { exec }
                    nm = node_text(child)
                    out.imports.append(
                        RawImport(module=module, imported_name=nm, alias=nm, span=span)
                    )
                elif child.type == "pair_pattern":  # { exec: run }
                    key = child.child_by_field_name("key")
                    value = child.child_by_field_name("value")
                    if key is None:
                        continue
                    alias = (
                        node_text(value)
                        if value is not None and value.type == "identifier"
                        else node_text(key)
                    )
                    out.imports.append(
                        RawImport(
                            module=module, imported_name=node_text(key), alias=alias, span=span
                        )
                    )
            return
        # bare `require('m')`, `require('m').foo`, `module.exports = require('m')`, etc.
        out.imports.append(
            RawImport(module=module, imported_name=None, alias=module.split(".")[-1], span=span)
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
        return resolve_relative_module(spec, ctx.module_path, ctx.is_package)

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
                    assign_target=self._assign_target(node),
                )
            )
            self._emit_callbacks(args, caller, out)

        for node in caps.get("member", []):
            self._emit_accessor_read(node, ctx, out, is_subscript=False)
        for node in caps.get("subscript", []):
            self._emit_accessor_read(node, ctx, out, is_subscript=True)

    def _emit_accessor_read(self, node, ctx, out, *, is_subscript: bool) -> None:
        """`req.body.name` / `req.query["q"]` read request input but are not calls,
        so they emit no source edge. Synthesize an accessor-read reference carrying
        the key when the shape is `<root>.<prop>` with root a request accessor and
        prop a known input accessor (#87C). Skips the call-function position so a
        real `req.body.foo()` call isn't double-counted."""
        parent = node.parent
        if (
            parent is not None
            and parent.type == "call_expression"
            and parent.child_by_field_name("function") is node
        ):
            return
        obj = node.child_by_field_name("object")
        if obj is None or obj.type != "member_expression":
            return
        root_node = obj.child_by_field_name("object")
        prop_node = obj.child_by_field_name("property")
        if root_node is None or prop_node is None:
            return
        root = node_text(root_node)
        prop = node_text(prop_node)
        if root not in ACCESSOR_ROOTS or prop not in REQUEST_ACCESSOR_PROPS:
            return
        accessor = node_text(obj)  # "req.body"
        if is_subscript:
            index = node.child_by_field_name("index")
            key = subscript_key(node_text(index)) if index is not None else None
        else:
            outer_prop = node.child_by_field_name("property")  # the `.name` in req.body.name
            key = member_key(node_text(outer_prop)) if outer_prop is not None else None
        out.references.append(
            RawReference(
                kind="call",
                callee_text=accessor,
                callee_name=prop,
                receiver_text=root,
                span=span_of(node),
                caller_qualified_name=self._caller(node, ctx),
                arg_count=1,
                arg_preview=f'("{key}")' if key else None,
            )
        )

    def _assign_target(self, call) -> str | None:
        """The binding a call (or its enclosing call-chain) is assigned to, for
        Express router composition (#36): `const api = Router().use(...)` -> "api",
        `export default Router().use('/api', api)` -> "<default>". Walks up through
        chained member/call expressions only, stopping at any statement/function
        boundary so a call inside a function body isn't mis-attributed.
        """
        current = call.parent
        while current is not None:
            t = current.type
            if t == "variable_declarator":
                name = current.child_by_field_name("name")
                return node_text(name) if name is not None and name.type == "identifier" else None
            if t == "export_statement":
                # `export default <chain>` — only the default export is a router root
                return "<default>" if any(c.type == "default" for c in current.children) else None
            if t in ("assignment_expression",):
                left = current.child_by_field_name("left")
                return node_text(left) if left is not None and left.type == "identifier" else None
            # keep climbing only through the call-chain / parenthesized wrappers
            if t not in (
                "member_expression",
                "call_expression",
                "arguments",
                "parenthesized_expression",
            ):
                return None
            current = current.parent
        return None

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
                    # object-literal shorthand: same key-path qname its symbol got,
                    # so body call edges attach to the resolver, not a flat alias.
                    if current.parent is not None and current.parent.type == "object":
                        key = self._key_name(name)
                        path = self._object_parent_path(current, ctx)
                        if key is not None and path is not None:
                            return f"{path}.{key}"
                    return self._qualify(current, node_text(name), ctx)[0]
            if current.type == "pair":
                value = current.child_by_field_name("value")
                if value is not None and value.type in ("arrow_function", "function_expression"):
                    key = self._key_name(current.child_by_field_name("key"))
                    path = self._object_parent_path(current, ctx)
                    if key is not None and path is not None:
                        return f"{path}.{key}"
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
