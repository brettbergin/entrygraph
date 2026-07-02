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
class Entrypoint:
    id: int
    kind: str
    framework: str | None
    symbol: Symbol
    route: str | None = None
    http_method: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FileInfo:
    id: int
    path: str
    language: str | None
    size_bytes: int
    symbol_count: int = 0
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PathEdge:
    kind: str
    line: int
    confidence: int
    sink_id: str | None = None
    via: str | None = None  # "cha" | "dynamic" | "reexport" | None
    arg_preview: str | None = None
    constant_args: bool = False  # terminal hop: sink called with literal args only
    sanitized_by: tuple[str, ...] = ()  # sanitizer ids matched on/around this hop


@dataclass(frozen=True, slots=True)
class CallPath:
    symbols: tuple[Symbol, ...]  # source ... sink, in order
    edges: tuple[PathEdge, ...]  # one per hop; len == len(symbols) - 1
    risk_score: float | None = None  # query-time heuristic, higher = riskier
    may_continue: bool = False  # a node on this path has out-edges the filter excluded

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
