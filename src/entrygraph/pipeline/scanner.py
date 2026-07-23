"""Index orchestrator: walk -> diff -> parse+extract -> resolve -> detect -> write.

One code path serves both full and incremental indexing. Incremental wipes only
changed/deleted files, re-extracts them, resolves their references against a
symbol table seeded from the surviving DB symbols, and heals edges (in either
direction) that now bind to newly-created symbols. The result is identical to a
full re-index.
"""

from __future__ import annotations

import functools
import json
import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.orm import Session, aliased

from entrygraph.db.meta import ANALYZER_VERSION
from entrygraph.db.migrations import is_stale, prepare_db
from entrygraph.db.models import Detection, Edge, Entrypoint, File, Repository, Symbol
from entrygraph.detect import entrypoints as entrypoint_rules
from entrygraph.detect.entrypoints.base import first_string_arg
from entrygraph.detect.express_mounts import resolve_mount_prefixes
from entrygraph.detect.frameworks import detect_frameworks
from entrygraph.detect.graphql_link import link_graphql
from entrygraph.detect.grpc_expand import expand_grpc
from entrygraph.detect.manifests import parse_manifests
from entrygraph.detect.taint import SinkRegistry, registry_for_repo
from entrygraph.errors import IndexCancelledError
from entrygraph.extract.ir import EntrypointHint, FileExtraction
from entrygraph.fs.hashing import FileState, diff_files
from entrygraph.fs.walker import RepoLanguageProfile, WalkedFile, walk_repo
from entrygraph.kinds import Confidence, EdgeKind, SymbolKind
from entrygraph.pipeline.worker import extract_batch, extract_one
from entrygraph.pipeline.writer import BatchedWriter, IdAllocator, bulk_insert
from entrygraph.resolve.bindings import resolve_bindings
from entrygraph.resolve.externals import ExternalRegistry
from entrygraph.resolve.hierarchy import resolve_hierarchy
from entrygraph.resolve.resolver import FileResolver
from entrygraph.resolve.symbol_table import SymbolTable
from entrygraph.results import IndexStats

_PARALLEL_THRESHOLD = 200  # files; below this the pool overhead isn't worth it
_BATCH = 24

# Progress callback: (phase, done, total) -> False requests cancellation.
# Phases: "walking", "extracting", "resolving", "writing".
ProgressCallback = Callable[[str, int, int], "bool | None"]


def _report(on_progress, phase: str, done: int, total: int) -> None:
    """Invoke the progress callback, translating a False return into
    cancellation. A *throwing* callback must never corrupt an index run, so its
    own exceptions are swallowed — only the explicit False triggers the
    (transaction-rolling-back) IndexCancelledError."""
    if on_progress is None:
        return
    try:
        result = on_progress(phase, done, total)
    except Exception:
        return
    if result is False:
        raise IndexCancelledError(f"index cancelled during {phase}")


