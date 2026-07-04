"""Syntactic name->type binding resolution (#98 Phase 1).

Extractors emit :class:`RawBinding` at construction/declaration sites with a
*written* type text. This module resolves those texts to qnames against each
file's import map — the same two-phase split the class hierarchy already uses —
and fills the :class:`SymbolTable` binding maps plus a per-symbol ``type_ref``
for persistence.

Nothing consumes the maps yet in Phase 1 (no behavior change); the generic
idiom resolvers in Phase 2 query them.
"""

from __future__ import annotations

from entrygraph.extract.ir import FileExtraction
from entrygraph.kinds import SymbolKind
from entrygraph.resolve.hierarchy import build_import_map
from entrygraph.resolve.symbol_table import SymbolTable

# languages where a type in the same package/namespace is referenced without an
# import (so a bare name may resolve one module level up)
_SAME_PACKAGE_LANGS = frozenset({"java", "csharp", "go", "php"})

# language name -> canonical callee prefix, for external (non-project) type qnames
_LANG_PREFIX = {
    "python": "py",
    "javascript": "js",
    "typescript": "js",
    "tsx": "js",
    "go": "go",
    "java": "java",
    "ruby": "rb",
    "csharp": "cs",
    "php": "php",
    "rust": "rs",
}


def _resolve_type(
    type_text: str,
    import_map: dict[str, str],
    module_path: str,
    language: str,
    table: SymbolTable,
    wildcard_modules: list[str] | None = None,
) -> str | None:
    """Resolve a written type to a qname: a project FQN if known, else a
    language-prefixed external qname (``go:database/sql.DB``) so existing
    wildcard sink globs keep matching. None for an unusable text."""
    text = type_text.strip().lstrip("*&").strip()
    if not text:
        return None
    first_seg = text.split(".", 1)[0].split("::", 1)[0]
    dotted = text.replace("::", ".")
    # 1. imported name -> its module target
    if first_seg in import_map:
        expanded = import_map[first_seg] + dotted[len(first_seg) :]
        if expanded in table.by_fqn:
            return expanded
        # imported but not a project symbol -> external, keep the resolved dotted
        return f"{_LANG_PREFIX.get(language, language)}:{expanded}"
    # 2. same-module type
    local = f"{module_path}.{dotted}"
    if local in table.by_fqn:
        return local
    # 2b. same-package type (Java/C#/Go/PHP have implicit same-package visibility):
    # a class in `com.example` is referenced bare from `com.example.Other`.
    if language in _SAME_PACKAGE_LANGS and "." in module_path:
        package = module_path.rsplit(".", 1)[0]
        sibling = f"{package}.{dotted}"
        if sibling in table.by_fqn:
            return sibling
    # 2c. type imported via a whole-namespace/package import (`using X.Y;`,
    # `from x import *`): try each wildcard module as a prefix.
    for wmod in wildcard_modules or ():
        candidate = f"{wmod}.{dotted}"
        if candidate in table.by_fqn:
            return candidate
    # 3. already a project FQN
    if dotted in table.by_fqn:
        return dotted
    # 4. external, unqualified -> language-prefixed
    return f"{_LANG_PREFIX.get(language, language)}:{dotted}"


class FileBindingView:
    """Per-file query surface over the resolved binding table.

    Built for one file's extraction; resolves a variable/field name to its type
    qname within a scope, and a call receiver to its type. Phase 2 consumers use
    this to type gRPC impls, router vars, and sink receivers.
    """

    def __init__(
        self, extraction: FileExtraction, table: SymbolTable, is_package: bool = False
    ) -> None:
        self._table = table
        self._module = extraction.module_path
        self._lang = extraction.language
        self._import_map, wildcards = build_import_map(extraction, is_package)
        # scope -> {name -> type qname}; None scope = module/class level
        self._scoped: dict[str | None, dict[str, str]] = {}
        for b in extraction.bindings:
            resolved = _resolve_type(
                b.type_text, self._import_map, self._module, self._lang, table, wildcards
            )
            if resolved is None:
                continue
            self._scoped.setdefault(b.scope, {})[b.name] = resolved

    def type_of(self, name: str, scope: str | None = None) -> str | None:
        """Type qname bound to ``name`` in ``scope``, falling back to module level."""
        scoped = self._scoped.get(scope)
        if scoped and name in scoped:
            return scoped[name]
        module_level = self._scoped.get(None)
        if module_level and name in module_level:
            return module_level[name]
        # a declared field of the enclosing type: "Owner.name"
        if scope:
            owner = scope.rsplit(".", 1)[0]
            return self._table.field_types.get(f"{owner}.{name}")
        return None

    def receiver_type(self, receiver: str, caller_fqn: str | None) -> str | None:
        """Type qname of a call receiver identifier within the calling function."""
        return self.type_of(receiver, caller_fqn)


def resolve_bindings(
    extractions: list[tuple[str, FileExtraction, bool]], table: SymbolTable
) -> dict[int, str]:
    """Scanner pre-pass: resolve every file's bindings against the symbol table,
    fill the table's binding maps, and return {symbol_id: type_ref} to persist.

    Runs after ``resolve_hierarchy`` (symbols + class parents present)."""
    type_refs: dict[int, str] = {}
    # index declared field/property + module-level variable symbols by fqn/module
    field_symbol_ids: dict[str, int] = {}
    for _path, x, _pkg in extractions:
        for raw in x.symbols:
            if raw.kind in (SymbolKind.FIELD, SymbolKind.PROPERTY):
                sid = table.by_fqn.get(raw.qualified_name)
                if sid is not None:
                    field_symbol_ids[raw.qualified_name] = sid

    for _path, x, is_package in extractions:
        import_map, wildcards = build_import_map(x, is_package)
        module_map = table.module_bindings.setdefault(x.module_path, {})
        for b in x.bindings:
            resolved = _resolve_type(
                b.type_text, import_map, x.module_path, x.language, table, wildcards
            )
            if resolved is None:
                continue
            if b.kind == "field":
                # b.name is the field's full FQN ("pkg.App.Ingester")
                table.field_types[b.name] = resolved
                sid = field_symbol_ids.get(b.name) or table.by_fqn.get(b.name)
                if sid is not None:
                    type_refs[sid] = resolved
            elif b.scope is None:
                # module-level variable/constant binding (b.name unqualified)
                module_map[b.name] = resolved
                sid = table.by_fqn.get(f"{x.module_path}.{b.name}")
                if sid is not None:
                    type_refs[sid] = resolved
    return type_refs
