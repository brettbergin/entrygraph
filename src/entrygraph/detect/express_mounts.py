"""Cross-file Express router mount-prefix resolution (#36).

Express composes routers across files: a controller registers routes on a local
`router` and `export default`s it; an aggregator mounts that router under a path
prefix (`app.use('/api', router)`), often through intermediate routers. The route
rule only sees the method-level path, so the prefix is lost.

This module builds the repo-wide mount graph — nodes are `(module, router-name)`,
edges are `parent mounts child at prefix` — and returns, per module, the prefix at
which each router variable's routes are reachable from an app root.
"""

from __future__ import annotations

from entrygraph.detect.entrypoints.base import first_string_arg, identifier_args

_USE = "use"
_DEFAULT = "<default>"  # a module's `export default` router
_JS_LANGS = frozenset({"javascript", "typescript", "tsx"})

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

    for _p, x, _pkg in extractions:
        if x.language not in _JS_LANGS:
            continue
        m = x.module_path
        # whole-module (default/namespace) imports: local alias -> source module
        imports = {imp.alias: imp.module for imp in x.imports if imp.imported_name is None}
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
            if not idents:
                continue  # `.use('/x', serveStatic(...))` — no sub-router identifier
            target = idents[-1]  # the mounted router (last handler arg)
            child = (imports[target], _DEFAULT) if target in imports else (m, target)
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
