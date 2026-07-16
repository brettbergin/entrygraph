"""In-memory symbol index for pass-2 reference resolution."""

from __future__ import annotations

from collections import defaultdict

from entrygraph.kinds import SymbolKind


class SymbolTable:
    def __init__(self) -> None:
        self.by_fqn: dict[str, int] = {}
        self.qname_of: dict[int, str] = {}
        self.by_name: dict[str, list[int]] = defaultdict(list)
        self.kinds: dict[int, SymbolKind] = {}
        self.lang: dict[int, str] = {}  # symbol id -> source language (for fuzzy scoping)
        self.project_modules: set[str] = set()
        self.module_symbol_ids: dict[str, int] = {}  # module_path -> module symbol id
        # class fqn -> resolved parent FQNs (project or external), for the
        # transitive ancestor walk and class-hierarchy analysis (see hierarchy.py).
        self.class_parents: dict[str, list[str]] = {}
        # re-export chains (barrel files): module -> {exported_name: (target_module, target_name)}
        self.reexports: dict[str, dict[str, tuple[str, str]]] = {}
        self.star_reexports: dict[str, list[str]] = {}  # module -> [source modules]
        # --- binding table (#98): syntactic name->type resolution ---
        self.field_types: dict[str, str] = {}  # "Owner.field" fqn -> type qname
        self.module_bindings: dict[str, dict[str, str]] = {}  # module -> {var -> type qname}
        self.return_types: dict[str, str] = {}  # function fqn -> return type qname (#98 P3)
        self.children_by_qname: dict[str, list[int]] = defaultdict(list)  # parent -> child ids

    def add_symbol(
        self,
        symbol_id: int,
        qname: str,
        name: str,
        kind: SymbolKind,
        language: str | None = None,
        parent_qname: str | None = None,
    ) -> None:
        self.by_fqn[qname] = symbol_id
        self.qname_of[symbol_id] = qname
        self.by_name[name].append(symbol_id)
        self.kinds[symbol_id] = kind
        if language is not None:
            self.lang[symbol_id] = language
        if parent_qname is not None:
            self.children_by_qname[parent_qname].append(symbol_id)

    def add_module(self, module_path: str, symbol_id: int, language: str | None = None) -> None:
        self.project_modules.add(module_path)
        self.module_symbol_ids[module_path] = symbol_id
        self.add_symbol(
            symbol_id, module_path, module_path.rsplit(".", 1)[-1], SymbolKind.MODULE, language
        )

    def is_project_path(self, dotted: str) -> bool:
        """True if a dotted path starts with any project module (prefix match)."""
        if dotted in self.project_modules:
            return True
        parts = dotted.split(".")
        return any(".".join(parts[:i]) in self.project_modules for i in range(1, len(parts)))

    def unique_by_name(
        self,
        name: str,
        kinds: tuple[SymbolKind, ...] | None = None,
        language: str | None = None,
    ) -> int | None:
        candidates = self.by_name.get(name, [])
        if kinds is not None:
            candidates = [c for c in candidates if self.kinds.get(c) in kinds]
        # Fuzzy (unique-name) resolution must not cross languages: a Ruby `render`
        # call should never bind to a JS `render` in a polyglot repo. When the
        # caller's language is known, restrict candidates to same-language symbols
        # (symbols with no recorded language stay eligible, for backward safety).
        if language is not None:
            same = [c for c in candidates if self.lang.get(c, language) == language]
            candidates = same
        return candidates[0] if len(candidates) == 1 else None
