"""CodeGraph — the public facade.

Every method opens a short-lived ORM Session internally and returns detached,
frozen dataclasses; the Session never escapes (except via the explicit
``session()`` escape hatch).
"""

from __future__ import annotations

import json
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
)
from entrygraph.graph.adjacency import AdjacencyCache
from entrygraph.graph.scoring import is_constant_args, score_path
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

type SourceSpec = str | Symbol | Entrypoint | list[str | Symbol | Entrypoint]


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

    def paths(self, sources, sinks, max_depth=25, max_paths=10):
        return self.cache.paths(
            sources, sinks, max_depth, max_paths, self.min_confidence, self.include_cha
        )

    def reachable(self, sources, sinks, max_depth):
        return self.cache.reachable(
            sources, sinks, max_depth, self.min_confidence, self.include_cha
        )


class PathResults(list):
    """CallPath list that also reports whether enumeration was budget-truncated.

    It is a plain list for every existing use (iteration, len, indexing); the
    `truncated` flag lets the CLI warn when 0 paths may mean "budget spent", not
    "no reachable sink".
    """

    truncated: bool = False


def _has_sanitizer(path: CallPath) -> bool:
    """True if a category sanitizer is called on the path (heuristic, no dataflow).

    Drives ``--prune-sanitized``. Sanitizer matches only discount risk (never zero
    it), so pruning is an explicit opt-in that trades recall for noise reduction
    rather than a silent, certain "this path is safe" claim."""
    return any(e.sanitized_by for e in path.edges)