def index_repository(
    root: str | Path,
    engine: Engine,
    *,
    incremental: bool = False,
    paranoid: bool = False,
    max_workers: int | None = None,
    include_tests: bool = False,
    on_progress: ProgressCallback | None = None,
) -> IndexStats:
    started = time.monotonic()
    root = Path(root).resolve()
    fresh = prepare_db(engine)  # migrate in place; True only if created/rebuilt from scratch
    if fresh:
        incremental = False

    walked, profile = walk_repo(root, include_tests=include_tests)
    _report(on_progress, "walking", len(walked), len(walked))
    manifests = parse_manifests(root)
    sink_registry = registry_for_repo(root)

    with Session(engine) as session:
        repo = _load_or_create_repo(session, root, incremental)
        # An analyzer-logic upgrade leaves file bytes unchanged, so the content-hash
        # diff would skip everything and miss the new detection. When this repo's
        # data predates the current analyzer, force a full re-scan of *this repo*
        # (others are untouched) so every file is re-extracted (#analyzer-versioning).
        if is_stale(repo.analyzer_version):
            incremental = False
        known = _known_file_states(session, repo.id) if incremental else {}
        diff = diff_files(walked, known, paranoid=paranoid)

        if incremental and not diff.to_index and not diff.deleted_paths:
            # Nothing changed: skip the whole middle — symbol-table load, parse,
            # resolution, edge healing, external GC, detection rewrite, and the
            # full symbol re-count (all O(repo)). The graph and detections are
            # already correct; re-running detection here with empty extractions
            # would even *degrade* import-based framework confidence.
            session.commit()
            return IndexStats(
                files_scanned=len(walked),
                files_indexed=0,
                files_skipped=sum(1 for w in walked if w.skip_reason),
                files_deleted=0,
                symbols=repo.symbol_count or 0,
                edges=0,
                entrypoints=0,
                duration_seconds=round(time.monotonic() - started, 3),
            )

        if incremental:
            # Changed files are wiped so they can be re-inserted fresh, but only the
            # genuinely-removed paths count as "deleted"; a modified file is reported
            # under files_indexed (it's in diff.to_index), not deleted (#46).
            _wipe_files(session, repo.id, [*[w.path for w in diff.changed], *diff.deleted_paths])
            deleted = len(diff.deleted_paths)
        else:
            _wipe_repo_graph(session, repo.id)
            deleted = 0
        session.flush()

        # Defer FK enforcement to COMMIT (checked once) instead of per-insert: a bulk
        # load writes rows in dependency order by construction, so per-row FK probes
        # are pure overhead, and cross-batch/self-referential ordering (a symbol whose
        # parent module has a deeper qname — e.g. C#) can't be guaranteed at insert
        # time. This MUST run after a transaction is already open (the flush above);
        # pysqlite executes a PRAGMA in autocommit otherwise, so it would silently
        # apply to a throwaway transaction and leave the bulk writes checking FKs
        # immediately (#116 QA: FK-constraint crash indexing large C# repos). Resets
        # at commit, so read sessions keep immediate FKs.
        session.execute(text("PRAGMA defer_foreign_keys=ON"))

        alloc = IdAllocator(session)
        table = SymbolTable()
        if incremental:
            _load_existing_symbols(session, repo.id, table)

        to_index = diff.to_index
        extractions, worker_hashes = _parse_phase(
            to_index, max_workers, include_tests, on_progress=on_progress
        )
        _report(on_progress, "resolving", 0, 1)
        # the diff phase deferred hashing of parsed files to the worker; fold the
        # results back in so file rows get their content_hash.
        diff.hashes.update(worker_hashes)

        # Drop test-only submodule files declared elsewhere (Rust `#[cfg(test)] mod
        # tests;` -> a separate tests.rs the file gate can't classify). Their symbols
        # and edges are excluded; the file is recorded as skipped, not indexed. #100
        if not include_tests:
            test_files = {f for _p, x, _pkg in extractions for f in x.test_submodule_files}
            if test_files:
                extractions = [(p, x, pkg) for p, x, pkg in extractions if p not in test_files]
                for wf in walked:
                    if wf.path in test_files and wf.skip_reason is None:
                        wf.skip_reason = "test"

        # ---- persist file rows (skipped + indexed + unchanged metadata) ----
        file_id_by_path = _write_files(session, repo, walked, diff, alloc, incremental)

        # ---- framework detection + entrypoint rules ----
        frameworks, detected_names = _detect_frameworks(manifests, extractions, walked, profile)
        fw_confidence = {fw.name: fw.confidence for fw in frameworks}
        route_wrappers = _collect_route_wrappers(extractions)
        mount_prefixes = resolve_mount_prefixes(extractions)
        for _path, x, _pkg in extractions:
            x.route_wrappers = route_wrappers
            x.route_prefixes = mount_prefixes.get(x.module_path, {})
            for rule in entrypoint_rules.rules_for(x.language, detected_names):
                x.entrypoint_hints.extend(rule.match(x))
            # express/fastify/koa/hono (and gin/chi/fiber) share a registration
            # shape, so when several are detected the same route is emitted once
            # per framework. Collapse hints identical in kind/handler/route/method,
            # keeping the highest-confidence framework's label — kills the ~2x
            # inflation and prefers the real framework over a spuriously-detected one.
            x.entrypoint_hints = _dedup_entrypoint_hints(x.entrypoint_hints, fw_confidence)

        # ---- symbols ----
        symbol_id_by_qname, module_ids, handler_ids_by_path = _write_symbols(
            session, extractions, file_id_by_path, alloc, table, repo.id
        )

        # ---- class hierarchy (parents + re-exports) before edge resolution ----
        resolve_hierarchy(extractions, table)

        # ---- syntactic name->type bindings (#98): fills the table's binding maps
        # and persists resolved type_ref on field/variable symbols ----
        type_refs = resolve_bindings(extractions, table)
        _write_type_refs(session, type_refs)

        # ---- gRPC per-method expansion via the binding table (#98 P2 / #37) ----
        expand_grpc(extractions, table)

        # ---- GraphQL SDL fields -> code resolvers (cross-file rebind + dedup) ----
        link_graphql(extractions, table)

        # ---- resolve references -> edges + entrypoints ----
        externals = ExternalRegistry(lambda: alloc.take(Symbol), repo.id)
        if incremental:
            externals.preload(_existing_externals(session, repo.id))
        new_qnames = set(symbol_id_by_qname) | {x.module_path for _p, x, _pkg in extractions}
        edge_count, entrypoint_count = _write_edges_and_entrypoints(
            session,
            extractions,
            file_id_by_path,
            module_ids,
            symbol_id_by_qname,
            handler_ids_by_path,
            table,
            externals,
            alloc,
            sink_registry,
            repo.id,
        )
        entrypoint_count += _write_config_entrypoints(
            session, root, symbol_id_by_qname, table, alloc, incremental, repo.id
        )

        if incremental:
            _heal_dangling_edges(session, table, new_qnames, repo.id)
            _gc_orphan_externals(session, repo.id)

        # ---- detections ----
        _write_detections(session, repo, profile, frameworks, incremental=incremental)

        _report(on_progress, "writing", 0, 1)
        repo.file_count = len(walked)
        repo.symbol_count = _count_symbols(session, repo.id)
        repo.analyzer_version = ANALYZER_VERSION  # this repo is now current; clears stale
        session.commit()

        return IndexStats(
            files_scanned=len(walked),
            files_indexed=len(diff.to_index),
            # skips among files considered this run. The byte-peek content gate is
            # deferred to the diff, so on an incremental run a binary/minified file
            # that is unchanged (fast-path) is not re-flagged here — consistent with
            # files_indexed, which is also a this-run count.
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

    On Linux, prefer ``fork``: unlike ``spawn``, fork does not re-import the
    caller's ``__main__`` module, so ``CodeGraph.index()`` works from any calling
    context — scripts without an ``if __name__ == "__main__"`` guard, notebooks,
    web request handlers — rather than raising a bootstrapping RuntimeError and
    re-running top-level code in every worker. tree-sitter parsers are created
    lazily inside each worker, so there is no pre-fork C state to corrupt.

    macOS is excluded: ``fork`` without ``exec`` is unsafe there because Apple
    system frameworks (Objective-C runtime, libdispatch) abort the child if they
    were touched by any thread in the parent (``+[NSNumber initialize] may have
    been in progress ... when fork() was called``). macOS and Windows use
    ``spawn``; the CLI entry points are ``__main__``-guarded, and unguarded
    library callers fall back to sequential extraction in ``_parse_phase``.
    """
    import multiprocessing as mp

    if sys.platform != "darwin" and "fork" in mp.get_all_start_methods():
        return mp.get_context("fork")
    return mp.get_context("spawn")


def _extract_sequential(
    to_index: list[WalkedFile], include_tests: bool = False, on_progress=None
) -> list[tuple[str, FileExtraction, bool, str]]:
    results = []
    for i, wf in enumerate(to_index):
        result = extract_one(wf, include_tests)
        if result is not None:
            results.append(result)
        if on_progress is not None and (i + 1) % _BATCH == 0:
            _report(on_progress, "extracting", i + 1, len(to_index))
    return results


def _collect_extractions(
    to_index: list[WalkedFile],
    max_workers: int | None,
    include_tests: bool = False,
    on_progress=None,
) -> list[tuple[str, FileExtraction, bool, str]]:
    if not to_index:
        return []
    workers = max_workers if max_workers is not None else (os.cpu_count() or 2)
    if len(to_index) < _PARALLEL_THRESHOLD or workers <= 1:
        return _extract_sequential(to_index, include_tests, on_progress)

    batches = [to_index[i : i + _BATCH] for i in range(0, len(to_index), _BATCH)]
    # a picklable partial carries the flag across the spawn/fork pool boundary
    worker = functools.partial(extract_batch, include_tests=include_tests)
    try:
        results = []
        done = 0
        with ProcessPoolExecutor(max_workers=workers, mp_context=_pool_context()) as pool:
            for i, batch_result in enumerate(pool.map(worker, batches)):
                results.extend(batch_result)
                done += len(batches[i])
                _report(on_progress, "extracting", done, len(to_index))
        return results
    except BrokenProcessPool:
        # A worker pool couldn't start or died (e.g. an unguarded __main__ under
        # spawn, or a sandbox with no subprocess support). Degrade to correct,
        # single-threaded extraction rather than crashing the whole index.
        return _extract_sequential(to_index, include_tests, on_progress)


def _parse_phase(
    to_index: list[WalkedFile],
    max_workers: int | None,
    include_tests: bool = False,
    on_progress=None,
) -> tuple[list[tuple[str, FileExtraction, bool]], dict[str, str]]:
    """Extract to_index files, returning (extractions, content hashes by path).

    The worker hashes each file it reads, so hashes flow back here instead of the
    diff phase reading every file a second time."""
    raw = _collect_extractions(to_index, max_workers, include_tests, on_progress)
    extractions = [(path, x, pkg) for path, x, pkg, _h in raw]
    hashes = {path: h for path, _x, _pkg, h in raw}
    return extractions, hashes


def _load_or_create_repo(session: Session, root: Path, incremental: bool) -> Repository:
    repo = (
        session.execute(select(Repository).where(Repository.root_path == str(root)))
        .scalars()
        .first()
    )
    if repo is None:
        # allocate a unique repo id (a global DB holds many repositories, #116)
        next_id = (session.execute(select(func.max(Repository.id))).scalar() or 0) + 1
        repo = Repository(id=next_id, root_path=str(root), index_generation=0)
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
    session.execute(delete(Edge).where(Edge.repo_id == repo_id))
    session.execute(delete(Entrypoint).where(Entrypoint.repo_id == repo_id))
    # repo_id also covers this repo's external placeholders (file_id NULL) without
    # touching other repos' symbols in a global DB (#116)
    session.execute(delete(Symbol).where(Symbol.repo_id == repo_id))
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


# Confidence-tie preference for frameworks that share a route decorator shape.
# Only frameworks listed here get a nudge; everyone else stays at 0 (first-seen).
_FRAMEWORK_TIEBREAK = {"fastapi": 1}


def _dedup_entrypoint_hints(
    hints: list[EntrypointHint], fw_confidence: dict[str, float] | None = None
) -> list[EntrypointHint]:
    """Collapse hints that duplicate another in (kind, handler, route, methods).

    Shared-shape router rules (express/fastify/koa/hono; gin/chi/fiber) each fire
    when their framework is detected, emitting the same registration once per
    framework. Hints identical in those fields are the same physical route; keep
    the one whose framework has the highest detection confidence (so a real
    framework wins over a spuriously-detected one). Confidence ties break by a
    small framework preference, then first-seen order.
    """
    conf = fw_confidence or {}

    def rank(fw: str | None) -> tuple[float, int]:
        # On a detection-confidence tie, prefer the framework whose *primary* syntax
        # matches the collision. `@app.get`-style verb routes are FastAPI's primary
        # form but only Flask's secondary form (its primary is `@app.route`), so a
        # verb route shared with Flask should read as FastAPI, not flask (#116 QA).
        return (conf.get(fw or "", 0.0), _FRAMEWORK_TIEBREAK.get(fw or "", 0))

    best: dict[tuple, EntrypointHint] = {}
    order: list[tuple] = []
    for h in hints:
        key = (h.kind, h.handler_qualified_name, h.route, tuple(h.http_methods))
        if key not in best:
            best[key] = h
            order.append(key)
        elif rank(h.framework) > rank(best[key].framework):
            best[key] = h
    return [best[k] for k in order]


def _write_type_refs(session: Session, type_refs: dict[int, str]) -> None:
    """Persist resolved binding types onto their symbols (#98). Bulk UPDATE keyed
    by symbol id; a no-op when nothing resolved."""
    if not type_refs:
        return
    session.execute(
        update(Symbol),
        [{"id": sid, "type_ref": ref} for sid, ref in type_refs.items()],
    )


def _load_existing_symbols(session: Session, repo_id: int, table: SymbolTable) -> None:
    # File.language feeds same-language fuzzy scoping; external symbols have no file.
    parent = aliased(Symbol)
    rows = session.execute(
        select(
            Symbol.id,
            Symbol.qname,
            Symbol.name,
            Symbol.kind,
            Symbol.type_ref,
            File.language,
            parent.qname,
        )
        .join(File, Symbol.file_id == File.id, isouter=True)
        .join(parent, Symbol.parent_id == parent.id, isouter=True)
        .where(Symbol.repo_id == repo_id)
    )
    for sid, qname, name, kind, type_ref, language, parent_qname in rows:
        if kind is SymbolKind.MODULE:
            table.add_module(qname, sid, language)
        elif kind is not SymbolKind.EXTERNAL:
            table.add_symbol(sid, qname, name, kind, language, parent_qname)
        # rebuild the binding maps from persisted type_ref (#98)
        if type_ref:
            if kind in (SymbolKind.FIELD, SymbolKind.PROPERTY):
                table.field_types[qname] = type_ref
            elif kind in (SymbolKind.VARIABLE, SymbolKind.CONSTANT) and "." in qname:
                module, var = qname.rsplit(".", 1)
                table.module_bindings.setdefault(module, {})[var] = type_ref
            elif kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                # a function's type_ref is its resolved return type; needed so a
                # call_result binding in a changed file can still type through an
                # unchanged callee's return type on incremental re-index (#113)
                table.return_types[qname] = type_ref
    # class parents of surviving classes, from inherit + implement edges.
    # dst_qname is the already-resolved parent FQN; keep only project parents,
    # which are the walkable ancestors for the transitive hierarchy.
    base_rows = session.execute(
        select(Symbol.qname, Edge.dst_qname)
        .join(Edge, Edge.src_symbol_id == Symbol.id)
        .where(Edge.repo_id == repo_id, Edge.kind.in_((EdgeKind.INHERITS, EdgeKind.IMPLEMENTS)))
    )
    for class_qname, base_qname in base_rows:
        if base_qname in table.by_fqn:  # project parent -> walkable ancestor
            table.class_parents.setdefault(class_qname, []).append(base_qname)


def _existing_externals(session: Session, repo_id: int) -> dict[str, int]:
    rows = session.execute(
        select(Symbol.qname, Symbol.id).where(
            Symbol.repo_id == repo_id, Symbol.kind == SymbolKind.EXTERNAL
        )
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

    to_index_paths = {w.path for w in diff.to_index}
    reindexed = to_index_paths | {
        w.path for w in walked if w.skip_reason and w.path not in file_id_by_path
    }
    new_rows = []
    for wf in walked:
        if wf.path in file_id_by_path and wf.path not in to_index_paths:
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


_DJANGO_REGISTRARS = frozenset({"path", "re_path", "url"})


def _collect_route_wrappers(extractions) -> set[str]:
    """Names of project functions that forward to a native Django route registrar.

    A function whose body makes a bare `path(...)` / `re_path(...)` / `url(...)`
    call is a thin routing wrapper (Zulip's `rest_path` forwards to `path`), so
    calls to it in a urls.py are route registrations. Module-level registrar calls
    (the urlpatterns themselves) have no enclosing function and are excluded (#50).
    """
    wrappers: set[str] = set()
    for _p, x, _pkg in extractions:
        if x.language != "python":
            continue
        for ref in x.references:
            if (
                ref.kind == "call"
                and ref.receiver_text is None
                and ref.callee_name in _DJANGO_REGISTRARS
                and ref.caller_qualified_name  # inside a def, not module-level urlpatterns
            ):
                wrappers.add(ref.caller_qualified_name.rsplit(".", 1)[-1])
    return wrappers


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


def _write_symbols(session, extractions, file_id_by_path, alloc, table, repo_id):
    symbol_rows: list[dict] = []
    module_ids: dict[str, int] = {}
    for path, x, _pkg in extractions:
        module_id = alloc.take(Symbol)
        module_ids[path] = module_id
        table.add_module(x.module_path, module_id, x.language)
        # A module spans its whole file. The extractor doesn't record a line count,
        # so approximate the last line from the furthest span of any extracted
        # symbol/import/reference (>= 1 = start_line). This keeps the module row
        # inside the end_line >= start_line invariant instead of the old end_line=0
        # (#44), while covering all of the file's content.
        line_spans = [s.span.end_line for s in x.symbols]
        line_spans += [r.span.end_line for r in x.references]
        line_spans += [i.span.end_line for i in x.imports]
        module_end_line = max(line_spans, default=1)
        symbol_rows.append(
            {
                "id": module_id,
                "repo_id": repo_id,
                "file_id": file_id_by_path[path],
                "kind": SymbolKind.MODULE,
                "name": x.module_path.rsplit(".", 1)[-1],
                "qname": x.module_path,
                "parent_id": None,
                "start_line": 1,
                "end_line": module_end_line,
                "start_col": 0,
                "signature": None,
                "docstring": None,
                "is_exported": True,
            }
        )

    symbol_id_by_qname: dict[str, int] = {}
    # Per-file qname -> id, so an entrypoint binds to the definition in ITS OWN
    # file rather than whichever same-qname symbol happened to be written last.
    # Without this, N same-package `func init()` / overloads collapse in the
    # global map and all their entrypoints bind to one symbol (duplicate rows,
    # the rest of the real definitions unrepresented).
    handler_ids_by_path: dict[str, dict[str, int]] = {}
    for path, x, _pkg in extractions:
        per_file = handler_ids_by_path.setdefault(path, {})
        for raw in x.symbols:
            symbol_id = alloc.take(Symbol)
            symbol_id_by_qname[raw.qualified_name] = symbol_id
            per_file[raw.qualified_name] = symbol_id
            table.add_symbol(
                symbol_id,
                raw.qualified_name,
                raw.name,
                raw.kind,
                x.language,
                raw.parent_qualified_name,
            )
            symbol_rows.append(
                {
                    "id": symbol_id,
                    "repo_id": repo_id,
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
    # file id -> that file's module symbol id, so a top-level symbol is parented to
    # ITS OWN module. When several files share a package/module_path (every
    # multi-file Go package), `module_symbol_ids` keeps only the last file's module
    # id; parenting siblings to it means wiping one file cascade-deletes the others'
    # symbols (parent_id FK is ondelete=CASCADE). This was the incremental-reindex
    # data loss in #41 (F-H11).
    module_id_by_file_id = {file_id_by_path[p]: mid for p, mid in module_ids.items()}
    for row in symbol_rows:
        qname = row["qname"]
        if row["kind"] is not SymbolKind.MODULE and "." in qname:
            parent_q = qname.rsplit(".", 1)[0]
            parent = symbol_id_by_qname.get(parent_q)
            if parent is None:  # parent is the module itself, not a project symbol
                parent = module_id_by_file_id.get(row["file_id"]) or table.module_symbol_ids.get(
                    parent_q
                )
            row["parent_id"] = parent
    # Insert shallowest-qname-first so a symbol's parent usually precedes it. This is
    # only best-effort: a symbol whose parent is its file's *module* can have a parent
    # whose qname is deeper than its own (e.g. C#, where the class qname prefix is the
    # namespace, not the file-path module), which this sort can't order. FK checks are
    # deferred to COMMIT (see index_repository), so cross-batch insert order is safe.
    symbol_rows.sort(key=lambda r: r["qname"].count("."))
    bulk_insert(session, Symbol, symbol_rows)
    return symbol_id_by_qname, module_ids, handler_ids_by_path


def _write_edges_and_entrypoints(
    session,
    extractions,
    file_id_by_path,
    module_ids,
    symbol_id_by_qname,
    handler_ids_by_path,
    table,
    externals,
    alloc,
    sink_registry: SinkRegistry,
    repo_id: int,
):
    # Stream edge/entrypoint rows to the DB in batches instead of accumulating the
    # whole graph's rows in Python lists first — the edge list alone is the largest
    # allocation on a big repo. Newly-created external symbols are flushed just
    # before each edge batch so the edge -> symbol FK always resolves at insert.
    externals_written = 0

    def _flush_new_externals() -> None:
        nonlocal externals_written
        pending = externals.new_rows[externals_written:]
        if pending:
            bulk_insert(session, Symbol, pending)
            externals_written = len(externals.new_rows)

    edge_writer = BatchedWriter(session, Edge, before_flush=_flush_new_externals)
    entrypoint_writer = BatchedWriter(session, Entrypoint)

    for path, x, is_package in extractions:
        resolver = FileResolver(
            x,
            module_ids[path],
            table,
            externals,
            is_package,
            sink_registry=sink_registry,
            local_symbol_ids=handler_ids_by_path.get(path, {}),
        )
        file_id = file_id_by_path[path]
        # registration line -> handler symbol, for routes whose handler is passed by
        # reference (router.get('/x', ctrl.fn)): the resolver emits a callback edge
        # at the same line, and the entrypoint below binds to it instead of falling
        # back to the module symbol.
        callback_handler_by_line: dict[int, int] = {}
        # A file's resolve() can surface the same edge more than once (e.g. two calls
        # to the same target on one line, or a decorator counted as both decorator
        # and call), producing literal duplicate rows that inflate edge/sink counts
        # (#45). Collapse them on the identity tuple; the set is per file, and
        # src_symbol_id is file-local, so this catches every duplicate group.
        seen_edges: set[tuple] = set()
        for edge in resolver.resolve():
            is_call = edge.kind is EdgeKind.CALLS
            sink_id = sink_registry.match(edge.dst_qname, edge.arg_preview) if is_call else None
            source_id = sink_registry.match_source(edge.dst_qname) if is_call else None
            # the specific input identifier (query param, header, flag) rides the
            # first string-literal argument when there is one (#87)
            source_key: str | None = None
            if source_id and edge.arg_preview:
                key = first_string_arg(edge.arg_preview)
                source_key = key[:128] if key else None
            if edge.kind is EdgeKind.PASSED_AS_CALLBACK and edge.dst_symbol_id is not None:
                callback_handler_by_line.setdefault(edge.line, edge.dst_symbol_id)
            dedup_key = (
                edge.kind,
                edge.src_symbol_id,
                edge.dst_qname,
                edge.line,
                sink_id,
                edge.via,
            )
            if dedup_key in seen_edges:
                continue
            seen_edges.add(dedup_key)
            edge_writer.add(
                {
                    "id": alloc.take(Edge),
                    "repo_id": repo_id,
                    "kind": edge.kind,
                    "src_symbol_id": edge.src_symbol_id,
                    "dst_symbol_id": edge.dst_symbol_id,
                    "dst_qname": edge.dst_qname,
                    "src_file_id": file_id,
                    "line": edge.line,
                    "confidence": int(edge.confidence),
                    "arg_preview": edge.arg_preview,
                    "sink_id": sink_id,
                    "source_id": source_id,
                    "source_key": source_key,
                    "via": edge.via,
                }
            )
        per_file = handler_ids_by_path.get(path, {})
        for hint in x.entrypoint_hints:
            handler_q = hint.handler_qualified_name or ""
            # Bind to the handler defined in this file first (so same-package
            # collisions like per-file `func init()` each keep their own row),
            # then the global map.
            symbol_id = per_file.get(handler_q) or symbol_id_by_qname.get(handler_q)
            # Route handler passed by reference (router.get('/x', ctrl.fn)) — the
            # name isn't a symbol in scope, but the resolver bound the callback at
            # the registration line. Bind the route to that real handler instead of
            # the module, so it (not the whole module) is the http_input source.
            if symbol_id is None and hint.span is not None:
                symbol_id = callback_handler_by_line.get(hint.span.start_line)
            symbol_id = symbol_id or module_ids[path]
            entrypoint_writer.add(
                {
                    "id": alloc.take(Entrypoint),
                    "repo_id": repo_id,
                    "kind": hint.kind,
                    "framework": hint.framework,
                    "symbol_id": symbol_id,
                    "route": hint.route,
                    "http_method": ",".join(hint.http_methods) or None,
                    "extra": json.dumps(hint.metadata) if hint.metadata else None,
                }
            )
    edge_writer.flush()  # before_flush writes any remaining new externals first
    _flush_new_externals()  # externals with no trailing edge batch (belt-and-suspenders)
    entrypoint_writer.flush()
    return edge_writer.count, entrypoint_writer.count


def _write_config_entrypoints(
    session,
    root,
    symbol_id_by_qname: dict[str, int],
    table: SymbolTable,
    alloc: IdAllocator,
    incremental: bool,
    repo_id: int,
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
        session.execute(
            delete(Entrypoint).where(
                Entrypoint.repo_id == repo_id, Entrypoint.framework.in_(CONFIG_FRAMEWORKS)
            )
        )

    rows = []
    for hint in scan_config_entrypoints(root):
        symbol_id = bind_handler(hint.handler_ref, symbol_id_by_qname, table.module_symbol_ids)
        if symbol_id is None:  # non-nullable FK: skip unbindable handlers
            continue
        rows.append(
            {
                "id": alloc.take(Entrypoint),
                "repo_id": repo_id,
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


def _heal_dangling_edges(session, table: SymbolTable, new_qnames: set[str], repo_id: int) -> None:
    """Re-bind edges left NULL (degraded on wipe, or targeting a not-yet-existing
    symbol) whose dst_qname now names a freshly-created symbol."""
    dangling = session.execute(
        select(Edge.id, Edge.dst_qname, Edge.confidence).where(
            Edge.repo_id == repo_id,
            Edge.dst_symbol_id.is_(None),
            Edge.dst_qname.in_(new_qnames),
        )
    ).all()
    updates = []
    unresolved = int(Confidence.UNRESOLVED)
    for edge_id, dst_qname, confidence in dangling:
        target = table.by_fqn.get(dst_qname)
        if target is not None:
            # Preserve the edge's original tier. Only an edge that was UNRESOLVED
            # (a project import whose target didn't exist yet) upgrades to IMPORT
            # on heal, matching what a full re-index produces. An EXACT/IMPORT/
            # FUZZY/cha edge whose target was merely wiped and recreated keeps its
            # tier — healing must not silently promote a fuzzy or CHA edge.
            healed = int(Confidence.IMPORT) if confidence == unresolved else confidence
            updates.append({"id": edge_id, "dst_symbol_id": target, "confidence": healed})
    if updates:
        session.execute(update(Edge), updates)


def _gc_orphan_externals(session, repo_id: int) -> None:
    """Drop this repo's external placeholder symbols no edge points at anymore.

    Full re-index never creates them; deleting them keeps an incremental graph
    byte-identical to a full one after a file's last reference disappears. Scoped
    to the repo so another repo's externals are never GC'd in a global DB (#116).
    """
    referenced = select(Edge.dst_symbol_id).where(
        Edge.repo_id == repo_id, Edge.dst_symbol_id.is_not(None)
    )
    session.execute(
        delete(Symbol).where(
            Symbol.repo_id == repo_id,
            Symbol.kind == SymbolKind.EXTERNAL,
            Symbol.id.not_in(referenced),
        )
    )


def _write_detections(
    session, repo, profile: RepoLanguageProfile, frameworks, incremental: bool = False
) -> None:
    # Languages come from the whole-repo walk every run, so they're always replaced.
    session.execute(
        delete(Detection).where(Detection.repo_id == repo.id, Detection.category == "language")
    )
    # Framework detection only saw THIS run's extracted files. On an incremental run
    # a framework whose import evidence lives in an *unchanged* file re-derives at a
    # lower confidence (manifest-only) or not at all — which used to wipe it or
    # degrade it (httpie's argparse vanished; photoprism's gin 0.94 -> 0.8) after any
    # unrelated edit (#38 / F-H12). So merge with the existing rows, keeping the
    # higher confidence per framework and never dropping one; a --full run rebuilds
    # from scratch.
    fresh = {
        fw.name: (
            fw.confidence,
            json.dumps({"language": fw.language, "signals": list(fw.evidence)}),
        )
        for fw in frameworks
    }
    if incremental:
        existing = session.execute(
            select(Detection.name, Detection.confidence, Detection.evidence).where(
                Detection.repo_id == repo.id, Detection.category == "framework"
            )
        ).all()
        for name, confidence, evidence in existing:
            prev = fresh.get(name)
            if prev is None or confidence > prev[0]:  # keep the stronger prior signal
                fresh[name] = (confidence, evidence)
    session.execute(
        delete(Detection).where(Detection.repo_id == repo.id, Detection.category == "framework")
    )
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
            "name": name,
            "version": None,
            "confidence": confidence,
            "evidence": evidence,
        }
        for name, (confidence, evidence) in fresh.items()
    )
    if rows:
        bulk_insert(session, Detection, rows)


def _count_symbols(session, repo_id: int) -> int:
    return (
        session.execute(select(func.count(Symbol.id)).where(Symbol.repo_id == repo_id)).scalar()
        or 0
    )
