"""Class-hierarchy analysis and shared import-map construction.

Runs as an index-time pre-pass (`resolve_hierarchy`) after all symbols are in
the table but before edge resolution: it expands each class's raw base
expressions **in the defining file's own import context** and records the
resolved parents, so `ancestors()` can walk the full chain and `cha_candidates()`
can enumerate virtual-dispatch targets.

Kept import-free of resolver.py to avoid a cycle; resolver imports from here.
"""

from __future__ import annotations

from entrygraph.extract.ir import FileExtraction, RawImport
from entrygraph.kinds import SymbolKind
from entrygraph.resolve.symbol_table import SymbolTable

_MAX_ANCESTOR_DEPTH = 10
_MAX_CHA_CANDIDATES = 8  # max virtual-dispatch edges emitted per unknown-receiver call
_MAX_CHA_SCAN = 64  # raw same-name method count above which CHA is skipped entirely


def expand_relative(imp: RawImport, module_path: str, is_package: bool) -> str:
    """Resolve a Python-style relative import against the importing module."""
    parts = module_path.split(".")
    if not is_package:
        parts = parts[:-1]  # level 1 = the containing package
    drop = imp.relative_level - 1
    if drop >= len(parts):
        return imp.module  # over-deep relative import; keep as written
    if drop:
        parts = parts[:-drop]
    base = ".".join(parts)
    return f"{base}.{imp.module}" if imp.module else base


def build_import_map(
    extraction: FileExtraction, is_package: bool
) -> tuple[dict[str, str], list[str]]:
    """Return (alias -> dotted target, wildcard source modules).

    Wildcard (`from x import *`) modules are returned separately so the resolver
    can try `<module>.<name>` for otherwise-unresolved bare names.
    """
    import_map: dict[str, str] = {}
    wildcard_modules: list[str] = []
    for imp in extraction.imports:
        module = (
            expand_relative(imp, extraction.module_path, is_package)
            if imp.is_relative
            else imp.module
        )
        if imp.alias == "*" or imp.imported_name == "*":
            if module:
                wildcard_modules.append(module)
            continue
        if imp.imported_name is None:
            top = module.split(".")[0]
            import_map[imp.alias] = module if imp.alias != top else top
        else:
            import_map[imp.alias] = f"{module}.{imp.imported_name}" if module else imp.imported_name
    return import_map, wildcard_modules


def resolve_hierarchy(
    extractions: list[tuple[str, FileExtraction, bool]], table: SymbolTable
) -> None:
    """Populate table.class_parents and table.reexports from freshly extracted files."""
    for _path, x, is_package in extractions:
        import_map, _wild = build_import_map(x, is_package)
        for raw in x.symbols:
            if raw.kind is not SymbolKind.CLASS or not raw.bases:
                continue
            parents: list[str] = []
            for base in raw.bases:
                resolved = _resolve_type_name(base, import_map, x.module_path, table)
                if resolved is not None:
                    parents.append(resolved)
            if parents:
                table.class_parents[raw.qualified_name] = parents
        for rx in x.reexports:
            if rx.is_star:
                table.star_reexports.setdefault(x.module_path, []).append(rx.module)
            elif rx.exported_name is not None:
                local_name = rx.alias or rx.exported_name
                table.reexports.setdefault(x.module_path, {})[local_name] = (
                    rx.module,
                    rx.exported_name,
                )


def _resolve_type_name(
    text: str, import_map: dict[str, str], module_path: str, table: SymbolTable
) -> str | None:
    """Resolve a base/interface expression to a project FQN, or None if external."""
    first_seg = text.split(".", 1)[0]
    if first_seg in import_map:
        expanded = import_map[first_seg] + text[len(first_seg) :]
        return expanded if expanded in table.by_fqn else None
    local = f"{module_path}.{text}"
    if local in table.by_fqn:
        return local
    return text if text in table.by_fqn else None


def ancestors(
    class_fqn: str, table: SymbolTable, max_depth: int = _MAX_ANCESTOR_DEPTH
) -> list[str]:
    """Transitive project ancestors of a class, nearest first, cycle-safe."""
    seen: set[str] = set()
    order: list[str] = []
    frontier = [(p, 1) for p in table.class_parents.get(class_fqn, [])]
    while frontier:
        parent, depth = frontier.pop(0)
        if parent in seen or depth > max_depth:
            continue
        seen.add(parent)
        order.append(parent)
        for grand in table.class_parents.get(parent, []):
            if grand not in seen:
                frontier.append((grand, depth + 1))
    return order


def cha_candidates(
    table: SymbolTable, method_name: str, exclude: set[int] | None = None
) -> list[int]:
    """Symbol ids of all method overrides named `method_name` that share a hierarchy.

    Class-hierarchy analysis for virtual dispatch on an unknown receiver: only
    fires when >=2 classes defining the method are related by inheritance. The
    hierarchy filter runs first, then the result is capped at _MAX_CHA_CANDIDATES,
    so a small related group is kept even when many unrelated methods share the
    name (run/get/...); a pathological raw count skips the scan entirely.
    """
    candidates = [
        sid
        for sid in table.by_name.get(method_name, [])
        if table.kinds.get(sid) is SymbolKind.METHOD
    ]
    if exclude:
        candidates = [c for c in candidates if c not in exclude]
    # A pathological same-name count (get/set/run defined hundreds of times) can't
    # yield a useful dispatch set; skip before the O(n^2) closure scan. This is a
    # generous scan bound, NOT the result cap — the real cap is applied to the
    # hierarchy-related subset below so a small related group isn't zeroed just
    # because many unrelated methods share the name.
    if len(candidates) < 2 or len(candidates) > _MAX_CHA_SCAN:
        return []
    # Keep methods whose owning classes are connected in the hierarchy — either
    # one is an ancestor of the other, or they share a common ancestor (siblings
    # implementing the same interface/base). Both cases mean a virtual call could
    # dispatch across them.
    closures = {
        sid: {
            table.qname_of[sid].rsplit(".", 1)[0],
            *ancestors(table.qname_of[sid].rsplit(".", 1)[0], table),
        }
        for sid in candidates
    }
    related: list[int] = []
    for sid, closure in closures.items():
        if any(oid != sid and closure & other for oid, other in closures.items()):
            related.append(sid)
    if len(related) < 2:
        return []
    # Cap the fan-out of the *related* set (truncate deterministically; don't zero).
    if len(related) > _MAX_CHA_CANDIDATES:
        related = sorted(related)[:_MAX_CHA_CANDIDATES]
    return related