class CodeGraph:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = make_session_factory(engine)
        self._adjacency: dict[tuple[frozenset[str], int], AdjacencyCache] = {}
        self._last_index_stats: IndexStats | None = None
        self._registry_cache: tuple[tuple, object] | None = None

    # ---------------- construction ----------------

    @classmethod
    def index(cls, root: str | Path, db: str | Path | None = None) -> CodeGraph:
        """Index (or fully re-index) a repository and return an open graph."""
        from entrygraph.pipeline.scanner import index_repository

        root = Path(root).resolve()
        db_path = Path(db) if db else root / DEFAULT_DB_NAME
        engine = make_engine(db_path)
        graph = cls(engine)
        graph._last_index_stats = index_repository(root, engine)
        return graph

    @classmethod
    def open(cls, db: str | Path) -> CodeGraph:
        db_path = Path(db)
        if not db_path.exists():
            raise DatabaseNotFoundError(f"no index database at {db_path}")
        engine = make_engine(db_path)
        check_schema(engine)
        return cls(engine)

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
            return q.select_files(session, language=language, path=path)

    # ---------------- detection ----------------

    def detect(self) -> DetectionReport:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(models.Detection).order_by(models.Detection.confidence.desc())
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
                session, kind=kind, framework=framework, route=route, limit=limit
            )

    # ---------------- traversal ----------------

    def callers(
        self, target: SourceSpec, *, depth: int = 1, edge_kinds: tuple[str, ...] = ("calls",)
    ) -> list[Symbol]:
        return self._neighbors(target, depth, "in", edge_kinds)

    def callees(
        self, target: SourceSpec, *, depth: int = 1, edge_kinds: tuple[str, ...] = ("calls",)
    ) -> list[Symbol]:
        return self._neighbors(target, depth, "out", edge_kinds)

    def references(self, target: SourceSpec) -> list[Edge]:
        """All inbound edges (any kind) to the matching symbols."""
        with self._session_factory() as session:
            ids = self._spec_to_ids(session, target)
            if not ids:
                return []
            rows = (
                session.execute(select(models.Edge).where(models.Edge.dst_symbol_id.in_(ids)))
                .scalars()
                .all()
            )
            src_map = q.symbols_by_ids(session, {r.src_symbol_id for r in rows})
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
        prune_sanitized: bool = False,
        engine: str = "memory",
    ) -> list[CallPath]:
        """Enumerate source->sink call paths, risk-ranked (highest first).

        Sources are an explicit `source` spec (qname/glob/Symbol/Entrypoint) and/or
        a `source_category` resolved from the taint-source catalog (e.g. every call
        site of an `http_input`/`env` source). Sinks work the same via `sink` /
        `sink_category`.

        By default only EXACT/IMPORT edges are traversed; `include_fuzzy` /
        `include_unresolved` lower the confidence floor (an explicit
        `min_confidence` int overrides the flags). `include_callbacks` also
        follows PASSED_AS_CALLBACK edges. Each returned path carries a heuristic
        `risk_score` and a `may_continue` flag; `prune_sanitized` drops paths
        neutralized by a registered sanitizer.
        """
        floor, include_cha = _traversal_params(min_confidence, include_fuzzy, include_unresolved)
        kinds = (*edge_kinds, "callback") if include_callbacks else edge_kinds
        with self._session_factory() as session:
            sources = self._source_ids(session, source, source_category)
            sinks = self._sink_ids(session, sink, sink_category)
            if not sources or not sinks:
                return PathResults()
            traverser = self._traverser(session, engine, kinds, floor, include_cha)
            raw_paths = traverser.paths(sources, sinks, max_depth=max_depth, max_paths=max_paths)
            if not raw_paths:
                # 0 paths may mean the visit budget was spent before any sink was
                # reached — propagate the flag so this isn't mistaken for "safe".
                empty = PathResults()
                empty.truncated = bool(getattr(raw_paths, "truncated", False))
                return empty
            all_ids = {node for path in raw_paths for node, _ in path}
            symbol_map = q.symbols_by_ids(session, all_ids)
            registry = self._registry(session)
            edge_map = self._edge_rows(session, raw_paths)
            tainted_sources = self._tainted_source_ids(session, sources)
            excluded_nodes = self._nodes_with_open_frontier(
                session, all_ids, kinds, floor, include_cha
            )
            sibling_calls = self._out_call_qnames(session, all_ids)
            built = [
                self._materialize_path(
                    path,
                    symbol_map,
                    edge_map,
                    registry,
                    tainted_sources,
                    excluded_nodes,
                    sibling_calls,
                )
                for path in raw_paths
            ]
        results = [cp for cp in built if not (prune_sanitized and _has_sanitizer(cp))]
        results.sort(
            key=lambda p: (-(p.risk_score or 0.0), len(p.symbols), [s.id for s in p.symbols])
        )
        # Enumeration collected a candidate pool; return the top max_paths by risk.
        # Truncating after the risk rank (not during DFS) is what makes the widen
        # flags monotonic — a wider edge set can only add candidates to rank.
        out = PathResults(results[:max_paths])
        out.truncated = bool(getattr(raw_paths, "truncated", False))
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
        engine: str = "memory",
    ) -> bool:
        floor, include_cha = _traversal_params(min_confidence, include_fuzzy, include_unresolved)
        kinds = (*edge_kinds, "callback") if include_callbacks else edge_kinds
        with self._session_factory() as session:
            sources = self._source_ids(session, source, source_category)
            sinks = self._sink_ids(session, sink, sink_category)
            if not sources or not sinks:
                return False
            traverser = self._traverser(session, engine, kinds, floor, include_cha)
            return traverser.reachable(sources, sinks, max_depth)

    # ---------------- maintenance ----------------

    def refresh(self, *, paranoid: bool = False) -> IndexStats:
        """Incrementally re-index: only changed/added/deleted files are reparsed."""
        from entrygraph.pipeline.scanner import index_repository

        with self._session_factory() as session:
            repo = session.execute(select(models.Repository)).scalars().first()
        if repo is None:
            raise RepositoryNotIndexedError("database has no indexed repository")
        stats = index_repository(repo.root_path, self._engine, incremental=True, paranoid=paranoid)
        self._adjacency.clear()
        return stats

    def stats(self) -> GraphStats:
        with self._session_factory() as session:
            repo = session.execute(select(models.Repository)).scalars().first()
            if repo is None:
                raise RepositoryNotIndexedError("database has no indexed repository")

            def count(stmt) -> int:
                return session.execute(stmt).scalar() or 0

            return GraphStats(
                repo_root=repo.root_path,
                index_generation=repo.index_generation,
                files=count(select(func.count(models.File.id))),
                symbols=count(select(func.count(models.Symbol.id))),
                edges=count(select(func.count(models.Edge.id))),
                resolved_edges=count(
                    select(func.count(models.Edge.id)).where(models.Edge.dst_symbol_id.is_not(None))
                ),
                entrypoints=count(select(func.count(models.Entrypoint.id))),
                sink_edges=count(
                    select(func.count(models.Edge.id)).where(models.Edge.sink_id.is_not(None))
                ),
                source_edges=count(
                    select(func.count(models.Edge.id)).where(models.Edge.source_id.is_not(None))
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
        gen = session.execute(select(models.Repository.index_generation)).scalar()
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

            return CteEngine(session, frozenset(edge_kinds), min_confidence, include_cha)
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
            cache = AdjacencyCache.build(session, generation, kinds)
            self._adjacency = {k: v for k, v in self._adjacency.items() if k[1] == generation}
            self._adjacency[key] = cache
        return cache

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
        return q.symbol_ids_matching(session, str(spec))

    def _source_ids(self, session: Session, source, source_category: str | None) -> set[int]:
        """Taint origins: explicit `source` spec plus, for `source_category`, every
        symbol that calls a matching catalog taint-source function."""
        ids = self._spec_to_ids(session, source) if source is not None else set()
        if source_category is not None:
            registry = self._registry(session)
            source_pattern_ids = registry.source_ids_for_category(source_category)
            if source_pattern_ids:
                rows = session.execute(
                    select(models.Edge.src_symbol_id).where(
                        models.Edge.source_id.in_(source_pattern_ids)
                    )
                ).scalars()
                ids |= set(rows)
            if source_category == "http_input":
                # Every HTTP route handler receives attacker-controlled request
                # data, so the handler itself is an http_input source. This covers
                # frameworks whose request access is a property read (Express
                # `req.body`, Symfony `$request->get`) rather than a catalog-matched
                # call, which otherwise yield zero source edges (F-H9) — Express/
                # Symfony apps could never produce a taint path.
                ep_rows = session.execute(
                    select(models.Entrypoint.symbol_id).where(
                        models.Entrypoint.kind == EntrypointKind.HTTP_ROUTE
                    )
                ).scalars()
                ids |= set(ep_rows)
        return ids

    def _sink_ids(self, session: Session, sink, sink_category: str | None) -> set[int]:
        ids = self._spec_to_ids(session, sink) if sink is not None else set()
        if sink_category is not None:
            registry = self._registry(session)
            sink_ids = registry.ids_for_category(sink_category)
            if sink_ids:
                rows = session.execute(
                    select(models.Edge.dst_symbol_id).where(
                        models.Edge.sink_id.in_(sink_ids),
                        models.Edge.dst_symbol_id.is_not(None),
                    )
                ).scalars()
                ids |= {r for r in rows if r is not None}
        return ids

    def _neighbors(
        self, target, depth: int, direction: str, edge_kinds: tuple[str, ...]
    ) -> list[Symbol]:
        with self._session_factory() as session:
            ids = self._spec_to_ids(session, target)
            if not ids:
                raise SymbolNotFoundError(f"no symbol matching {target!r}")
            cache = self._cache(session, edge_kinds)
            found = cache.neighborhood(ids, depth, direction)
            symbol_map = q.symbols_by_ids(session, found)
        return sorted(symbol_map.values(), key=lambda s: s.qname)

    def _registry(self, session: Session):
        """The repo's sink/source/sanitizer registry, cached on the instance.

        Rebuilding runs `merged_with`, which recompiles every pattern's regex — so
        without caching a single `paths()` call recompiled the whole catalog up to
        three times. The cache is keyed on the repo root, the `entrygraph.toml`
        mtime (so config edits are picked up), and the counts of process-global
        registered patterns (so `register_sink()` invalidates it)."""
        from entrygraph.detect import taint

        repo = session.execute(select(models.Repository)).scalars().first()
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
            len(taint._user_sanitizers),
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
    def _tainted_source_ids(session: Session, sources: set[int]) -> set[int]:
        """Source symbol ids known to carry user-controlled data: entrypoints with
        tainted params, plus call sites of catalog taint-source functions."""
        if not sources:
            return set()
        tainted: set[int] = set()
        rows = session.execute(
            select(models.Entrypoint.symbol_id, models.Entrypoint.extra).where(
                models.Entrypoint.symbol_id.in_(sources)
            )
        ).all()
        for sid, extra in rows:
            if extra and json.loads(extra).get("tainted_params"):
                tainted.add(sid)
        source_callers = session.execute(
            select(models.Edge.src_symbol_id).where(
                models.Edge.source_id.is_not(None),
                models.Edge.src_symbol_id.in_(sources),
            )
        ).scalars()
        tainted |= set(source_callers)
        return tainted

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

    @staticmethod
    def _out_call_qnames(session: Session, node_ids: set[int]) -> dict[int, list[str]]:
        """For each node, the callee qnames of its outgoing CALLS edges.

        Used for sanitizer detection: a sanitizer is usually a *sibling* call of
        an on-path function (`shlex.quote(x)` then `subprocess.run(...)`), not a
        node on the source->sink path itself, so it never appears among the path's
        own hops."""
        if not node_ids:
            return {}
        from entrygraph.kinds import EdgeKind

        rows = session.execute(
            select(models.Edge.src_symbol_id, models.Edge.dst_qname).where(
                models.Edge.src_symbol_id.in_(node_ids),
                models.Edge.kind == EdgeKind.CALLS,
            )
        ).all()
        out: dict[int, list[str]] = {}
        for sid, dst_qname in rows:
            out.setdefault(sid, []).append(dst_qname)
        return out

    def _materialize_path(
        self,
        path,
        symbol_map,
        edge_map,
        registry,
        tainted_sources,
        excluded_nodes,
        sibling_calls,
    ) -> CallPath:
        symbols = tuple(symbol_map[node] for node, _ in path)
        hops = [hop for _, hop in path[1:]]
        rows = [edge_map.get(hop.edge_id) if hop else None for hop in hops]

        # sink is the terminal edge; its category drives sanitizer matching
        terminal = rows[-1] if rows else None
        sink_id = terminal.sink_id if terminal else None
        sink_category = registry.sinks[sink_id].category if sink_id in registry.sinks else None
        terminal_const = is_constant_args(terminal.arg_preview) if terminal else False

        # sanitizer detection: a sanitizer for the sink's category called by any
        # on-path node (its own hops OR a sibling call)
        sibling_qnames = {q for node, _ in path for q in sibling_calls.get(node, ())}
        sanitized_ids, sanitized_effect = self._path_sanitizers(
            symbols, rows, sibling_qnames, registry, sink_category
        )

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
                    sanitized_by=tuple(sanitized_ids) if is_terminal else (),
                )
            )

        risk = score_path(
            hop_confidences=[h.confidence for h in hops],
            hop_vias=[(row.via if row is not None else h.via) for row, h in zip(rows, hops)],
            sink_severity=registry.severity_of(sink_id),
            sanitized_effect=sanitized_effect,
            constant_args=terminal_const,
            source_tainted=path[0][0] in tainted_sources,
        )
        may_continue = any(node in excluded_nodes for node, _ in path)
        return CallPath(
            symbols=symbols, edges=tuple(path_edges), risk_score=risk, may_continue=may_continue
        )

    @staticmethod
    def _path_sanitizers(symbols, rows, sibling_qnames, registry, sink_category):
        """Return (matched sanitizer ids, effect) for the sink category.

        Candidates are every callee reachable from an on-path node: the path's own
        hops plus sibling calls (`sibling_qnames`). The effect is capped at
        ``"reduces"`` regardless of the catalog's ``effect`` — with no dataflow we
        cannot prove the sanitized value is the one reaching the sink, so a match
        discounts risk but never drives it to zero (which would silently hide a
        real path). ``--prune-sanitized`` is the explicit, heuristic opt-in to
        drop these."""
        if not sink_category:
            return [], None
        matched: list = []
        candidates = (
            [s.qname for s in symbols]
            + [r.dst_qname for r in rows if r is not None]
            + list(sibling_qnames)
        )
        for qname in candidates:
            for san in registry.match_sanitizers(qname):
                if san.category == sink_category:
                    matched.append(san)
        if not matched:
            return [], None
        return [s.id for s in matched], "reduces"
