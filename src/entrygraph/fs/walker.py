"""Repository file walk with pruning, .gitignore support, and content gates.

Order of gates (cheapest first):
1. hard-pruned directory names (checked before pathspec — pruning node_modules
   at the directory level is the single biggest walk speedup),
2. .gitignore rules (root + nested, via pathspec),
3. per-file gates: size cap, NUL-byte binary sniff, minified-JS heuristics.

Every skipped-but-recognized file is reported with a reason so "why isn't my
file indexed" is always answerable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pathspec

from entrygraph.fs.lang import RepoLanguageProfile, detect_language
from entrygraph.fs.testfiles import is_test_path

PRUNED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "bower_components",
        ".venv",
        "venv",
        ".tox",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "site-packages",
        ".eggs",
        "vendor",
        "third_party",
        "dist",
        "build",
        "target",
        "out",
        ".next",
        ".nuxt",
        ".gradle",
        ".idea",
        ".vscode",
        "Pods",
        "DerivedData",
    }
)

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
_MINIFIED_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".map")


@dataclass(slots=True)
class WalkedFile:
    path: str  # repo-relative, posix separators
    abs_path: str
    language: str | None
    size_bytes: int
    mtime_ns: int
    skip_reason: str | None = None  # test | too_large | binary | minified | None


def _collect_gitignores(root: Path) -> list[Path]:
    """`.gitignore` paths found via a pruned walk.

    NOT ``root.rglob(".gitignore")`` — that recursively descends into ``.git``,
    ``node_modules``, ``vendor`` etc. (the huge trees the indexer prunes) on every
    run, often costing more than the real walk. Skipping those dirs is also more
    correct: a ``.gitignore`` inside an un-indexed subtree must not contribute
    repo-wide patterns."""
    found: list[Path] = []
    stack = [str(root)]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in PRUNED_DIRS and not entry.name.startswith(".git"):
                        stack.append(entry.path)
                elif entry.name == ".gitignore" and entry.is_file(follow_symlinks=False):
                    found.append(Path(entry.path))
            except OSError:
                continue
    return found


def _load_gitignore(root: Path) -> pathspec.GitIgnoreSpec | None:
    patterns: list[str] = []
    for ignore_file in sorted(_collect_gitignores(root)):
        try:
            rel_dir = ignore_file.parent.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - symlink escape
            continue
        prefix = "" if rel_dir == "." else rel_dir + "/"
        try:
            lines = ignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if prefix:
                # scope nested .gitignore patterns to their directory
                negate = stripped.startswith("!")
                body = stripped[1:] if negate else stripped
                anchored = body.lstrip("/")
                scoped = f"{prefix}{anchored}" if body.startswith("/") else f"{prefix}**/{anchored}"
                patterns.append(("!" if negate else "") + scoped)
            else:
                patterns.append(stripped)
    if not patterns:
        return None
    return pathspec.GitIgnoreSpec.from_lines(patterns)


def cheap_gate(name: str, size_bytes: int) -> str | None:
    """Skip reasons decidable from stat + filename alone — no file read."""
    if size_bytes > MAX_FILE_BYTES:
        return "too_large"
    if name.endswith(_MINIFIED_SUFFIXES):
        return "minified"
    return None


def content_gate(abs_path: str, language: str | None, size_bytes: int) -> str | None:
    """Skip reasons requiring a peek at the bytes (binary / minified-by-content).

    Split from :func:`cheap_gate` so this 8 KiB read runs only for files that are
    actually going to be indexed (added/changed), not for every unchanged file on
    every walk. Assumes the cheap gate already passed."""
    try:
        with open(abs_path, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return "unreadable"
    if b"\x00" in head:
        return "binary"
    if language in ("javascript", "typescript", "tsx"):
        lines = head.splitlines()
        if any(len(line) > 5000 for line in lines):
            return "minified"
        if size_bytes > 50 * 1024 and lines and (len(head) / max(len(lines), 1)) > 250:
            return "minified"
    return None


def walk_repo(
    root: str | Path, *, include_tests: bool = False
) -> tuple[list[WalkedFile], RepoLanguageProfile]:
    """Walk a repository, returning candidate files and a language profile.

    Files in unrecognized languages are omitted; recognized-but-gated files are
    included with ``skip_reason`` set so they can be recorded in the DB. Test
    files are gated (``skip_reason="test"``) unless ``include_tests`` is set.
    """
    root = Path(root).resolve()
    spec = _load_gitignore(root)
    profile = RepoLanguageProfile()
    results: list[WalkedFile] = []

    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if name in PRUNED_DIRS or name.startswith(".git"):
                    continue
                rel = os.path.relpath(entry.path, root).replace(os.sep, "/")
                if spec and spec.match_file(rel + "/"):
                    continue
                stack.append(entry.path)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            rel = os.path.relpath(entry.path, root).replace(os.sep, "/")
            if spec and spec.match_file(rel):
                continue

            language = detect_language(rel)
            if language is None:
                # one cheap read for shebang sniffing of extensionless files
                if "." not in name:
                    try:
                        with open(entry.path, "rb") as fh:
                            language = detect_language(rel, fh.readline(256))
                    except OSError:
                        continue
                if language is None:
                    continue

            stat = entry.stat(follow_symlinks=False)
            profile.add(language, stat.st_size)
            results.append(
                WalkedFile(
                    path=rel,
                    abs_path=entry.path,
                    language=language,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    # only the cheap (no-read) gates here; the byte-peek content
                    # gate is deferred to the diff phase so unchanged files aren't
                    # opened on every walk (see fs.hashing.diff_files).
                    skip_reason=(
                        "test"
                        if not include_tests and is_test_path(rel, language)
                        else cheap_gate(name, stat.st_size)
                    ),
                )
            )

    results.sort(key=lambda f: f.path)
    return results, profile
