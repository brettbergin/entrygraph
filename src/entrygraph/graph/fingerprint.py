"""Stable, line-independent fingerprints for source -> sink paths (#116).

A reachability finding must keep the same identity across commits so a diff-aware
gate can tell "this PR introduced a *new* path" from "this path already existed,
just moved". The fingerprint hashes the **semantic shape** of a path — its taint
category, sink, and the ordered chain of symbol qualified names — and deliberately
excludes line numbers, so re-indenting or moving a function leaves it unchanged.
Qualified names are already location-free and canonical (project FQNs, or
``lang:ext.qname`` for externals like ``py:subprocess.run``), so no extra
normalization is needed.

Two variants are produced:

- ``strict``   — the full ordered qname chain. Two paths match only if every hop
  is identical. This is the primary diff key.
- ``endpoint`` — source entrypoint + sink only. A coarser key that survives a
  mid-path refactor, so an interior change doesn't masquerade as a brand-new
  finding; the gate uses it as a fuzzy fallback.

Both are ``blake2b`` digests rendered as 32 hex chars (128-bit), matching the
``Finding.fingerprint`` column width in the findings store.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from entrygraph.results import CallPath

# Bump when the hashed shape changes, so old baselines can't silently mis-match.
_FP_VERSION = b"eg-fp-v1"
_SEP = b"\x00"  # not a legal qname/category/sink-id byte, so fields can't run together
_DIGEST_SIZE = 16  # 128-bit -> 32 hex chars


@dataclass(frozen=True, slots=True)
class PathFingerprint:
    """A path's identity across commits. ``strict`` = full qname chain; ``endpoint``
    = source + sink only (fuzzy fallback for mid-path refactors)."""

    strict: str
    endpoint: str


def _digest(kind: bytes, *parts: str) -> str:
    h = hashlib.blake2b(digest_size=_DIGEST_SIZE)
    h.update(_FP_VERSION)
    h.update(_SEP)
    h.update(kind)
    for part in parts:
        h.update(_SEP)
        h.update(part.encode("utf-8"))
    return h.hexdigest()


def _sink_id(path: CallPath) -> str:
    """The terminal edge's tagged sink id (``py.command-exec.subprocess``), or ""."""
    return (path.edges[-1].sink_id if path.edges else None) or ""


def _source_qname(path: CallPath) -> str:
    return path.symbols[0].qname if path.symbols else ""


def fingerprint(path: CallPath, source_category: str | None = None) -> PathFingerprint:
    """Compute the ``strict`` and ``endpoint`` fingerprints of ``path``.

    ``source_category`` overrides the category recorded on the path (useful when a
    caller enumerated by category but the path carries none); it falls back to
    ``path.source_category`` and then to "".
    """
    category = source_category if source_category is not None else (path.source_category or "")
    sink = _sink_id(path)
    qnames = [sym.qname for sym in path.symbols]
    strict = _digest(b"strict", category, sink, *qnames)
    endpoint = _digest(b"endpoint", category, _source_qname(path), sink)
    return PathFingerprint(strict=strict, endpoint=endpoint)
