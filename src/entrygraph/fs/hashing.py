"""Content hashing and change detection against the DB's file table."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from entrygraph.fs.walker import WalkedFile, content_gate
from entrygraph.parsing.parsers import supported


def hash_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def hash_file(abs_path: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(abs_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _finalize_skip(wf: WalkedFile) -> None:
    """Apply the byte-peek content gate to a to-be-considered file. walk_repo only
    set the cheap gate, so this fills in binary/minified-by-content for the files
    that are actually about to be indexed."""
    if not wf.skip_reason:
        wf.skip_reason = content_gate(wf.abs_path, wf.language, wf.size_bytes)


def _worker_hashes(wf: WalkedFile) -> bool:
    """True if the parse worker will read this file and can hash it there, so the
    diff phase should not read it a second time. False for skipped files and
    recognized-but-not-extracted ones (markdown/toml/...), which the worker never
    reads — those are hashed here."""
    return not wf.skip_reason and wf.language is not None and supported(wf.language)


@dataclass(slots=True)
class FileState:
    content_hash: str
    size_bytes: int
    mtime_ns: int


@dataclass(slots=True)
class Diff:
    added: list[WalkedFile] = field(default_factory=list)
    changed: list[WalkedFile] = field(default_factory=list)
    unchanged: list[WalkedFile] = field(default_factory=list)
    deleted_paths: list[str] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)  # path -> content_hash

    @property
    def to_index(self) -> list[WalkedFile]:
        return [*self.added, *self.changed]


def diff_files(
    walked: list[WalkedFile],
    known: dict[str, FileState],
    *,
    paranoid: bool = False,
) -> Diff:
    """Classify walked files vs the DB's recorded state.

    Fast path: identical size+mtime is assumed unchanged (skips hashing) unless
    ``paranoid``. Otherwise the file is hashed and compared.
    """
    diff = Diff()
    seen: set[str] = set()
    for wf in walked:
        seen.add(wf.path)
        prior = known.get(wf.path)
        if prior is None:
            _finalize_skip(wf)
            # supported+unskipped files are hashed by the parse worker (avoids a
            # second read); everything else the worker won't read is hashed here.
            if not wf.skip_reason and not _worker_hashes(wf):
                diff.hashes[wf.path] = hash_file(wf.abs_path)
            diff.added.append(wf)
            continue
        if not paranoid and prior.size_bytes == wf.size_bytes and prior.mtime_ns == wf.mtime_ns:
            # unchanged fast path: no content read at all (the whole point of the
            # deferred content gate — most files on a warm refresh land here).
            diff.unchanged.append(wf)
            diff.hashes[wf.path] = prior.content_hash
            continue
        _finalize_skip(wf)
        new_hash = hash_file(wf.abs_path) if not wf.skip_reason else prior.content_hash
        diff.hashes[wf.path] = new_hash
        if new_hash == prior.content_hash:
            diff.unchanged.append(wf)
        else:
            diff.changed.append(wf)

    for path in known:
        if path not in seen:
            diff.deleted_paths.append(path)
    return diff
