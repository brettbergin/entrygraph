"""Taint verification for a materialized path (#96 Phase 2/3).

Phase 2 checks the same-function case (source directly calls the sink); Phase 3
extends it across a bounded number of call hops. Reads the files from disk,
guards against staleness via the indexed content hash, and returns a tri-state
verdict. Nothing is persisted; parses are memoized per file within one query.
"""

from __future__ import annotations

from pathlib import Path

from entrygraph.analysis.facts import extract_function_facts, language_supported
from entrygraph.analysis.summaries import verify_interprocedural
from entrygraph.fs.hashing import hash_bytes

# handler-tier source kinds whose parameters seed taint (a request/argv handler)
_HANDLER_KINDS = frozenset({"handler", "handler_params"})


class FileFactCache:
    """Per-query cache of (function facts) keyed by (path, start, end), with a
    content-hash staleness guard so a file edited since indexing yields None."""

    def __init__(self, repo_root: str | None) -> None:
        self._root = Path(repo_root) if repo_root else None
        self._bytes: dict[str, bytes | None] = {}

    def _read(self, rel_path: str, expected_hash: str | None) -> bytes | None:
        if self._root is None:
            return None
        if rel_path not in self._bytes:
            try:
                data = (self._root / rel_path).read_bytes()
            except OSError:
                data = None
            # staleness guard: on-disk content must match what was indexed
            if data is not None and expected_hash and hash_bytes(data) != expected_hash:
                data = None
            self._bytes[rel_path] = data
        return self._bytes[rel_path]

    def facts_for(
        self,
        rel_path: str,
        language: str | None,
        expected_hash: str | None,
        start_line: int,
        end_line: int,
    ):
        data = self._read(rel_path, expected_hash)
        if data is None:
            return None
        return extract_function_facts(language, data, start_line, end_line)


def verify_path(
    path,
    source_kind: str,
    source_channel_lines: set[int],
    file_hashes: dict[str, str],
    cache: FileFactCache,
    hop_limit: int = 5,
) -> bool | None:
    """Tri-state: does a request value reach the sink across the path's functions?

    Covers the same-function case (0 interior hops) and up to ``hop_limit``
    interior call hops. Returns None whenever the path can't be safely analyzed
    (unsupported language, stale file, too deep, non-positional-arg mapping)."""
    functions = path.symbols[:-1]  # the last symbol is the external sink
    if not functions:
        return None
    # every function on the path must be a supported, resolvable definition
    facts_list = []
    languages: list[str | None] = []
    is_method: list[bool] = []
    for sym in functions:
        lang = _language_of(sym)
        if sym.kind not in ("function", "method") or sym.file is None:
            return None
        if not language_supported(lang):
            return None
        facts = cache.facts_for(
            sym.file, lang, file_hashes.get(sym.file), sym.start_line, sym.end_line
        )
        if facts is None:
            return None
        facts_list.append(facts)
        languages.append(lang)
        is_method.append(sym.kind == "method")

    seed_roots: set[str] = set()
    if source_kind in _HANDLER_KINDS:
        seed_roots |= set(facts_list[0].params)
    # A parameter-declarator accessor (FastAPI `q: str = Query(...)`, etc.) sits in
    # the handler's signature, so its tainted value is a *parameter*, not a
    # body-local assignment that `source_channel_lines` could seed. Signature
    # default-value calls aren't captured as body facts, so any accessor line
    # before the first body statement is a declarator — seed the params so the
    # request value is tracked instead of wrongly refuted (#134).
    head = functions[0]
    body_start = min((f.line for f in facts_list[0].facts), default=head.start_line + 1)
    if source_channel_lines and any(line < body_start for line in source_channel_lines):
        seed_roots |= set(facts_list[0].params)

    edge_lines = [e.line for e in path.edges]  # one per hop; last is the sink call
    sink_callee = _callee_name(path.edges[-1], path.symbols[-1])
    return verify_interprocedural(
        symbols=path.symbols,
        edge_lines=edge_lines,
        seed_roots=seed_roots,
        source_lines=source_channel_lines,
        languages=languages,
        is_method=is_method,
        facts_list=facts_list,
        sink_callee=sink_callee,
        hop_limit=hop_limit,
    )


def _language_of(symbol) -> str | None:
    if symbol.file is None:
        return None
    from entrygraph.fs.lang import detect_language

    return detect_language(symbol.file)


def _callee_name(sink_edge, sink_symbol) -> str:
    # the sink symbol qname is like "py:subprocess.run" / "js:child_process.exec"
    qname = sink_symbol.qname
    body = qname.split(":", 1)[-1]
    return body.rsplit(".", 1)[-1]
