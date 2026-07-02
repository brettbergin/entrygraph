"""CodeGraph — the public facade.

Every method opens a short-lived ORM Session internally and returns detached,
frozen dataclasses; the Session never escapes (except via the explicit
``session()`` escape hatch).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

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
from entrygraph.graph.adjacency import AdjacencyCache, Hop
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

SourceSpec = "str | Symbol | Entrypoint | list[str | Symbol | Entrypoint]"


class CodeGraph:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = make_session_factory(engine)
        self._adjacency: dict[tuple[frozenset[str], int], AdjacencyCache] = {}

    # ---------------- construction ----------------

    @classmethod
    def index(cls, root: str | Path, db: str | Path | None = None) -> "CodeGraph":
        """Index (or fully re-index) a repository and return an open graph."""
        from entrygraph.pipeline.scanner import index_repository

        root = Path(root).resolve()
        db_path = Path(db) if db else root / DEFAULT_DB_NAME
        engine = make_engine(db_path)
        graph = cls(engine)
        graph._last_index_stats = index_repository(root, engine)
        return graph

    @classmethod
    def open(cls, db: str | Path) -> "CodeGraph":
        db_path = Path(db)
        if not db_path.exists():
            raise DatabaseNotFoundError(f"no index database at {db_path}")
        engine = make_engine(db_path)
        check_schema(engine)
        return cls(engine)

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> "CodeGraph":
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
    ) -> list[Symbol]:
        with self._session_factory() as session:
            return q.select_symbols(
                session, kind=kind, name=name, qname=qname, file=file,
                include_external=include_external, limit=limit, offset=offset,
            )

    def symbol(self, qname: str) -> Symbol:
        matches = self.symbols(qname=qname, include_external=True, limit=2)
        exact = [s for s in matches if s.qname == qname]
        if not exact:
            raise SymbolNotFoundError(f"no symbol with qname {qname!r}")
        return exact[0]

    def iter_symbols(self, *, batch_size: int = 1000, **filters) -> Iterator[Symbol]:
        offset = 0
        while True:
            batch = self.symbols(**filters, limit=batch_size, offset=offset)
            yield from batch
            if len(batch) < batch_size:
                return
            offset += batch_size

    def files(self, *, language: str | None = None, path: str | None = None) -> list[FileInfo]:
        with self._session_factory() as session:
            return q.select_files(session, language=language, path=path)

    # ---------------- detection ----------------

    def detect(self) -> DetectionReport:
        with self._session_factory() as session:
            rows = session.execute(
                select(models.Detection).order_by(models.Detection.confidence.desc())
            ).scalars().all()
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
        languages.sort(key=lambda l: l.byte_count, reverse=True)
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

    def callers(self, target: "SourceSpec", *, depth: int = 1,
                edge_kinds: tuple[str, ...] = ("calls",)) -> list[Symbol]:
        return self._neighbors(target, depth, "in", edge_kinds)

    def callees(self, target: "SourceSpec", *, depth: int = 1,
                edge_kinds: tuple[str, ...] = ("calls",)) -> list[Symbol]:
        return self._neighbors(target, depth, "out", edge_kinds)

    def references(self, target: "SourceSpec") -> list[Edge]:
        """All inbound edges (any kind) to the matching symbols."""
        with self._session_factory() as session:
            ids = self._spec_to_ids(session, target)
            if not ids:
                return []
            rows = session.execute(
                select(models.Edge).where(models.Edge.dst_symbol_id.in_(ids))
            ).scalars().all()
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
        source: "SourceSpec",
        sink: "SourceSpec | None" = None,
        sink_category: str | None = None,
        max_depth: int = 25,
        max_paths: int = 10,
        edge_kinds: tuple[str, ...] = ("calls",),
        min_confidence: int = 0,
        engine: str = "memory",
    ) -> list[CallPath]:
        with self._session_factory() as session:
            sources = self._spec_to_ids(session, source)
            sinks = self._sink_ids(session, sink, sink_category)
            if not sources or not sinks:
                return []
            traverser = self._traverser(session, engine, edge_kinds, min_confidence)
            raw_paths = traverser.paths(sources, sinks, max_depth=max_depth, max_paths=max_paths)
            all_ids = {node for path in raw_paths for node, _ in path}
            symbol_map = q.symbols_by_ids(session, all_ids)
        return [self._to_call_path(path, symbol_map) for path in raw_paths]

    def reachable(
        self,
        *,
        source: "SourceSpec",
        sink: "SourceSpec | None" = None,
        sink_category: str | None = None,
        max_depth: int = 25,
        edge_kinds: tuple[str, ...] = ("calls",),
        min_confidence: int = 0,
        engine: str = "memory",
    ) -> bool:
        with self._session_factory() as session:
            sources = self._spec_to_ids(session, source)
            sinks = self._sink_ids(session, sink, sink_category)
            if not sources or not sinks:
                return False
            traverser = self._traverser(session, engine, edge_kinds, min_confidence)
            return traverser.reachable(sources, sinks, max_depth)

    # ---------------- maintenance ----------------

    def refresh(self, *, paranoid: bool = False) -> IndexStats:
        """Incrementally re-index: only changed/added/deleted files are reparsed."""
        from entrygraph.pipeline.scanner import index_repository

        with self._session_factory() as session:
            repo = session.execute(select(models.Repository)).scalars().first()
        if repo is None:
            raise RepositoryNotIndexedError("database has no indexed repository")
        stats = index_repository(repo.root_path, self._engine, incremental=True,
                                 paranoid=paranoid)
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
                    select(func.count(models.Edge.id)).where(
                        models.Edge.dst_symbol_id.is_not(None)
                    )
                ),
                entrypoints=count(select(func.count(models.Entrypoint.id))),
                sink_edges=count(
                    select(func.count(models.Edge.id)).where(models.Edge.sink_id.is_not(None))
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

    def _traverser(self, session: Session, engine: str, edge_kinds: tuple[str, ...],
                min_confidence: int):
        if engine == "sql":
            from entrygraph.graph.cte import CteEngine

            return CteEngine(session, frozenset(edge_kinds), min_confidence)
        if engine != "memory":
            raise ValueError(f"unknown reachability engine {engine!r} (use 'memory' or 'sql')")
        return self._cache(session, edge_kinds, min_confidence)

    def _cache(self, session: Session, edge_kinds: tuple[str, ...],
               min_confidence: int) -> AdjacencyCache:
        kinds = frozenset(edge_kinds)
        generation = self._generation(session)
        key = (kinds | {f"minconf:{min_confidence}"}, generation)
        cache = self._adjacency.get(key)
        if cache is None:
            cache = AdjacencyCache.build(session, generation, kinds, min_confidence)
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

    def _sink_ids(self, session: Session, sink, sink_category: str | None) -> set[int]:
        ids = self._spec_to_ids(session, sink) if sink is not None else set()
        if sink_category is not None:
            from entrygraph.detect.taint import registry_for_repo

            repo = session.execute(select(models.Repository)).scalars().first()
            registry = registry_for_repo(repo.root_path if repo else None)
            sink_ids = registry.ids_for_category(sink_category)
            if sink_ids:
                rows = session.execute(
                    select(models.Edge.dst_symbol_id).where(
                        models.Edge.sink_id.in_(sink_ids),
                        models.Edge.dst_symbol_id.is_not(None),
                    )
                ).scalars()
                ids |= set(rows)
        return ids

    def _neighbors(self, target, depth: int, direction: str,
                   edge_kinds: tuple[str, ...]) -> list[Symbol]:
        with self._session_factory() as session:
            ids = self._spec_to_ids(session, target)
            if not ids:
                raise SymbolNotFoundError(f"no symbol matching {target!r}")
            cache = self._cache(session, edge_kinds, 0)
            found = cache.neighborhood(ids, depth, direction)
            symbol_map = q.symbols_by_ids(session, found)
        return sorted(symbol_map.values(), key=lambda s: s.qname)

    @staticmethod
    def _to_call_path(path: list[tuple[int, "Hop | None"]],
                      symbol_map: dict[int, Symbol]) -> CallPath:
        symbols = tuple(symbol_map[node] for node, _ in path)
        edges = tuple(
            PathEdge(kind=hop.kind, line=hop.line, confidence=hop.confidence)
            for _, hop in path[1:]
        )
        return CallPath(symbols=symbols, edges=edges)
