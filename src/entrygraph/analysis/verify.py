"""Same-function taint verification for a materialized path (#96 Phase 2).

Applies only when the path's source symbol directly makes the sink call (source
and sink in one function). Reads the file from disk, guards against staleness via
the indexed content hash, and returns a tri-state verdict. Nothing is persisted;
parses are memoized per file within one query via ``FileFactCache``.
"""

from __future__ import annotations

from pathlib import Path

from entrygraph.analysis.facts import extract_function_facts, language_supported
from entrygraph.analysis.reaching import reaches
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


def verify_same_function(
    path,
    source_kind: str,
    source_channel_lines: set[int],
    file_hash: str | None,
    cache: FileFactCache,
) -> bool | None:
    """Tri-state: does a request value reach the sink within the source function?

    Returns None unless the whole path lives in the source symbol's function
    (source symbol is a function/method and directly calls the sink)."""
    source_sym = path.symbols[0]
    sink_caller = path.symbols[-2] if len(path.symbols) >= 2 else None
    if sink_caller is None or source_sym.id != sink_caller.id:
        return None  # not a same-function path
    if source_sym.kind not in ("function", "method"):
        return None
    if source_sym.file is None or not language_supported(_language_of(source_sym)):
        return None

    facts = cache.facts_for(
        source_sym.file,
        _language_of(source_sym),
        file_hash,
        source_sym.start_line,
        source_sym.end_line,
    )
    if facts is None:
        return None

    seed_roots: set[str] = set()
    if source_kind in _HANDLER_KINDS:
        seed_roots |= set(facts.params)

    sink_edge = path.edges[-1]
    sink_line = sink_edge.line
    sink_callee = _callee_name(sink_edge, path.symbols[-1])
    return reaches(facts, seed_roots, source_channel_lines, sink_line, sink_callee)


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
