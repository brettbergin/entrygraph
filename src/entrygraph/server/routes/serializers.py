"""JSON shapes for the read API — the contract the webapp consumes.

Enriched to CLI parity: paths carry per-hop
edge metadata (line, confidence, via, sanitizers) and literal source/sink line
snippets read from the repo checkout (best-effort, path-contained).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def repo_name(root_path: str) -> str:
    # sentinel:// synthetic roots and real paths both reduce to a readable name
    return root_path.rstrip("/").split("/")[-1] or root_path


def symbol_json(s) -> dict[str, Any]:
    return {
        "id": s.id,
        "kind": s.kind,
        "name": s.name,
        "qname": s.qname,
        "file": s.file,
        "line": s.start_line,
        "end_line": s.end_line,
        "signature": s.signature,
        "exported": s.is_exported,
    }


def entrypoint_json(e) -> dict[str, Any]:
    return {
        "id": e.id,
        "kind": e.kind,
        "framework": e.framework,
        "route": e.route,
        "http_method": e.http_method,
        "handler": symbol_json(e.symbol) if e.symbol else None,
    }


def file_json(f) -> dict[str, Any]:
    return {
        "id": f.id,
        "path": f.path,
        "language": f.language,
        "size_bytes": f.size_bytes,
        "skip_reason": f.skip_reason,
    }


def make_line_reader(repo_root: str | None):
    """Best-effort reader of the literal source line at file:line, mirroring the
    CLI's ``_line_reader`` — plus a containment check, since these paths come from
    the DB but the repo may have moved (never read outside the repo root)."""
    if not repo_root:
        return lambda _file, _line: None
    root = Path(repo_root).resolve()
    cache: dict[str, list[str] | None] = {}

    def read(file: str | None, line: int | None) -> str | None:
        if not file or not line or line < 1:
            return None
        if file not in cache:
            try:
                target = (root / file).resolve()
                if not target.is_relative_to(root):
                    cache[file] = None
                else:
                    cache[file] = target.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                cache[file] = None
        lines = cache[file]
        if not lines or line > len(lines):
            return None
        text = lines[line - 1].strip()
        return (text[:399] + "…") if len(text) > 400 else text

    return read


def path_json(p, read_line=None) -> dict[str, Any]:
    read_line = read_line or (lambda _f, _l: None)
    src = p.symbols[0]
    src_line = p.symbols[0].start_line
    sink_edge = p.edges[-1] if p.edges else None
    # the sink call happens in the caller's file (the sink symbol is external)
    sink_file = p.symbols[-2].file if len(p.symbols) >= 2 else None
    return {
        "severity": p.severity,
        "verified": p.taint_verified,
        "min_confidence": p.min_confidence,
        "source_category": p.source_category,
        "source_kind": p.source_kind,
        "source_channel": p.source_channel,
        "source_key": p.source_key,
        "may_continue": p.may_continue,
        "sink_id": sink_edge.sink_id if sink_edge else None,
        "source_snippet": read_line(src.file, src_line),
        "sink_snippet": read_line(sink_file, sink_edge.line if sink_edge else None),
        "hops": [
            {"qname": sym.qname, "name": sym.name, "file": sym.file, "kind": sym.kind}
            for sym in p.symbols
        ],
        "edges": [
            {
                "kind": e.kind,
                "line": e.line,
                "confidence": e.confidence,
                "via": e.via,
                "sink_id": e.sink_id,
                "constant_args": e.constant_args,
            }
            for e in p.edges
        ],
    }
