"""Cross-file Express router mount-prefix resolution (#36).

Express composes routers across files: a controller registers routes on a local
`router` and exports it; an aggregator mounts that router under a path prefix
(`app.use('/api', router)`), often through intermediate routers. The route rule
only sees the method-level path, so the prefix is lost.

This module builds the repo-wide mount graph — nodes are `(module, router-name)`,
edges are `parent mounts child at prefix` — and returns, per module, the prefix at
which each router variable's routes are reachable from an app root.

Cross-file router identity is resolved through the whole import surface (#113):
`export default router` (the default node), a **named** import/export
(`import { userRouter } from './users'` mounting an `export const userRouter`), and
re-export chains through barrel files (`export { r as userRouter } from './impl'`).
"""

from __future__ import annotations

import re

from entrygraph.detect.entrypoints.base import first_string_arg, identifier_args
from entrygraph.extract.javascript import resolve_relative_module

_USE = "use"
_DEFAULT = "<default>"  # a module's `export default` router
_JS_LANGS = frozenset({"javascript", "typescript", "tsx"})
# inline `require('./router')` used directly as a `.use()` argument (no
# intermediate variable) — the mount target is the required module (#133)
_INLINE_REQUIRE = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")

Node = tuple[str, str]  # (module_path, router-name)


def _compose(parent_prefix: str, edge_prefix: str) -> str:
    parts = [p.strip("/") for p in (parent_prefix, edge_prefix) if p and p.strip("/")]
    return "/" + "/".join(parts) if parts else ""


def resolve_mount_prefixes(extractions) -> dict[str, dict[str, str]]:
    """module_path -> {router-var: mount-prefix} for every router mounted under a path."""
    # child node -> list of (parent node, edge prefix); a node with no parent is a root
    parent_edges: dict[Node, list[tuple[Node, str]]] = {}
    nodes: set[Node] = set()

    def add_edge(child: Node, parent: Node, prefix: str) -> None:
        parent_edges.setdefault(child, []).append((parent, prefix))
        nodes.add(child)
        nodes.add(parent)

    # repo-wide named re-export map: (module, exported-as) -> (source module, source
    # name), so a named import can be chased through barrel files to the module + var
    # where routes are actually registered. Mirrors resolve_hierarchy's reexport map,
    # but computed locally because this pass runs before the symbol table is filled.
    reexports: dict[Node, Node] = {}
    for _p, x, _pkg in extractions:
        if x.language not in _JS_LANGS:
            continue
        for rx in x.reexports:
            if rx.is_star or rx.exported_name is None:
                continue
            exported_as = rx.alias or rx.exported_name
            reexports[(x.module_path, exported_as)] = (rx.module, rx.exported_name)

    def follow_reexports(node: Node) -> Node:
        """Chase a named re-export chain (barrel files) to the module + var routes
        are registered on; a node with no re-export entry is returned unchanged."""
        seen: set[Node] = set()
        while node in reexports and node not in seen:
            seen.add(node)
            node = reexports[node]
        return node

    for _p, x, is_package in extractions:
        if x.language not in _JS_LANGS:
            continue
        m = x.module_path
        # whole-module (default/namespace) imports: local alias -> source module
        whole_module = {imp.alias: imp.module for imp in x.imports if imp.imported_name is None}
        # named imports: local alias -> (source module, exported name), so a router
        # imported by name resolves to its defining module, not this one (#113)
        named = {
            imp.alias: (imp.module, imp.imported_name)
            for imp in x.imports
            if imp.imported_name is not None and imp.imported_name != "*"
        }
        # `export default router` aliases the default export to that router var
        if x.default_export is not None:
            add_edge((m, x.default_export), (m, _DEFAULT), "")
        for ref in x.references:
            if ref.kind != "call" or ref.callee_name != _USE or not ref.arg_preview:
                continue
            if ref.assign_target:  # `const api = Router().use(child)` / `export default ...`
                parent = (m, ref.assign_target)
            elif ref.receiver_text and ref.receiver_text.isidentifier():  # `app.use(child)`
                parent = (m, ref.receiver_text)
            else:
                continue  # mounting router can't be identified (bare inline chain)
            prefix = first_string_arg("(" + ref.arg_preview.lstrip("("))
            prefix = prefix if prefix and prefix.startswith("/") else ""
            idents = identifier_args(ref.arg_preview)
            if idents:
                target = idents[-1]  # the mounted router (last handler arg)
                if target in whole_module:
                    child = (whole_module[target], _DEFAULT)  # default/namespace import
                elif target in named:
                    source_module, exported = named[target]
                    child = follow_reexports((source_module, exported))
                else:
                    child = (m, target)  # a router local to this module
            else:
                # inline `app.use('/x', require('./router'))`: the require call is
                # the mount argument directly, so resolve it like a default import
                # to the required module's default export (#133)
                inline = _INLINE_REQUIRE.findall(ref.arg_preview)
                if not inline:
                    continue  # `.use('/x', serveStatic(...))` — no sub-router
                child = (resolve_relative_module(inline[-1], m, is_package), _DEFAULT)
            add_edge(child, parent, prefix)

    memo: dict[Node, str] = {}

    def accumulate(node: Node, stack: frozenset[Node]) -> str:
        if node in memo:
            return memo[node]
        if node in stack:
            return ""  # mount cycle — stop
        parents = parent_edges.get(node)
        if not parents:
            memo[node] = ""  # root (an app instance / unmounted router)
            return ""
        parent, prefix = parents[0]  # first mount wins when a router is mounted twice
        result = _compose(accumulate(parent, stack | {node}), prefix)
        memo[node] = result
        return result

    prefixes: dict[str, dict[str, str]] = {}
    for module, name in nodes:
        pref = accumulate((module, name), frozenset())
        if pref:
            prefixes.setdefault(module, {})[name] = pref
    return prefixes
