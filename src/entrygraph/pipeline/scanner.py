"""Index orchestrator: walk -> diff -> parse+extract -> resolve -> detect -> write.

One code path serves both full and incremental indexing. Incremental wipes only
changed/deleted files, re-extracts them, resolves their references against a
symbol table seeded from the surviving DB symbols, and heals edges (in either
direction) that now bind to newly-created symbols. The result is identical to a
full re-index.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session

from entrygraph.db.meta import ensure_schema
from entrygraph.db.models import Detection, Edge, Entrypoint, File, Repository, Symbol
from entrygraph.detect import entrypoints as entrypoint_rules
from entrygraph.detect.frameworks import detect_frameworks
from entrygraph.detect.manifests import parse_manifests
from entrygraph.detect.taint import SinkRegistry, registry_for_repo
from entrygraph.extract.ir import FileExtraction
from entrygraph.fs.hashing import FileState, diff_files
from entrygraph.fs.walker import RepoLanguageProfile, WalkedFile, walk_repo
from entrygraph.kinds import Confidence, EdgeKind, SymbolKind
from entrygraph.pipeline.worker import extract_batch, extract_one
from entrygraph.pipeline.writer import IdAllocator, bulk_insert
from entrygraph.resolve.externals import ExternalRegistry
from entrygraph.resolve.hierarchy import resolve_hierarchy
from entrygraph.resolve.resolver import FileResolver
from entrygraph.resolve.symbol_table import SymbolTable
from entrygraph.results import IndexStats

_PARALLEL_THRESHOLD = 200  # files; below this the pool overhead isn't worth it
_BATCH = 24


def index_repository(
    root: str | Path,
    engine: Engine,
    *,
    incremental: bool = False,
    paranoid: bool = False,
    max_workers: int | None = None,
) -> IndexStats:
    started = time.monotonic()
    root = Path(root).resolve()
    fresh = ensure_schema(engine)
    if fresh:
        incremental = False

    walked, profile = walk_repo(root)
    manifests = parse_manifests(root)
    sink_registry = registry_for_repo(root)

    with Session(engine) as session:
        repo = _load_or_create_repo(session, root, incremental)
        known = _known_file_states(session, repo.id) if incremental else {}
        diff = diff_files(walked, known, paranoid=paranoid)

        if incremental:
            deleted = _wipe_files(
                session, repo.id, [*[w.path for w in diff.changed], *diff.deleted_paths]
            )
        else:
            _wipe_repo_graph(session, repo.id)
            deleted = 0
        session.flush()

        alloc = IdAllocator(session)
        table = SymbolTable()
        if incremental:
            _load_existing_symbols(session, repo.id, table)

        to_index = diff.to_index
        extractions = _parse_phase(to_index, max_workers)

        # ---- persist file rows (skipped + indexed + unchanged metadata) ----
        file_id_by_path = _write_files(session, repo, walked, diff, alloc, incremental)

        # ---- framework detection + entrypoint rules ----
        frameworks, detected_names = _detect_frameworks(manifests, extractions, walked, profile)
        for _path, x, _pkg in extractions:
            for rule in entrypoint_rules.rules_for(x.language, detected_names):
                x.entrypoint_hints.extend(rule.match(x))

        # ---- symbols ----
        symbol_id_by_qname, module_ids = _write_symbols(
            session, extractions, file_id_by_path, alloc, table
        )

        # ---- class hierarchy (parents + re-exports) before edge resolution ----
        resolve_hierarchy(extractions, table)

        # ---- resolve references -> edges + entrypoints ----
        externals = ExternalRegistry(lambda: alloc.take(Symbol))
        if incremental:
            externals.preload(_existing_externals(session))
        new_qnames = set(symbol_id_by_qname) | {x.module_path for _p, x, _pkg in extractions}
        edge_count, entrypoint_count = _write_edges_and_entrypoints(
            session,
            extractions,
            file_id_by_path,
            module_ids,
            symbol_id_by_qname,
            table,
            externals,
            alloc,
            sink_registry,
        )
        entrypoint_count += _write_config_entrypoints(
            session, root, symbol_id_by_qname, table, alloc, incremental
        )

        if incremental:
            _heal_dangling_edges(session, table, new_qnames)
            _gc_orphan_externals(session)

        # ---- detections ----
        _write_detections(session, repo, profile, frameworks)

        repo.file_count = len(walked)
        repo.symbol_count = _count_symbols(session)
        session.commit()

        return IndexStats(
            files_scanned=len(walked),
            files_indexed=len(diff.to_index),
            files_skipped=sum(1 for w in walked if w.skip_reason),
            files_deleted=deleted,
            symbols=repo.symbol_count,
            edges=edge_count,
            entrypoints=entrypoint_count,
            duration_seconds=round(time.monotonic() - started, 3),
        )


# ---------------- phase helpers ----------------


def _pool_context():
    """multiprocessing context for the parse pool.

    Prefer ``fork`` where available (POSIX): unlike the ``spawn`` default on
    macOS/Windows, fork does not re-import the caller's ``__main__`` module, so
    ``CodeGraph.index()`` works from any calling context — scripts without an
    ``if __name__ == "__main__"`` guard, notebooks, web request handlers — rather
    than raising a bootstrapping RuntimeError and re-running top-level code in
    every worker. tree-sitter parsers are created lazily inside each worker, so
    there is no pre-fork C state to corrupt.
    """
    import multiprocessing as mp

    if "fork" in mp.get_all_start_methods():
        return mp.get_context("fork")
    return mp.get_context()  # spawn (Windows); callers must guard __main__


def _parse_phase(
    to_index: list[WalkedFile], max_workers: int | None
) -> list[tuple[str, FileExtraction, bool]]:
    if not to_index:
        return []
    workers = max_workers if max_workers is not None else (os.cpu_count() or 2)
    if len(to_index) < _PARALLEL_THRESHOLD or workers <= 1:
        results = []
        for wf in to_index:
            result = extract_one(wf)
            if result is not None:
                results.append(result)
        return results

    batches = [to_index[i : i + _BATCH] for i in range(0, len(to_index), _BATCH)]
    results = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=_pool_context()) as pool:
        for batch_result in pool.map(extract_batch, batches):
            results.extend(batch_result)
    return results


def _load_or_create_repo(session: Session, root: Path, incremental: bool) -> Repository:
    repo = (
        session.execute(select(Repository).where(Repository.root_path == str(root)))
        .scalars()
        .first()
    )
    if repo is None:
        repo = Repository(id=1, root_path=str(root), index_generation=0)
        session.add(repo)
        session.flush()
    repo.indexed_at = datetime.now(UTC)
    repo.index_generation += 1
    return repo


def _known_file_states(session: Session, repo_id: int) -> dict[str, FileState]:
    rows = session.execute(
        select(File.path, File.content_hash, File.size_bytes, File.mtime_ns).where(
            File.repo_id == repo_id
        )
    )
    return {p: FileState(h, s, m) for p, h, s, m in rows}


def _wipe_repo_graph(session: Session, repo_id: int) -> None:
    """Full-reindex: clear the graph for this repo, keep the repo row + meta."""
    file_ids = select(File.id).where(File.repo_id == repo_id)
    symbol_ids = select(Symbol.id).where(Symbol.file_id.in_(file_ids))
    session.execute(delete(Edge).where(Edge.src_file_id.in_(file_ids)))
    session.execute(delete(Entrypoint).where(Entrypoint.symbol_id.in_(symbol_ids)))
    session.execute(delete(Symbol))  # includes external placeholders (file_id NULL)
    session.execute(delete(File).where(File.repo_id == repo_id))
    session.execute(delete(Detection).where(Detection.repo_id == repo_id))


def _wipe_files(session: Session, repo_id: int, paths: list[str]) -> int:
    if not paths:
        return 0
    file_ids = list(
        session.execute(
            select(File.id).where(File.repo_id == repo_id, File.path.in_(paths))
        ).scalars()
    )
    if not file_ids:
        return 0
    # edges owned by these files; symbols cascade to entrypoints and SET NULL on
    # inbound edges (degrading them to unresolved but keeping dst_qname).
    session.execute(delete(Edge).where(Edge.src_file_id.in_(file_ids)))
    session.execute(delete(Symbol).where(Symbol.file_id.in_(file_ids)))
    session.execute(delete(File).where(File.id.in_(file_ids)))
    return len(file_ids)


def _load_existing_symbols(session: Session, repo_id: int, table: SymbolTable) -> None:
    rows = session.execute(select(Symbol.id, Symbol.qname, Symbol.name, Symbol.kind))
    for sid, qname, name, kind in rows:
        if kind is SymbolKind.MODULE:
            table.add_module(qname, sid)
        elif kind is not SymbolKind.EXTERNAL:
            table.add_symbol(sid, qname, name, kind)
    # class bases/parents of surviving classes, from inherit + implement edges.
    # dst_qname is the already-resolved parent FQN, so it feeds both class_bases
    # (raw text, legacy) and class_parents (the transitive ancestor walk).
    base_rows = session.execute(
        select(Symbol.qname, Edge.dst_qname)
        .join(Edge, Edge.src_symbol_id == Symbol.id)
        .where(Edge.kind.in_((EdgeKind.INHERITS, EdgeKind.IMPLEMENTS)))
    )
    for class_qname, base_qname in base_rows:
        table.class_bases.setdefault(class_qname, []).append(base_qname)
        if base_qname in table.by_fqn:  # project parent -> walkable ancestor
            table.class_parents.setdefault(class_qname, []).append(base_qname)


def _existing_externals(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Symbol.qname, Symbol.id).where(Symbol.kind == SymbolKind.EXTERNAL)
    )
    # NOT dict(rows): a SQLAlchemy Result exposes .keys(), so dict() would treat it
    # as a mapping and subscript it (TypeError). Unpack each Row explicitly.
    return {qname: sid for qname, sid in rows}  # noqa: C416


def _write_files(
    session: Session,
    repo: Repository,
    walked: list[WalkedFile],
    diff,
    alloc: IdAllocator,
    incremental: bool,
) -> dict[str, int]:
    """Insert File rows for added/changed (+ skipped) files; return path->id.

    On incremental, unchanged files keep their existing rows; we look up their
    ids so symbols/edges can still reference them if needed.
    """
    file_id_by_path: dict[str, int] = {}
    if incremental:
        for path, fid in session.execute(select(File.path, File.id).where(File.repo_id == repo.id)):
            file_id_by_path[path] = fid

    reindexed = {w.path for w in diff.to_index} | {
        w.path for w in walked if w.skip_reason and w.path not in file_id_by_path
    }
    new_rows = []
    for wf in walked:
        if wf.path in file_id_by_path and wf.path not in {w.path for w in diff.to_index}:
            continue  # unchanged; row already present
        if wf.path not in reindexed and not wf.skip_reason:
            continue
        file_id = alloc.take(File)
        file_id_by_path[wf.path] = file_id
        new_rows.append(
            {
                "id": file_id,
                "repo_id": repo.id,
                "path": wf.path,
                "language": wf.language,
                "content_hash": diff.hashes.get(wf.path, ""),
                "size_bytes": wf.size_bytes,
                "mtime_ns": wf.mtime_ns,
                "generation": repo.index_generation,
                "skip_reason": wf.skip_reason,
            }
        )
    bulk_insert(session, File, new_rows)
    return file_id_by_path


def _detect_frameworks(manifests, extractions, walked, profile: RepoLanguageProfile):
    import_signals = {
        (x.language, value)
        for _p, x, _pkg in extractions
        for kind, value in x.framework_signals
        if kind == "import"
    }
    symbol_names = {raw.name for _p, x, _pkg in extractions for raw in x.symbols}
    languages_present = profile.extractable_languages()
    if languages_present & {"typescript", "tsx"}:
        languages_present.add("javascript")
    frameworks = detect_frameworks(
        manifests,
        import_signals,
        [w.path for w in walked],
        symbol_names,
        languages_present=languages_present,
    )
    return frameworks, {fw.name for fw in frameworks}


def _write_symbols(session, extractions, file_id_by_path, alloc, table):
    symbol_rows: list[dict] = []
    module_ids: dict[str, int] = {}
    for path, x, _pkg in extractions:
        module_id = alloc.take(Symbol)
        module_ids[path] = module_id
        table.add_module(x.module_path, module_id)
        symbol_rows.append(
            {
                "id": module_id,
                "file_id": file_id_by_path[path],
                "kind": SymbolKind.MODULE,
                "name": x.module_path.rsplit(".", 1)[-1],
                "qname": x.module_path,
                "parent_id": None,
                "start_line": 1,
                "end_line": 0,
                "start_col": 0,
                "signature": None,
                "docstring": None,
                "is_exported": True,
            }
        )

    symbol_id_by_qname: dict[str, int] = {}
    for path, x, _pkg in extractions:
        for raw in x.symbols:
            symbol_id = alloc.take(Symbol)
            symbol_id_by_qname[raw.qualified_name] = symbol_id
            table.add_symbol(symbol_id, raw.qualified_name, raw.name, raw.kind)
            if raw.kind is SymbolKind.CLASS and raw.bases:
                table.class_bases[raw.qualified_name] = raw.bases
            symbol_rows.append(
                {
                    "id": symbol_id,
                    "file_id": file_id_by_path[path],
                    "kind": raw.kind,
                    "name": raw.name,
                    "qname": raw.qualified_name,
                    "parent_id": None,
                    "start_line": raw.span.start_line,
                    "end_line": raw.span.end_line,
                    "start_col": raw.span.start_col,
                    "signature": raw.signature,
                    "docstring": raw.docstring,
                    "is_exported": raw.is_exported,
                }
            )
    for row in symbol_rows:
        qname = row["qname"]
        if row["kind"] is not SymbolKind.MODULE and "." in qname:
            parent_q = qname.rsplit(".", 1)[0]
            row["parent_id"] = symbol_id_by_qname.get(parent_q) or table.module_symbol_ids.get(
                parent_q
            )
    # A row's parent has one fewer qname segment, so inserting shallowest-first
    # guarantees the self-referential parent_id FK is satisfied within the batch.
    symbol_rows.sort(key=lambda r: r["qname"].count("."))
    bulk_insert(session, Symbol, symbol_rows)
    return symbol_id_by_qname, module_ids


def _write_edges_and_entrypoints(
    session,
    extractions,
    file_id_by_path,
    module_ids,
    symbol_id_by_qname,
    table,
    externals,
    alloc,
    sink_registry: SinkRegistry,
):
    edge_rows: list[dict] = []
    entrypoint_rows: list[dict] = []

    for path, x, is_package in extractions:
        resolver = FileResolver(x, module_ids[path], table, externals, is_package)
        file_id = file_id_by_path[path]
        for edge in resolver.resolve():
            sink_id = (
                sink_registry.match(edge.dst_qname, edge.arg_preview)
                if edge.kind is EdgeKind.CALLS
                else None
            )
            edge_rows.append(
                {
                    "id": alloc.take(Edge),
                    "kind": edge.kind,
                    "src_symbol_id": edge.src_symbol_id,
                    "dst_symbol_id": edge.dst_symbol_id,
                    "dst_qname": edge.dst_qname,
                    "src_file_id": file_id,
                    "line": edge.line,
                    "confidence": int(edge.confidence),
                    "arg_preview": edge.arg_preview,
                    "sink_id": sink_id,
                    "via": edge.via,
                }
            )
        for hint in x.entrypoint_hints:
            symbol_id = (
                symbol_id_by_qname.get(hint.handler_qualified_name or "") or module_ids[path]
            )
            entrypoint_rows.append(
                {
                    "id": alloc.take(Entrypoint),
                    "kind": hint.kind,
                    "framework": hint.framework,
                    "symbol_id": symbol_id,
                    "route": hint.route,
                    "http_method": ",".join(hint.http_methods) or None,
                    "extra": json.dumps(hint.metadata) if hint.metadata else None,
                }
            )
    bulk_insert(session, Symbol, externals.new_rows)
    bulk_insert(session, Edge, edge_rows)
    bulk_insert(session, Entrypoint, entrypoint_rows)
    return len(edge_rows), len(entrypoint_rows)


def _write_config_entrypoints(
    session,
    root,
    symbol_id_by_qname: dict[str, int],
    table: SymbolTable,
    alloc: IdAllocator,
    incremental: bool,
) -> int:
    """Scan serverless/SAM/Procfile/Dockerfile and bind their handlers to symbols.

    Config files aren't tracked in the files table, so on incremental runs their
    entrypoints are fully deleted and re-derived each time.
    """
    from entrygraph.detect.entrypoints.configs import (
        CONFIG_FRAMEWORKS,
        bind_handler,
        scan_config_entrypoints,
    )

    if incremental:
        session.execute(delete(Entrypoint).where(Entrypoint.framework.in_(CONFIG_FRAMEWORKS)))

    rows = []
    for hint in scan_config_entrypoints(root):
        symbol_id = bind_handler(hint.handler_ref, symbol_id_by_qname, table.module_symbol_ids)
        if symbol_id is None:  # non-nullable FK: skip unbindable handlers
            continue
        rows.append(
            {
                "id": alloc.take(Entrypoint),
                "kind": hint.kind,
                "framework": hint.framework,
                "symbol_id": symbol_id,
                "route": hint.route,
                "http_method": None,
                "extra": json.dumps(hint.metadata) if hint.metadata else None,
            }
        )
    bulk_insert(session, Entrypoint, rows)
    return len(rows)


def _heal_dangling_edges(session, table: SymbolTable, new_qnames: set[str]) -> None:
    """Re-bind edges left NULL (degraded on wipe, or targeting a not-yet-existing
    symbol) whose dst_qname now names a freshly-created symbol."""
    dangling = session.execute(
        select(Edge.id, Edge.dst_qname).where(
            Edge.dst_symbol_id.is_(None), Edge.dst_qname.in_(new_qnames)
        )
    ).all()
    updates = []
    for edge_id, dst_qname in dangling:
        target = table.by_fqn.get(dst_qname)
        if target is not None:
            updates.append(
                {"id": edge_id, "dst_symbol_id": target, "confidence": int(Confidence.IMPORT)}
            )
    if updates:
        session.execute(update(Edge), updates)


def _gc_orphan_externals(session) -> None:
    """Drop external placeholder symbols no edge points at anymore.

    Full re-index never creates them; deleting them keeps an incremental graph
    byte-identical to a full one after a file's last reference disappears.
    """
    referenced = select(Edge.dst_symbol_id).where(Edge.dst_symbol_id.is_not(None))
    session.execute(
        delete(Symbol).where(Symbol.kind == SymbolKind.EXTERNAL, Symbol.id.not_in(referenced))
    )


def _write_detections(session, repo, profile: RepoLanguageProfile, frameworks) -> None:
    session.execute(delete(Detection).where(Detection.repo_id == repo.id))
    rows = [
        {
            "repo_id": repo.id,
            "category": "language",
            "name": stat.name,
            "version": None,
            "confidence": 1.0,
            "evidence": json.dumps(
                {
                    "files": stat.file_count,
                    "bytes": stat.byte_count,
                    "percent": round(stat.percent, 2),
                }
            ),
        }
        for stat in profile.stats()
    ]
    rows.extend(
        {
            "repo_id": repo.id,
            "category": "framework",
            "name": fw.name,
            "version": None,
            "confidence": fw.confidence,
            "evidence": json.dumps({"language": fw.language, "signals": list(fw.evidence)}),
        }
        for fw in frameworks
    )
    if rows:
        bulk_insert(session, Detection, rows)


def _count_symbols(session) -> int:
    from sqlalchemy import func

    return session.execute(select(func.count(Symbol.id))).scalar() or 0
