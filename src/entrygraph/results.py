"""Public result types.

All query results are frozen, slotted dataclasses detached from any ORM
session — safe to hold forever and free to serialize with dataclasses.asdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Symbol:
    id: int
    kind: str
    name: str
    qname: str
    file: str | None  # repo-relative path; None for external placeholders
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str | None = None
    is_exported: bool = True

    @property
    def is_external(self) -> bool:
        return self.kind == "external"


@dataclass(frozen=True, slots=True)
class Edge:
    id: int
    kind: str
    src_qname: str
    dst_qname: str
    resolved: bool
    line: int
    confidence: int
    file: str | None = None
    sink_id: str | None = None
    arg_preview: str | None = None


@dataclass(frozen=True, slots=True)
class Parameter:
    """A declared or observed input parameter of an entrypoint.

    ``location`` uses the taint source-channel vocabulary
    (path|query|body|form|header|cookie) so it matches ``PathResult.source_channel``
    directly; ``provenance`` records how it was learned
    (route|dsl|strong_params|usage)."""

    name: str
    location: str
    required: bool = True
    type_ref: str | None = None
    provenance: str = "route"
    line: int | None = None


@dataclass(frozen=True, slots=True)
class Entrypoint:
    id: int
    kind: str
    framework: str | None
    symbol: Symbol
    route: str | None = None
    http_method: str | None = None
    extra: dict = field(default_factory=dict)
    parameters: tuple[Parameter, ...] = ()


@dataclass(frozen=True, slots=True)
class FileInfo:
    id: int
    path: str
    language: str | None
    size_bytes: int
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RepoInfo:
    """One indexed repository in a (possibly multi-repo) database."""

    id: int
    root: str  # absolute root path, the repo's identity in the DB
    name: str  # trailing path segment, a friendly label
    files: int
    symbols: int
    indexed_at: str | None  # ISO-8601, or None if never completed
    stale: bool = False  # analyzer_version behind current: data valid but refreshing


@dataclass(frozen=True, slots=True)
class PathEdge:
    kind: str
    line: int
    confidence: int
    sink_id: str | None = None
    via: str | None = None  # "cha" | "dynamic" | "reexport" | None
    arg_preview: str | None = None
    constant_args: bool = False  # terminal hop: sink called with literal args only


@dataclass(frozen=True, slots=True)
class CallPath:
    """A source->sink call path. Carries displayable *facts* only — the sink's
    catalog severity, the weakest edge confidence, and the tri-state dataflow
    verdict — never a blended heuristic score; each field is checkable against
    the code the path points at."""

    symbols: tuple[Symbol, ...]  # source ... sink, in order
    edges: tuple[PathEdge, ...]  # one per hop; len == len(symbols) - 1
    severity: str | None = None  # the tagged sink's catalog severity, if any
    sink_category: str | None = None  # the tagged sink's category (command_exec, sql, ...)
    may_continue: bool = False  # a node on this path has out-edges the filter excluded
    source_channel: str | None = None  # query|path|header|cookie|body|form (#87)
    source_key: str | None = None  # the specific param/header/flag name (#87)
    source_kind: str | None = None  # explicit|spec|handler_params|handler (#96)
    # the taint source category this path was enumerated under (http_input, ...),
    # when a `source_category` query produced it
    source_category: str | None = None
    # reaching-defs verdict: True (flow observed) / False (provable non-flow) /
    # None (not checked / unknown). #96 Phase 2.
    taint_verified: bool | None = None

    @property
    def min_confidence(self) -> int:
        return min((e.confidence for e in self.edges), default=0)

    def render(self) -> str:
        parts = [self.symbols[0].qname]
        for edge, sym in zip(self.edges, self.symbols[1:]):
            parts.append(f"-> {sym.qname} (line {edge.line})")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class DetectedLanguage:
    name: str
    file_count: int
    byte_count: int
    percent: float  # by bytes, of recognized files


@dataclass(frozen=True, slots=True)
class DetectedFramework:
    name: str
    language: str
    confidence: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DetectionReport:
    languages: tuple[DetectedLanguage, ...]
    frameworks: tuple[DetectedFramework, ...]


@dataclass(frozen=True, slots=True)
class IndexStats:
    files_scanned: int
    files_indexed: int
    files_skipped: int
    files_deleted: int
    symbols: int
    edges: int
    entrypoints: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class GraphStats:
    repo_root: str
    index_generation: int
    files: int
    symbols: int
    edges: int
    resolved_edges: int
    entrypoints: int
    sink_edges: int
    source_edges: int
    stale: bool = False  # analyzer_version behind current: data valid but refreshing
