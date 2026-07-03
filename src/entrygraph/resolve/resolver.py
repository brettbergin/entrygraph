"""Pass-2 reference resolution: raw references -> edge rows.

Resolution order per reference (first hit wins):
1. import-map expansion (project target -> FQN lookup; external -> placeholder)
2. module-local lookup (same-module bare names)
3. self/cls receiver -> method on the enclosing class, then its bases (1 level)
4. unique-name fuzzy match across the project
5. unresolved: a language-prefixed guess so sink matching still works
   ("py:*.execute" for attribute calls, "py:eval" for bare calls)

Every call edge ends at a real symbol id — project symbol or external
placeholder — except import-expanded references into project modules whose
target doesn't exist (yet); those keep dst NULL + a project-style dst_qname so
incremental re-resolution can heal them when the target appears.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from entrygraph.extract.ir import FileExtraction, RawReference
from entrygraph.kinds import Confidence, EdgeKind, SymbolKind
from entrygraph.resolve.externals import LANG_PREFIX, ExternalRegistry
from entrygraph.resolve.hierarchy import (
    ancestors,
    build_import_map,
    cha_candidates,
    expand_relative,
)
from entrygraph.resolve.symbol_table import SymbolTable

if TYPE_CHECKING:
    from entrygraph.detect.taint import SinkRegistry

# expand_relative re-exported for backward compatibility with callers/tests.
__all__ = ["FileResolver", "ResolvedEdge", "expand_relative"]

_REF_EDGE_KIND = {
    "call": EdgeKind.CALLS,
    # An annotation/attribute/derive application (@Autowired, [HttpPost], #[derive])
    # is metadata, not an invocation; a Python decorator is technically a call but is
    # not part of source->sink data flow. Emitting these as CALLS inflated the call
    # graph (Java/C#/Rust repos: thousands of phantom edges) — keep them as a distinct
    # DECORATES edge so they stay queryable without polluting reachability (#43).
    "decorator": EdgeKind.DECORATES,
    "inherit": EdgeKind.INHERITS,
    "implement": EdgeKind.IMPLEMENTS,
    "annotation": EdgeKind.REFERENCES,
    "callback": EdgeKind.PASSED_AS_CALLBACK,
    "dynamic_call": EdgeKind.CALLS,
}

_SELF_RECEIVERS = frozenset({"self", "cls", "this", "$this"})
_MAX_REEXPORT_DEPTH = 16


@dataclass(slots=True)
class ResolvedEdge:
    kind: EdgeKind
    src_symbol_id: int
    dst_symbol_id: int | None
    dst_qname: str
    line: int
    confidence: Confidence
    arg_preview: str | None = None
    via: str | None = None  # "dynamic" | "reexport" | "cha" | None


class FileResolver:
    def __init__(
        self,
        extraction: FileExtraction,
        module_symbol_id: int,
        table: SymbolTable,
        externals: ExternalRegistry,
        is_package: bool = False,
        sink_registry: SinkRegistry | None = None,
        local_symbol_ids: dict[str, int] | None = None,
    ) -> None:
        self.x = extraction
        self.module_symbol_id = module_symbol_id
        self.table = table
        self.externals = externals
        self.is_package = is_package
        self.sink_registry = sink_registry
        # This file's own qname -> id. The caller of every reference lives in this
        # file, so an edge's source must resolve here first — resolving it through
        # the global (last-wins) table would attribute a.go's edge to a same-named
        # symbol in b.go, and wiping b.go would then cascade-delete a.go's edge.
        self.local_symbol_ids = local_symbol_ids or {}
        self.prefix = LANG_PREFIX.get(extraction.language, extraction.language)
        self.import_map, self.wildcard_modules = build_import_map(extraction, is_package)

    # ---------------- edges ----------------

    def resolve(self) -> list[ResolvedEdge]:
        edges = [*self._import_edges()]
        for ref in self.x.references:
            edge = self._resolve_reference(ref)
            if edge is None:  # callbacks to non-project data args are dropped
                continue
            edges.append(edge)
            edges.extend(self._cha_edges(ref, edge))
        return edges

    def _cha_edges(self, ref: RawReference, primary: ResolvedEdge) -> list[ResolvedEdge]:
        """Class-hierarchy virtual-dispatch candidates for an unknown-receiver call.

        Only for method calls that resolved imprecisely (FUZZY or an unresolved
        `prefix:*.name`). Each candidate is a FUZZY, via="cha" edge, so it is
        invisible under the default IMPORT confidence floor and only widens
        results when the caller opts into fuzzy traversal.
        """
        if ref.kind != "call" or ref.receiver_text is None:
            return []
        if ref.receiver_text in _SELF_RECEIVERS:
            return []
        if primary.confidence > Confidence.FUZZY:
            return []
        exclude = {primary.dst_symbol_id} if primary.dst_symbol_id is not None else set()
        out: list[ResolvedEdge] = []
        for cand in cha_candidates(
            self.table, ref.callee_name, exclude=exclude, language=self.x.language
        ):
            out.append(
                ResolvedEdge(
                    EdgeKind.CALLS,
                    primary.src_symbol_id,
                    cand,
                    self.table.qname_of[cand],
                    ref.span.start_line,
                    Confidence.FUZZY,
                    arg_preview=ref.arg_preview,
                    via="cha",
                )
            )
        return out

    def _import_edges(self) -> list[ResolvedEdge]:
        edges = []
        seen: set[str] = set()
        for imp in self.x.imports:
            module = (
                expand_relative(imp, self.x.module_path, self.is_package)
                if imp.is_relative
                else imp.module
            )
            if not module or module in seen:
                continue
            seen.add(module)
            if self.table.is_project_path(module):
                dst_id = self.table.module_symbol_ids.get(module) or self.table.by_fqn.get(module)
                edges.append(
                    ResolvedEdge(
                        EdgeKind.IMPORTS,
                        self.module_symbol_id,
                        dst_id,
                        module,
                        imp.span.start_line,
                        Confidence.IMPORT if dst_id else Confidence.UNRESOLVED,
                    )
                )
            else:
                qname = f"{self.prefix}:{module}"
                dst_id = self.externals.get_or_create(qname)
                edges.append(
                    ResolvedEdge(
                        EdgeKind.IMPORTS,
                        self.module_symbol_id,
                        dst_id,
                        qname,
                        imp.span.start_line,
                        Confidence.IMPORT,
                    )
                )
        return edges

    def _resolve_reference(self, ref: RawReference) -> ResolvedEdge | None:
        caller_q = ref.caller_qualified_name or ""
        src_id = self.local_symbol_ids.get(caller_q) or self.table.by_fqn.get(
            caller_q, self.module_symbol_id
        )
        kind = _REF_EDGE_KIND.get(ref.kind, EdgeKind.REFERENCES)

        if ref.kind == "callback":
            # A function name handed to another call. Only a real project
            # function/method is a meaningful edge; anything else is a data arg.
            dst_id = self._bind_project_callable(ref.callee_name)
            if dst_id is None:
                return None
            return ResolvedEdge(
                kind,
                src_id,
                dst_id,
                self.table.qname_of[dst_id],
                ref.span.start_line,
                Confidence.IMPORT,
            )

        if ref.kind == "dynamic_call":
            # getattr/computed/send — target isn't statically knowable. Keep a
            # real placeholder node so the path can flag "may continue".
            if ref.callee_name in ("<dynamic>", "<computed>"):
                guess = f"{self.prefix}:<dynamic>"
            else:
                guess = f"{self.prefix}:{ref.callee_name}.*"
            dst_id = self.externals.get_or_create(guess)
            return ResolvedEdge(
                kind,
                src_id,
                dst_id,
                guess,
                ref.span.start_line,
                Confidence.UNRESOLVED,
                arg_preview=ref.arg_preview,
                via="dynamic",
            )

        dst_id, dst_qname, confidence, via = self._bind(ref)
        return ResolvedEdge(
            kind=kind,
            src_symbol_id=src_id,
            dst_symbol_id=dst_id,
            dst_qname=dst_qname,
            line=ref.span.start_line,
            confidence=confidence,
            arg_preview=ref.arg_preview,
            via=via,
        )

    def _bind_project_callable(self, name: str) -> int | None:
        local = f"{self.x.module_path}.{name}"
        if local in self.table.by_fqn:
            return self.table.by_fqn[local]
        if name in self.import_map:
            target = self.import_map[name]
            if target in self.table.by_fqn:
                return self.table.by_fqn[target]
        return self.table.unique_by_name(
            name, (SymbolKind.FUNCTION, SymbolKind.METHOD), self.x.language
        )

    def _guess_is_sink(self, ref: RawReference) -> bool:
        """Whether the receiver-agnostic guess for this attribute call matches a
        registered sink — checked with the same call the index-time stamper uses."""
        if self.sink_registry is None:
            return False
        guess = f"{self.prefix}:*.{ref.callee_name}"
        return self.sink_registry.match(guess, ref.arg_preview) is not None

    def _bind(self, ref: RawReference) -> tuple[int | None, str, Confidence, str | None]:
        # A `(` in the callee text means it is a member access on a *call result*
        # (`re.search(rx).groups`, `os.popen(cmd).read`), so the dotted path is not
        # a real qname. Don't expand it through the import map or store it verbatim
        # as a guess — that is what produced multi-hundred-char garbage dst_qnames.
        chained = "(" in ref.callee_text

        # 1. import-map expansion (chase re-export chains for project targets)
        first_seg = ref.callee_text.split(".", 1)[0].split("(", 1)[0]
        if not chained and first_seg in self.import_map:
            rest = ref.callee_text[len(first_seg) :]
            expanded = self.import_map[first_seg] + rest
            if self.table.is_project_path(expanded):
                dst_id = self.table.by_fqn.get(expanded)
                if dst_id is not None:
                    return dst_id, expanded, Confidence.IMPORT, None
                chased = self._chase_reexport(expanded)
                if chased is not None:
                    return self.table.by_fqn[chased], chased, Confidence.IMPORT, "reexport"
                # project target: missing -> healable NULL edge
                return None, expanded, Confidence.UNRESOLVED, None
            qname = f"{self.prefix}:{expanded}"
            return self.externals.get_or_create(qname), qname, Confidence.IMPORT, None

        # 2. module-local bare name
        if ref.receiver_text is None:
            local = f"{self.x.module_path}.{ref.callee_name}"
            dst_id = self.table.by_fqn.get(local)
            if dst_id is not None:
                return dst_id, local, Confidence.EXACT, None

        # 3. self/cls/this receiver -> method on the enclosing class, then up the
        #    full ancestor chain (transitive, cycle-safe; see hierarchy.py).
        if ref.receiver_text in _SELF_RECEIVERS and ref.caller_qualified_name:
            class_fqn = ref.caller_qualified_name.rsplit(".", 1)[0]
            if self.table.kinds.get(self.table.by_fqn.get(class_fqn, -1)) == SymbolKind.CLASS:
                candidate = f"{class_fqn}.{ref.callee_name}"
                dst_id = self.table.by_fqn.get(candidate)
                if dst_id is not None:
                    return dst_id, candidate, Confidence.EXACT, None
                for base_fqn in ancestors(class_fqn, self.table):
                    candidate = f"{base_fqn}.{ref.callee_name}"
                    dst_id = self.table.by_fqn.get(candidate)
                    if dst_id is not None:
                        return dst_id, candidate, Confidence.EXACT, None

        # 3b. bare name via a wildcard import (`from mod import *`)
        if ref.receiver_text is None:
            for wmod in self.wildcard_modules:
                candidate = f"{wmod}.{ref.callee_name}"
                dst_id = self.table.by_fqn.get(candidate)
                if dst_id is not None:
                    return dst_id, candidate, Confidence.IMPORT, None

        # 4. unique-name fuzzy match (project symbols only, never ambiguous).
        # For attribute calls with an unknown receiver (e.g. `runner.execute()`
        # where runner is a local variable) this is the only way to recover the
        # call-graph edge without local type inference — it's the documented
        # FUZZY tradeoff.
        #
        # But if the receiver-agnostic guess (`py:*.execute`) is itself a
        # registered sink, do NOT fuzzy-bind: rewriting dst_qname to a unique
        # project method named `execute` would erase the sink stamp and hide the
        # vulnerability. Prefer the guess so the sink survives (step 5); CHA still
        # adds the project method as a via="cha" candidate, so no edge is lost.
        if not (ref.receiver_text is not None and self._guess_is_sink(ref)):
            kinds = (
                (SymbolKind.METHOD,)
                if ref.receiver_text is not None
                else (SymbolKind.FUNCTION, SymbolKind.CLASS)
            )
            dst_id = self.table.unique_by_name(ref.callee_name, kinds, self.x.language)
            if dst_id is not None:
                return dst_id, self.table.qname_of[dst_id], Confidence.FUZZY, None

        # 5. unresolved: language-prefixed guess, still a real node for sinks.
        # A bare name keeps its dotted text; a receiver call or a chained
        # call-result member collapses to `*.name` so args never leak into qnames.
        if ref.receiver_text is not None or chained:
            guess = f"{self.prefix}:*.{ref.callee_name}"
        else:
            guess = f"{self.prefix}:{ref.callee_text}"
        return self.externals.get_or_create(guess), guess, Confidence.UNRESOLVED, None

    def _chase_reexport(self, qname: str) -> str | None:
        """Follow barrel-file re-export chains to a terminal project symbol."""
        module, _, name = qname.rpartition(".")
        seen: set[tuple[str, str]] = set()
        for _ in range(_MAX_REEXPORT_DEPTH):
            if not module or (module, name) in seen:
                return None
            seen.add((module, name))
            mapping = self.table.reexports.get(module, {})
            if name in mapping:
                tgt_module, tgt_name = mapping[name]
                candidate = f"{tgt_module}.{tgt_name}"
                if candidate in self.table.by_fqn:
                    return candidate
                module, name = tgt_module, tgt_name
                continue
            for src_module in self.table.star_reexports.get(module, []):
                candidate = f"{src_module}.{name}"
                if candidate in self.table.by_fqn:
                    return candidate
            return None
        return None
