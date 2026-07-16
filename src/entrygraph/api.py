"""CodeGraph — the public facade.

Every method opens a short-lived ORM Session internally and returns detached,
frozen dataclasses; the Session never escapes (except via the explicit
``session()`` escape hatch).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from entrygraph.db import models
from entrygraph.db import queries as q
from entrygraph.db.engine import make_engine, make_session_factory
from entrygraph.db.meta import check_schema
from entrygraph.errors import (
    DatabaseNotFoundError,
    RepositoryNotIndexedError,
    SymbolNotFoundError,
    UnknownCategoryError,
)
from entrygraph.graph.adjacency import AdjacencyCache
from entrygraph.kinds import Confidence, EntrypointKind
from entrygraph.results import (
    CallPath,
    DetectedFramework,
    DetectedLanguage,
    DetectionReport,
    Edge,
    Entrypoint,
    FileInfo,
    GraphStats,
    IndexStats,
    PathEdge,
    Symbol,
)

DEFAULT_DB_NAME = ".entrygraph.db"

# Entrypoint kinds whose handler symbol is implicitly a source of a taint
# category: the handler receives that category's attacker-controlled input as
# parameters/properties, so no catalog accessor call is required (#86).
# MAIN rides cli_arg because argv enters a program at main (Java
# `main(String[] args)`, Go `os.Args` in main, ...).
_HANDLER_SOURCE_KINDS: dict[str, tuple[EntrypointKind, ...]] = {
    "http_input": (EntrypointKind.HTTP_ROUTE,),
    "cli_arg": (EntrypointKind.CLI_COMMAND, EntrypointKind.MAIN),
}

type SourceSpec = str | Symbol | Entrypoint | list[str | Symbol | Entrypoint]

# Source-provenance labels (#96 Phase 1): a demonstrable accessor read (explicit)
# or a user-named source (spec) is stronger evidence than handler-as-source.
_SOURCE_KIND_EXPLICIT = "explicit"
_SOURCE_KIND_SPEC = "spec"
_SOURCE_KIND_HANDLER_PARAMS = "handler_params"
_SOURCE_KIND_HANDLER = "handler"


@dataclass(frozen=True, slots=True)
class SourceSets:
    """Taint-origin symbol ids split by provenance strength (#96 Phase 1)."""

    explicit: frozenset[int]  # calls a catalog accessor — demonstrable read
    implicit: frozenset[int]  # handler-as-source — shaped like a source
    spec: frozenset[int]  # user named it via `source`

    @property
    def all(self) -> frozenset[int]:
        return self.explicit | self.implicit | self.spec


def _traversal_params(
    min_confidence: int | None, include_fuzzy: bool, include_unresolved: bool
) -> tuple[int, bool]:
    """Derive (confidence floor, include_cha) for traversal.

    Default floor is FUZZY: EXACT/IMPORT/unique-name-FUZZY edges are traversed,
    which keeps ordinary method dispatch on a local variable reachable. The
    speculative class-hierarchy fan-out (via="cha") stays hidden until
    `include_fuzzy=True`. `include_unresolved=True` lowers the floor to 0, which
    admits UNRESOLVED wildcard-sink guesses (`py:*.execute`) and dynamic-call
    placeholders. An explicit `min_confidence` int overrides the floor.
    """
    if min_confidence is not None:
        floor = min_confidence
    elif include_unresolved:
        floor = int(Confidence.UNRESOLVED)
    else:
        floor = int(Confidence.FUZZY)
    return floor, include_fuzzy


@dataclass(frozen=True)
class _FilteredAdjacency:
    """Binds a shared AdjacencyCache to one query's confidence/CHA filter, so the
    memory engine matches the CteEngine's ``paths``/``reachable`` interface while
    every combination reuses the same cache."""

    cache: AdjacencyCache
    min_confidence: int
    include_cha: bool

    def paths(
        self, sources, sinks, max_depth=25, max_paths=10, sink_edge_ids=None, open_sink_ids=None
    ):
        return self.cache.paths(
            sources,
            sinks,
            max_depth,
            max_paths,
            self.min_confidence,
            self.include_cha,
            sink_edge_ids=sink_edge_ids,
            open_sink_ids=open_sink_ids,
        )

    def reachable(self, sources, sinks, max_depth, sink_edge_ids=None, open_sink_ids=None):
        return self.cache.reachable(
            sources,
            sinks,
            max_depth,
            self.min_confidence,
            self.include_cha,
            sink_edge_ids=sink_edge_ids,
            open_sink_ids=open_sink_ids,
        )


class PathResults(list):
    """CallPath list that also reports whether enumeration was budget-truncated.

    It is a plain list for every existing use (iteration, len, indexing); the
    `truncated` flag lets the CLI warn when 0 paths may mean "budget spent", not
    "no reachable sink". `mode` records how the adaptive search produced the result
    ("precise" | "widened" | "strict" | "explicit"), so the CLI can say when it
    fell back to the lower-confidence frontier.
    """

    truncated: bool = False
    mode: str | None = None


# Path ranking is by displayed facts, not a blended score: confirmed flows
# first, then unchecked, then refuted; higher catalog severity, then stronger
# weakest-edge confidence, then shorter paths.
_VERIFIED_RANK = {True: 0, None: 1, False: 2}
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _path_rank(p: CallPath) -> tuple:
    return (
        _VERIFIED_RANK[p.taint_verified],
        _SEVERITY_RANK.get(p.severity or "", 4),
        -p.min_confidence,
        len(p.symbols),
        [s.id for s in p.symbols],
    )


# A literal-only argument preview: strings, numbers, bools, None, kwarg names,
# and bracketed literal collections. Anything with an identifier/operator that
# could carry a variable makes it non-constant.
_CONST_TOKEN = re.compile(
    r"""^(
        \s | , | = | \( | \) | \[ | \] | \{ | \} | : |
        '[^']*' | "[^"]*" | `(?:[^`$]|\$(?!\{))*` |   # backtick: no ${...} interpolation
        \d[\d_.eExXaAbBcCdDfF]* |
        True|False|None|null|true|false|nil |
        [A-Za-z_]\w*\s*=          # kwarg name before '='
    )*$""",
    re.VERBOSE,
)


def is_constant_args(arg_preview: str | None) -> bool:
    """True if the sink was called with only literal/constant arguments.

    Conservative: an empty/None preview is constant (no args); a preview that was
    truncated at the 80-char cap (trailing ellipsis) returns False because we
    can't see the whole argument list.
    """
    if not arg_preview:
        return True
    text = arg_preview.strip()
    if text.endswith("…") or text.endswith("..."):
        return False
    inner = text
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    if not inner.strip():
        return True
    return bool(_CONST_TOKEN.match(text))


def _lookup_repo_id(engine: Engine, root: Path | None = None) -> int:
    """Resolve which repository in a (possibly global multi-repo) DB to bind to.

    With an explicit ``root``, match its Repository row. Without one, use the sole
    repository if the DB holds exactly one (keeps single-repo callers simple);
    otherwise the choice is ambiguous and must be made explicit. (#116)"""
    with Session(engine) as session:
        if root is not None:
            rid = session.execute(
                select(models.Repository.id).where(models.Repository.root_path == str(root))
            ).scalar()
            if rid is None:
                raise RepositoryNotIndexedError(f"no indexed repository at {root}")
            return rid
        ids = (
            session.execute(select(models.Repository.id).order_by(models.Repository.id))
            .scalars()
            .all()
        )
        if len(ids) == 1:
            return ids[0]
        if not ids:
            raise RepositoryNotIndexedError("database has no indexed repository")
        raise RepositoryNotIndexedError(
            "database holds multiple repositories; select one with a repo root"
        )


class CodeGraph:
    def __init__(self, engine: Engine, repo_id: int | None = None) -> None:
        self._engine = engine
        # every read is scoped to this repo; omit repo_id to bind the sole repo of a
        # single-repo DB (the common case), else pass one explicitly (#116)
        self._repo_id = repo_id if repo_id is not None else _lookup_repo_id(engine)
        self._session_factory = make_session_factory(engine)
        self._adjacency: dict[tuple[frozenset[str], int], AdjacencyCache] = {}
        self._last_index_stats: IndexStats | None = None
        self._registry_cache: tuple[tuple, object] | None = None

    @property
    def repo_id(self) -> int:
        """The active repository this graph is bound to (its row id in the DB)."""
        return self._repo_id

    @property
    def repo_root(self) -> str | None:
        """The indexed repository's root path on disk, or None if unavailable — lets
        callers read original source lines for a symbol/edge location."""
        with self._session_factory() as session:
            return session.execute(
                select(models.Repository.root_path).where(models.Repository.id == self._repo_id)
            ).scalar()

    # ---------------- construction ----------------

    @classmethod
    def index(
        cls,
        root: str | Path,
        db: str | Path | None = None,
        *,
        include_tests: bool = False,
    ) -> CodeGraph:
        """Index (or fully re-index) a repository and return an open graph.

        Test files are recorded but not extracted unless ``include_tests`` is
        set; flipping the flag on an existing index requires a full re-index.
        """
        from entrygraph.pipeline.scanner import index_repository

        root = Path(root).resolve()
        db_path = Path(db) if db else root / DEFAULT_DB_NAME
        engine = make_engine(db_path)
        stats = index_repository(root, engine, include_tests=include_tests)
        graph = cls(engine, _lookup_repo_id(engine, root))
        graph._last_index_stats = stats
        return graph

    @classmethod
    def open(cls, db: str | Path, *, root: str | Path | None = None) -> CodeGraph:
        """Open an index. In a global multi-repo DB, ``root`` selects which repo to
        query; it may be omitted only when the DB holds a single repository."""
        db_path = Path(db)
        if not db_path.exists():
            raise DatabaseNotFoundError(f"no index database at {db_path}")
        engine = make_engine(db_path)
        check_schema(engine)
        return cls(engine, _lookup_repo_id(engine, Path(root).resolve() if root else None))

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> CodeGraph:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------- symbols / files ----------------

    def symbols(
        self,
        *,
        kind: str | None = None,
        name: str | None = None,
        qname: str | None = None,
        file: str | None = None,
        include_external: bool = False,
        limit: int | None = None,
        offset: int | None = None,
        after: tuple[str, int] | None = None,
    ) -> list[Symbol]:
        with self._session_factory() as session:
            return q.select_symbols(
                session,
                self._repo_id,
                kind=kind,
                name=name,
                qname=qname,
                file=file,
                include_external=include_external,
                limit=limit,
                offset=offset,
                after=after,
            )

    def symbol(self, qname: str) -> Symbol:
        matches = self.symbols(qname=qname, include_external=True, limit=2)
        exact = [s for s in matches if s.qname == qname]
        if not exact:
            raise SymbolNotFoundError(f"no symbol with qname {qname!r}")
        return exact[0]

    def iter_symbols(self, *, batch_size: int = 1000, **filters) -> Iterator[Symbol]:
        # keyset pagination: each batch resumes after the last (qname, id), so a
        # full iteration is O(N), not O(N^2) as OFFSET would be.
        after: tuple[str, int] | None = None
        while True:
            batch = self.symbols(**filters, limit=batch_size, after=after)
            yield from batch
            if len(batch) < batch_size:
                return
            last = batch[-1]
            after = (last.qname, last.id)

    def files(self, *, language: str | None = None, path: str | None = None) -> list[FileInfo]:
        with self._session_factory() as session:
            return q.select_files(session, self._repo_id, language=language, path=path)

    # ---------------- detection ----------------

    def detect(self) -> DetectionReport:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(models.Detection)
                    .where(models.Detection.repo_id == self._repo_id)
                    .order_by(models.Detection.confidence.desc())
                )
                .scalars()
                .all()
            )
        languages = []
        frameworks = []
        for row in rows:
            evidence = json.loads(row.evidence) if row.evidence else {}
            if row.category == "language":
                languages.append(
                    DetectedLanguage(
                        name=row.name,
                        file_count=evidence.get("files", 0),
                        byte_count=evidence.get("bytes", 0),
                        percent=evidence.get("percent", 0.0),
                    )
                )
            else:
                frameworks.append(
                    DetectedFramework(
                        name=row.name,
                        language=evidence.get("language", ""),
                        confidence=row.confidence,
                        evidence=tuple(evidence.get("signals", [])),
                    )
                )
        languages.sort(key=lambda lang: lang.byte_count, reverse=True)
        return DetectionReport(languages=tuple(languages), frameworks=tuple(frameworks))

    # ---------------- entrypoints ----------------

    def entrypoints(
        self,
        *,
        kind: str | None = None,
        framework: str | None = None,
        route: str | None = None,
        limit: int | None = None,
    ) -> list[Entrypoint]:
        with self._session_factory() as session:
            return q.select_entrypoints(
                session, self._repo_id, kind=kind, framework=framework, route=route, limit=limit
            )

    # ---------------- traversal ----------------

    def callers(
        self,
        target: SourceSpec,
        *,
        depth: int = 1,
        edge_kinds: tuple[str, ...] = ("calls",),
        include_speculative: bool = False,
    ) -> list[Symbol]:
        return self._neighbors(target, depth, "in", edge_kinds, include_speculative)

    def callees(
        self,
        target: SourceSpec,
        *,
        depth: int = 1,
        edge_kinds: tuple[str, ...] = ("calls",),
        include_speculative: bool = False,
    ) -> list[Symbol]:
        return self._neighbors(target, depth, "out", edge_kinds, include_speculative)

    def references(self, target: SourceSpec) -> list[Edge]:
        """All inbound edges (any kind) to the matching symbols."""
        with self._session_factory() as session:
            ids = self._spec_to_ids(session, target)
            if not ids:
                return []
            rows = (
                session.execute(
                    select(models.Edge).where(
                        models.Edge.repo_id == self._repo_id, models.Edge.dst_symbol_id.in_(ids)
                    )
                )
                .scalars()
                .all()
            )
            src_map = q.symbols_by_ids(session, self._repo_id, {r.src_symbol_id for r in rows})
            return [
                Edge(
                    id=r.id,
                    kind=r.kind.value,
                    src_qname=src_map[r.src_symbol_id].qname if r.src_symbol_id in src_map else "?",
                    dst_qname=r.dst_qname,
                    resolved=r.dst_symbol_id is not None,
                    line=r.line,
                    confidence=r.confidence,
                    file=src_map[r.src_symbol_id].file if r.src_symbol_id in src_map else None,
                    sink_id=r.sink_id,
                    arg_preview=r.arg_preview,
                )
                for r in rows
            ]

    # ---------------- reachability ----------------

    def paths(
        self,
        *,
        source: SourceSpec | None = None,
        source_category: str | None = None,
        sink: SourceSpec | None = None,
        sink_category: str | None = None,
        max_depth: int = 25,
        max_paths: int = 10,
        edge_kinds: tuple[str, ...] = ("calls",),
        min_confidence: int | None = None,
        include_fuzzy: bool = False,
        include_unresolved: bool = False,
        include_callbacks: bool = False,
        explicit_sources: bool = False,
        confirmed_only: bool = False,
        taint_hops: int = 5,
        engine: str = "memory",
        strict: bool = False,
    ) -> list[CallPath]:
        """Enumerate source->sink call paths adaptively, ranked by facts.

        Sources are an explicit `source` spec (qname/glob/Symbol/Entrypoint) and/or a
        `source_category` from the taint-source catalog; sinks work the same. Each
        path carries checkable facts — the sink's catalog `severity`, the weakest
        edge confidence, the tri-state `taint_verified` verdict, `may_continue` —
        and results are ordered by them (confirmed > unchecked > refuted, then
        severity, confidence, length).

        By default the search is **adaptive**: it first tries the high-confidence
        frontier (resolved EXACT/IMPORT/unique-name edges), and only if that finds
        nothing (or is budget-truncated) does it widen to the speculative frontier
        (class-hierarchy, unresolved wildcard sinks, and callback edges). This keeps
        small, well-resolved repos precise while still surfacing leads on large,
        dynamic ones — no flags to remember. The result's `mode` records which pass
        produced it ("precise" | "widened").

        `strict=True` forces the precise pass only (never widens). Passing any of
        `include_fuzzy` / `include_unresolved` / `include_callbacks` / an explicit
        `min_confidence` runs exactly that frontier once (mode "explicit").
        """
        forced = (
            strict
            or include_fuzzy
            or include_unresolved
            or include_callbacks
            or min_confidence is not None
        )

        def run(min_conf, fuzzy, unresolved, callbacks) -> PathResults:
            return self._paths_search(
                source=source,
                source_category=source_category,
                sink=sink,
                sink_category=sink_category,
                max_depth=max_depth,
                max_paths=max_paths,
                edge_kinds=edge_kinds,
                engine=engine,
                min_confidence=min_conf,
                include_fuzzy=fuzzy,
                include_unresolved=unresolved,
                include_callbacks=callbacks,
                explicit_sources=explicit_sources,
                confirmed_only=confirmed_only,
                taint_hops=taint_hops,
            )

        if forced:
            out = run(min_confidence, include_fuzzy, include_unresolved, include_callbacks)
            out.mode = "strict" if strict else "explicit"
            return out
        # Adaptive escalation, cheapest frontier first. Lowering the confidence floor
        # and adding class-hierarchy edges reuse the same cached adjacency (only the
        # traversal filter changes), so tier 2 is free after tier 1; adding callback
        # edges changes the edge set and rebuilds, so it's the last resort — most
        # widened hits (wildcard sinks) are found at tier 2 without paying for it.
        precise = run(None, False, False, False)  # tier 1: resolved edges only
        precise.mode = "precise"
        if precise and not precise.truncated:
            return precise
        widened = run(None, True, True, False)  # tier 2: + CHA + unresolved (same adjacency)
        widened.mode = "widened"
        if widened and not widened.truncated:
            return widened
        deep = run(None, True, True, True)  # tier 3: + callback edges (rebuilds adjacency)
        deep.mode = "widened"
        if deep:
            return deep
        # Escalation found nothing new. Never trade found paths for an empty
        # wider result — a truncated narrow pass may still hold real findings.
        return widened or precise or deep

    def _paths_search(
        self,
        *,
        source: SourceSpec | None = None,
        source_category: str | None = None,
        sink: SourceSpec | None = None,
        sink_category: str | None = None,
        max_depth: int = 25,
        max_paths: int = 10,
        edge_kinds: tuple[str, ...] = ("calls",),
        min_confidence: int | None = None,
        include_fuzzy: bool = False,
        include_unresolved: bool = False,
        include_callbacks: bool = False,
        explicit_sources: bool = False,
        confirmed_only: bool = False,
        taint_hops: int = 5,
        engine: str = "memory",
    ) -> PathResults:
        """One source->sink enumeration at a fixed confidence frontier (see `paths`)."""
        floor, include_cha = _traversal_params(min_confidence, include_fuzzy, include_unresolved)
        kinds = (*edge_kinds, "callback") if include_callbacks else edge_kinds
        with self._session_factory() as session:
            self._validate_categories(session, source_category, sink_category)
            source_sets = self._resolve_sources(
                session, source, source_category, explicit_only=explicit_sources
            )
            sources = set(source_sets.all)
            sinks = self._sink_ids(session, sink, sink_category)
            if not sources or not sinks:
                return PathResults()
            # A category sink is tagged per-EDGE (with arg hints), but traversal
            # stops at the sink SYMBOL. A path reaching that symbol via an untagged
            # sibling edge — createHash('sha256') vs the md5 edge that tagged the
            # shared createHash symbol — is a false positive. Constrain the terminal
            # edge inside the traversal (not after) so untagged arrivals can't crowd
            # the candidate pool and hide a real tagged path. An explicit --sink
            # symbol qualifies on its own.
            sink_edge_ids = None
            open_sink_ids = None
            if sink_category is not None:
                sink_edge_ids = self._category_sink_edge_ids(session, sink_category)
                open_sink_ids = self._spec_to_ids(session, sink) if sink is not None else set()
            traverser = self._traverser(session, engine, kinds, floor, include_cha)
            raw_paths = traverser.paths(
                sources,
                sinks,
                max_depth=max_depth,
                max_paths=max_paths,
                sink_edge_ids=sink_edge_ids,
                open_sink_ids=open_sink_ids,
            )
            truncated = bool(getattr(raw_paths, "truncated", False))
            if not raw_paths:
                # 0 paths may mean the visit budget was spent before any sink was
                # reached — propagate the flag so this isn't mistaken for "safe".
                empty = PathResults()
                empty.truncated = truncated
                return empty
            all_ids = {node for path in raw_paths for node, _ in path}
            symbol_map = q.symbols_by_ids(session, self._repo_id, all_ids)
            registry = self._registry(session)
            edge_map = self._edge_rows(session, raw_paths)
            source_kinds = self._source_kinds(session, source_sets)
            source_meta = self._source_edge_meta(
                session, {path[0][0] for path in raw_paths}, registry, source_category
            )
            excluded_nodes = self._nodes_with_open_frontier(
                session, all_ids, kinds, floor, include_cha
            )
            built = [
                self._materialize_path(
                    path,
                    symbol_map,
                    edge_map,
                    registry,
                    source_kinds,
                    excluded_nodes,
                    source_meta,
                    source_category,
                )
                for path in raw_paths
            ]
            built = self._verify_same_function(
                session, built, raw_paths, taint_hops, registry, source_category
            )
        results = [cp for cp in built if not (confirmed_only and cp.taint_verified is not True)]
        results.sort(key=_path_rank)
        # Enumeration collected a candidate pool; return the top max_paths by rank.
        # Truncating after ranking (not during DFS) is what makes the widen flags
        # monotonic — a wider edge set can only add candidates to rank.
        out = PathResults(results[:max_paths])
        out.truncated = truncated
        return out

    def reachable(
        self,
        *,
        source: SourceSpec | None = None,
        source_category: str | None = None,
        sink: SourceSpec | None = None,
        sink_category: str | None = None,
        max_depth: int = 25,
        edge_kinds: tuple[str, ...] = ("calls",),
        min_confidence: int | None = None,
        include_fuzzy: bool = False,
        include_unresolved: bool = False,
        include_callbacks: bool = False,
        explicit_sources: bool = False,
        engine: str = "memory",
    ) -> bool:
        floor, include_cha = _traversal_params(min_confidence, include_fuzzy, include_unresolved)
        kinds = (*edge_kinds, "callback") if include_callbacks else edge_kinds
        with self._session_factory() as session:
            self._validate_categories(session, source_category, sink_category)
            sources = set(
                self._resolve_sources(
                    session, source, source_category, explicit_only=explicit_sources
                ).all
            )
            sinks = self._sink_ids(session, sink, sink_category)
            if not sources or not sinks:
                return False
            sink_edge_ids = None
            open_sink_ids = None
            if sink_category is not None:
                # match paths(): reachability of a category means a tagged terminal
                # edge is reachable, not merely the shared sink symbol (Bug 4).
                sink_edge_ids = self._category_sink_edge_ids(session, sink_category)
                open_sink_ids = self._spec_to_ids(session, sink) if sink is not None else set()
            traverser = self._traverser(session, engine, kinds, floor, include_cha)
            return traverser.reachable(
                sources, sinks, max_depth, sink_edge_ids=sink_edge_ids, open_sink_ids=open_sink_ids
            )

    # ---------------- maintenance ----------------

    def refresh(self, *, paranoid: bool = False, include_tests: bool = False) -> IndexStats:
        """Incrementally re-index: only changed/added/deleted files are reparsed."""
        from entrygraph.pipeline.scanner import index_repository

        with self._session_factory() as session:
            repo = (
                session.execute(
                    select(models.Repository).where(models.Repository.id == self._repo_id)
                )
                .scalars()
                .first()
            )
        if repo is None:
            raise RepositoryNotIndexedError("database has no indexed repository")
        stats = index_repository(
            repo.root_path,
            self._engine,
            incremental=True,
            paranoid=paranoid,
            include_tests=include_tests,
        )
        self._adjacency.clear()
        return stats

    def stats(self) -> GraphStats:
        rid = self._repo_id
        with self._session_factory() as session:
            repo = (
                session.execute(select(models.Repository).where(models.Repository.id == rid))
                .scalars()
                .first()
            )
            if repo is None:
                raise RepositoryNotIndexedError("database has no indexed repository")

            def count(stmt) -> int:
                return session.execute(stmt).scalar() or 0

            return GraphStats(
                repo_root=repo.root_path,
                index_generation=repo.index_generation,
                files=count(select(func.count(models.File.id)).where(models.File.repo_id == rid)),
                symbols=count(
                    select(func.count(models.Symbol.id)).where(models.Symbol.repo_id == rid)
                ),
                edges=count(select(func.count(models.Edge.id)).where(models.Edge.repo_id == rid)),
                resolved_edges=count(
                    select(func.count(models.Edge.id)).where(
                        models.Edge.repo_id == rid, models.Edge.dst_symbol_id.is_not(None)
                    )
                ),
                entrypoints=count(
                    select(func.count(models.Entrypoint.id)).where(models.Entrypoint.repo_id == rid)
                ),
                sink_edges=count(
                    select(func.count(models.Edge.id)).where(
                        models.Edge.repo_id == rid, models.Edge.sink_id.is_not(None)
                    )
                ),
                source_edges=count(
                    select(func.count(models.Edge.id)).where(
                        models.Edge.repo_id == rid, models.Edge.source_id.is_not(None)
                    )
                ),
            )

    def session(self) -> Session:
        """Raw ORM session — the escape hatch. Caller owns the lifecycle."""
        return self._session_factory()

    def sql(self, statement: str, params: dict | None = None) -> list[dict]:
        with self._session_factory() as session:
            result = session.execute(text(statement), params or {})
            return [dict(row._mapping) for row in result]

    # ---------------- internals ----------------

    def _generation(self, session: Session) -> int:
        gen = session.execute(
            select(models.Repository.index_generation).where(models.Repository.id == self._repo_id)
        ).scalar()
        return gen or 0

    def _traverser(
        self,
        session: Session,
        engine: str,
        edge_kinds: tuple[str, ...],
        min_confidence: int,
        include_cha: bool = True,
    ):
        if engine == "sql":
            from entrygraph.graph.cte import CteEngine

            return CteEngine(
                session, frozenset(edge_kinds), min_confidence, include_cha, self._repo_id
            )
        if engine != "memory":
            raise ValueError(f"unknown reachability engine {engine!r} (use 'memory' or 'sql')")
        # one shared cache per (kinds, generation); confidence/CHA filtering is
        # applied per traversal by the view, not baked into a per-combo cache.
        return _FilteredAdjacency(self._cache(session, edge_kinds), min_confidence, include_cha)

    def _cache(self, session: Session, edge_kinds: tuple[str, ...]) -> AdjacencyCache:
        kinds = frozenset(edge_kinds)
        generation = self._generation(session)
        key = (kinds, generation)
        cache = self._adjacency.get(key)
        if cache is None:
            cache = AdjacencyCache.build(session, generation, kinds, self._repo_id)
            self._adjacency = {k: v for k, v in self._adjacency.items() if k[1] == generation}
            self._adjacency[key] = cache
        return cache

    def sink_categories(self) -> list[str]:
        """Valid ``sink_category`` values for this repo (catalog + repo config)."""
        with self._session_factory() as session:
            return self._registry(session).sink_categories()

    def source_categories(self) -> list[str]:
        """Valid ``source_category`` values for this repo (catalog + repo config)."""
        with self._session_factory() as session:
            return self._registry(session).source_categories()

    def _validate_categories(
        self, session: Session, source_category: str | None, sink_category: str | None
    ) -> None:
        """Reject an unknown category up front. An unknown name otherwise resolves
        to an empty pattern set and silently returns zero paths — indistinguishable
        from "no reachable sinks", which is the single biggest paths usability trap."""
        registry = self._registry(session)
        if source_category is not None and source_category != "all":
            valid = registry.source_categories()
            if source_category not in valid:
                raise UnknownCategoryError(
                    f"unknown source category {source_category!r}; valid: "
                    f"{', '.join(valid)} (or 'all')"
                )
        if sink_category is not None and sink_category != "all":
            valid = registry.sink_categories()
            if sink_category not in valid:
                raise UnknownCategoryError(
                    f"unknown sink category {sink_category!r}; valid: {', '.join(valid)} (or 'all')"
                )

    def _spec_to_ids(self, session: Session, spec) -> set[int]:
        if spec is None:
            return set()
        if isinstance(spec, (list, tuple, set)):
            ids: set[int] = set()
            for item in spec:
                ids |= self._spec_to_ids(session, item)
            return ids
        if isinstance(spec, Symbol):
            return {spec.id}
        if isinstance(spec, Entrypoint):
            return {spec.symbol.id}
        return q.symbol_ids_matching(session, self._repo_id, str(spec))

    def _resolve_sources(
        self,
        session: Session,
        source,
        source_category: str | None,
        explicit_only: bool = False,
    ) -> SourceSets:
        """Taint origins split by provenance (#96 Phase 1).

        - ``spec``: symbols the user named via ``source`` (no penalty — they asked).
        - ``explicit``: symbols that call a catalog taint-source accessor (a
          demonstrable read of the category's input).
        - ``implicit``: entrypoint handlers that receive the category's input as
          parameters/properties with no accessor call — kept because property-read
          frameworks (Express ``req.body``) produce no source edge (F-H9), but
          down-weighted and dropped entirely under ``explicit_only``.
        """
        spec = self._spec_to_ids(session, source) if source is not None else set()
        explicit: set[int] = set()
        implicit: set[int] = set()
        if source_category is not None:
            registry = self._registry(session)
            source_pattern_ids = registry.source_ids_for_category(source_category)
            if source_pattern_ids:
                rows = session.execute(
                    select(models.Edge.src_symbol_id).where(
                        models.Edge.repo_id == self._repo_id,
                        models.Edge.source_id.in_(source_pattern_ids),
                    )
                ).scalars()
                explicit |= set(rows)
            # Handler-as-source: an entrypoint handler receives the category's
            # attacker-controlled input even when no catalog accessor call
            # appears in its body. HTTP: property-read frameworks (Express
            # `req.body`) produce no source edge (F-H9); CLI: cobra/click/argparse
            # handlers receive parsed argv as parameters (#86). Kept on by default
            # (dropping them would make those frameworks unanalyzable), but this is
            # weaker evidence than a demonstrable accessor call — so labeled and
            # down-weighted, and omitted entirely under explicit_only.
            if not explicit_only:
                for category, kinds in _HANDLER_SOURCE_KINDS.items():
                    if source_category in (category, "all"):
                        ep_rows = session.execute(
                            select(models.Entrypoint.symbol_id).where(
                                models.Entrypoint.repo_id == self._repo_id,
                                models.Entrypoint.kind.in_(kinds),
                            )
                        ).scalars()
                        implicit |= set(ep_rows)
        # keep the sets disjoint so per-symbol classification is unambiguous:
        # explicit/spec evidence outranks the implicit handler union
        implicit -= explicit | spec
        return SourceSets(
            explicit=frozenset(explicit), implicit=frozenset(implicit), spec=frozenset(spec)
        )

    def _source_ids(self, session: Session, source, source_category: str | None) -> set[int]:
        """Flat taint-origin set (compat wrapper over :meth:`_resolve_sources`)."""
        return set(self._resolve_sources(session, source, source_category).all)

    def _category_sink_edge_ids(self, session: Session, sink_category: str) -> set[int]:
        """Edge ids tagged as a sink of `sink_category` — used to require that a
        path terminates at a sink EDGE, not merely a shared sink symbol."""
        registry = self._registry(session)
        sink_ids = registry.ids_for_category(sink_category)
        if not sink_ids:
            return set()
        rows = session.execute(
            select(models.Edge.id).where(
                models.Edge.repo_id == self._repo_id, models.Edge.sink_id.in_(sink_ids)
            )
        ).scalars()
        return set(rows)

    def _sink_ids(self, session: Session, sink, sink_category: str | None) -> set[int]:
        ids = self._spec_to_ids(session, sink) if sink is not None else set()
        # Sink symbols are external placeholders carrying a language prefix
        # (`py:subprocess.run`). A bare `subprocess.run` matches nothing; retry with
        # a wildcard prefix so users needn't know the `py:` convention.
        if isinstance(sink, str) and not ids and ":" not in sink and "*" not in sink:
            ids = self._spec_to_ids(session, f"*:{sink}")
        if sink_category is not None:
            registry = self._registry(session)
            sink_ids = registry.ids_for_category(sink_category)
            if sink_ids:
                rows = session.execute(
                    select(models.Edge.dst_symbol_id).where(
                        models.Edge.repo_id == self._repo_id,
                        models.Edge.sink_id.in_(sink_ids),
                        models.Edge.dst_symbol_id.is_not(None),
                    )
                ).scalars()
                ids |= {r for r in rows if r is not None}
        return ids

    def _neighbors(
        self,
        target,
        depth: int,
        direction: str,
        edge_kinds: tuple[str, ...],
        include_speculative: bool = False,
    ) -> list[Symbol]:
        # Default to resolved edges only (EXACT/IMPORT/unique-name FUZZY), matching
        # paths(): the speculative class-hierarchy fan-out and unresolved wildcard
        # guesses are noise in a caller/callee listing that carries no confidence
        # marker. include_speculative lowers the floor and admits CHA edges.
        floor = 0 if include_speculative else int(Confidence.FUZZY)
        with self._session_factory() as session:
            ids = self._spec_to_ids(session, target)
            if not ids:
                raise SymbolNotFoundError(f"no symbol matching {target!r}")
            cache = self._cache(session, edge_kinds)
            found = cache.neighborhood(
                ids, depth, direction, min_confidence=floor, include_cha=include_speculative
            )
            symbol_map = q.symbols_by_ids(session, self._repo_id, found)
        return sorted(symbol_map.values(), key=lambda s: s.qname)

    def _registry(self, session: Session):
        """The repo's sink/source registry, cached on the instance.

        Rebuilding runs `merged_with`, which recompiles every pattern's regex — so
        without caching a single `paths()` call recompiled the whole catalog up to
        three times. The cache is keyed on the repo root, the `entrygraph.toml`
        mtime (so config edits are picked up), and the counts of process-global
        registered patterns (so `register_sink()` invalidates it)."""
        from entrygraph.detect import taint

        repo = (
            session.execute(select(models.Repository).where(models.Repository.id == self._repo_id))
            .scalars()
            .first()
        )
        root = repo.root_path if repo else None
        config_mtime = None
        if root is not None:
            try:
                config_mtime = (Path(root) / "entrygraph.toml").stat().st_mtime_ns
            except OSError:
                config_mtime = None
        key = (
            root,
            config_mtime,
            len(taint._user_sinks),
            len(taint._user_sources),
        )
        cached = self._registry_cache
        if cached is None or cached[0] != key:
            registry = taint.registry_for_repo(root)
            self._registry_cache = (key, registry)
            return registry
        return cached[1]

    @staticmethod
    def _edge_rows(session: Session, raw_paths) -> dict[int, models.Edge]:
        edge_ids = {hop.edge_id for path in raw_paths for _, hop in path if hop and hop.edge_id}
        if not edge_ids:
            return {}
        rows = (
            session.execute(select(models.Edge).where(models.Edge.id.in_(edge_ids))).scalars().all()
        )
        return {r.id: r for r in rows}

    @staticmethod
    def _source_edge_meta(
        session: Session, heads: set[int], registry, source_category: str | None
    ) -> dict[int, tuple[str | None, str | None]]:
        """Head symbol id -> (channel, key) of its first matching source edge (#87).

        The source accessor edge is not on the path (it points at the accessor,
        not the next hop), so it is looked up per path head. When a category was
        queried, only that category's patterns qualify; the lowest-line edge wins
        so output is deterministic. Heads with no accessor edge (handler-as-source)
        get no entry.
        """
        if not heads:
            return {}
        wanted: set[str] | None = None
        if source_category is not None:
            wanted = registry.source_ids_for_category(source_category)
        rows = session.execute(
            select(
                models.Edge.src_symbol_id,
                models.Edge.source_id,
                models.Edge.source_key,
            )
            .where(
                models.Edge.src_symbol_id.in_(heads),
                models.Edge.source_id.is_not(None),
            )
            .order_by(models.Edge.line)
        ).all()
        meta: dict[int, tuple[str | None, str | None]] = {}
        for sid, source_id, source_key in rows:
            if sid in meta:
                continue
            if wanted is not None and source_id not in wanted:
                continue
            pattern = registry.sources.get(source_id)
            channel = pattern.channel if pattern else None
            if channel is None and source_key is None:
                continue  # nothing to surface; let a later edge with info win
            meta[sid] = (channel, source_key)
        return meta

    def _verify_same_function(
        self,
        session: Session,
        built,
        raw_paths,
        taint_hops: int = 5,
        registry=None,
        source_category: str | None = None,
    ):
        """Run the taint reaching check on candidate paths and record the
        tri-state verdict (#96 Phase 2/3) — a displayed fact, not a score input.
        Bounded to the candidate pool, ``taint_hops`` interior hops, one parse
        per distinct file, staleness-guarded."""
        import dataclasses

        from entrygraph.analysis.verify import FileFactCache, verify_path

        heads = {path[0][0] for path in raw_paths}
        if not heads:
            return built
        repo_root = session.execute(
            select(models.Repository.root_path).where(models.Repository.id == self._repo_id)
        ).scalar()
        cache = FileFactCache(repo_root)
        accessor_lines = self._source_accessor_lines(session, heads, registry, source_category)
        # hashes for every function file on any candidate path (not just heads)
        all_func_ids = {node for path in raw_paths for node, _ in path}
        file_hashes = self._file_hashes_for_symbols(session, all_func_ids)
        out = []
        for cp in built:
            head = cp.symbols[0]
            verdict = verify_path(
                cp,
                cp.source_kind or "spec",
                accessor_lines.get(head.id, set()),
                file_hashes,
                cache,
                hop_limit=taint_hops,
            )
            out.append(dataclasses.replace(cp, taint_verified=verdict))
        return out

    @staticmethod
    def _source_accessor_lines(
        session: Session, heads: set[int], registry=None, source_category: str | None = None
    ) -> dict[int, set[int]]:
        """Per source symbol, the lines of its catalog source-accessor call edges —
        seeds the inline-accessor case (`sink(request.args.get("q"))`). When a
        category was queried, only that category's accessors qualify: an accessor
        of a *different* category must not seed (and wrongly confirm) this one."""
        if not heads:
            return {}
        wanted: set[str] | None = None
        if registry is not None and source_category is not None:
            wanted = registry.source_ids_for_category(source_category)
        rows = session.execute(
            select(models.Edge.src_symbol_id, models.Edge.line, models.Edge.source_id).where(
                models.Edge.src_symbol_id.in_(heads),
                models.Edge.source_id.is_not(None),
            )
        ).all()
        out: dict[int, set[int]] = {}
        for sid, line, source_id in rows:
            if wanted is not None and source_id not in wanted:
                continue
            out.setdefault(sid, set()).add(line)
        return out

    @staticmethod
    def _file_hashes_for_symbols(session: Session, heads: set[int]) -> dict[str, str]:
        """Repo-relative path -> indexed content hash for the files owning ``heads``
        (the staleness guard for query-time re-parsing)."""
        if not heads:
            return {}
        rows = session.execute(
            select(models.File.path, models.File.content_hash)
            .join(models.Symbol, models.Symbol.file_id == models.File.id)
            .where(models.Symbol.id.in_(heads))
        ).all()
        return {path: h for path, h in rows}  # noqa: C416 (Row is not a plain tuple)

    @staticmethod
    def _source_kinds(session: Session, sets: SourceSets) -> dict[int, str]:
        """Per-seed provenance label, strongest evidence first (#96 Phase 1):
        explicit (accessor call) > spec (user named) > handler_params (implicit
        handler with tainted params) > handler (bare implicit handler)."""
        kinds: dict[int, str] = {}
        for sid in sets.explicit:
            kinds[sid] = _SOURCE_KIND_EXPLICIT
        for sid in sets.spec:
            kinds.setdefault(sid, _SOURCE_KIND_SPEC)
        if sets.implicit:
            rows = session.execute(
                select(models.Entrypoint.symbol_id, models.Entrypoint.extra).where(
                    models.Entrypoint.symbol_id.in_(sets.implicit)
                )
            ).all()
            has_params = {
                sid for sid, extra in rows if extra and json.loads(extra).get("tainted_params")
            }
            for sid in sets.implicit:
                kinds.setdefault(
                    sid,
                    _SOURCE_KIND_HANDLER_PARAMS if sid in has_params else _SOURCE_KIND_HANDLER,
                )
        return kinds

    @staticmethod
    def _nodes_with_open_frontier(
        session: Session,
        node_ids: set[int],
        kinds: tuple[str, ...],
        floor: int,
        include_cha: bool,
    ) -> set[int]:
        """Path nodes that have outgoing call edges the traverser excluded — i.e.
        reachability may continue past them. Covers edges below the confidence
        floor, dynamic placeholders, and (when CHA is off) class-hierarchy edges,
        which sit at FUZZY confidence and so aren't caught by the floor at the
        default FUZZY floor."""
        if not node_ids:
            return set()
        from entrygraph.kinds import EdgeKind

        kind_enums = [EdgeKind(k) for k in kinds if k in {e.value for e in EdgeKind}]
        excluded = (models.Edge.confidence < floor) | (models.Edge.via == "dynamic")
        if not include_cha:
            excluded = excluded | (models.Edge.via == "cha")
        rows = session.execute(
            select(models.Edge.src_symbol_id).where(
                models.Edge.src_symbol_id.in_(node_ids),
                models.Edge.dst_symbol_id.is_not(None),
                models.Edge.kind.in_(kind_enums),
                excluded,
            )
        ).scalars()
        return set(rows)

    def _materialize_path(
        self,
        path,
        symbol_map,
        edge_map,
        registry,
        source_kinds,
        excluded_nodes,
        source_meta,
        source_category=None,
    ) -> CallPath:
        symbols = tuple(symbol_map[node] for node, _ in path)
        hops = [hop for _, hop in path[1:]]
        rows = [edge_map.get(hop.edge_id) if hop else None for hop in hops]

        terminal = rows[-1] if rows else None
        sink_id = terminal.sink_id if terminal else None
        terminal_const = is_constant_args(terminal.arg_preview) if terminal else False

        path_edges: list[PathEdge] = []
        for i, hop in enumerate(hops):
            row = rows[i]
            is_terminal = i == len(hops) - 1
            path_edges.append(
                PathEdge(
                    kind=hop.kind,
                    line=hop.line,
                    confidence=hop.confidence,
                    sink_id=row.sink_id if row else None,
                    via=row.via if row else hop.via,
                    arg_preview=row.arg_preview if row else None,
                    constant_args=terminal_const if is_terminal else False,
                )
            )

        source_channel, source_key = source_meta.get(path[0][0], (None, None))
        source_kind = source_kinds.get(path[0][0], _SOURCE_KIND_SPEC)
        may_continue = any(node in excluded_nodes for node, _ in path)
        return CallPath(
            symbols=symbols,
            edges=tuple(path_edges),
            severity=registry.severity_of(sink_id),
            may_continue=may_continue,
            source_kind=source_kind,
            source_channel=source_channel,
            source_key=source_key,
            source_category=source_category,
        )
