"""Content hashing and change detection against the DB's file table."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from entrygraph.fs.walker import WalkedFile


def hash_file(abs_path: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(abs_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
            if not wf.skip_reason:
                diff.hashes[wf.path] = hash_file(wf.abs_path)
            diff.added.append(wf)
            continue
        if not paranoid and prior.size_bytes == wf.size_bytes and prior.mtime_ns == wf.mtime_ns:
            diff.unchanged.append(wf)
            diff.hashes[wf.path] = prior.content_hash
            continue
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
