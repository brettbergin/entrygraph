"""Fetch a git remote into a local checkout so ``index`` can accept a URL.

``entrygraph index <path>`` normally walks a local directory. This module lets
the same positional argument be a **git URL** (``https://github.com/org/repo``,
``git@github.com:org/repo.git``, ``file:///…``): :func:`is_git_url` classifies
the argument and :func:`prepare_source` clones it (or reuses a prior checkout)
and yields the directory the rest of the pipeline should index.

Indexing never executes the cloned code — it is tree-sitter parsing only — so
the only hardening that matters is the *clone* step: no repo-provided hooks, no
interactive credential prompt, an argv list (never ``shell=True``) so the URL
can't inject a shell, and a wall-clock timeout.
"""

from __future__ import annotations

import os
import re
import shutil

# subprocess is used only for hardened, argv-list git invocations (see _run_git).
import subprocess  # nosec B404
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from entrygraph.errors import GitCloneError

# Schemes we treat as a git remote. `file` is included so local bare repos can be
# cloned (git supports it) — which also makes the clone path testable offline.
_SCHEME_RE = re.compile(r"^(https?|git|ssh|file)://", re.IGNORECASE)
# scp-style syntax: user@host:org/repo(.git). The `@` before the `:` is what
# separates this from a Windows drive path (`C:\…`) or a plain `dir:sub` name.
_SCP_RE = re.compile(r"^[\w.-]+@[\w.-]+:.+$")

CLONE_SUBDIR = Path(".entrygraph") / "clones"


def is_git_url(candidate: str) -> bool:
    """True if ``candidate`` should be cloned rather than read as a local path.

    Conservative by design: a path that exists on disk always wins (even if it
    superficially looks URL-ish), and a bare ``org/repo`` shorthand is *not*
    auto-expanded — a scheme or scp-style ``user@host:path`` is required — so an
    ordinary relative path is never mistaken for a remote.
    """
    if not candidate:
        return False
    if Path(candidate).exists():
        return False
    return bool(_SCHEME_RE.match(candidate) or _SCP_RE.match(candidate))


@dataclass(slots=True)
class CloneResult:
    """The directory to index, plus how it was obtained.

    ``url`` is ``None`` for a local path (no clone happened); ``ephemeral`` marks
    a temp checkout that is deleted when :func:`prepare_source` exits.
    """

    root: Path
    url: str | None
    ref: str | None
    ephemeral: bool


def _parse_url(url: str) -> tuple[str, list[str]]:
    """(host, path-segments) for a git URL, ``.git`` suffix stripped."""
    scp = _SCP_RE.match(url)
    if scp and "://" not in url:
        host, _, path = url.partition("@")[2].partition(":")
    else:
        parts = urlsplit(url)
        host = parts.hostname or "local"
        path = parts.path
    segments = [seg for seg in path.strip("/").split("/") if seg]
    if segments and segments[-1].endswith(".git"):
        segments[-1] = segments[-1][:-4]
    return host, segments


def repo_name(url: str) -> str:
    """Last path segment of a git URL (``semgrep`` for …/semgrep.git)."""
    _, segments = _parse_url(url)
    return segments[-1] if segments else "repo"


def default_clone_dir(url: str, base: Path | None = None) -> Path:
    """Stable, reused checkout location: ``<base>/.entrygraph/clones/<host>/<org>/<repo>``."""
    host, segments = _parse_url(url)
    sub = Path(host, *segments) if segments else Path(host)
    return (base or Path.cwd()) / CLONE_SUBDIR / sub


def clone_destination(url: str, root: Path) -> Path:
    """Checkout location directly under an explicit workspace root:
    ``<root>/<host>/<org>/<repo>`` (no ``.entrygraph/clones`` nesting — the
    server's EG_CLONE_DIR *is* the clones directory)."""
    host, segments = _parse_url(url)
    sub = Path(host, *segments) if segments else Path(host)
    return root / sub


def _git_exe() -> str:
    git = shutil.which("git")
    if git is None:
        raise GitCloneError("git executable not found on PATH; install git to index a URL")
    return git


def _run_git(cmd: list[str], *, timeout: int, action: str) -> None:
    """Run a git subprocess with credential prompts disabled; raise on failure.

    argv list, no shell, absolute git path — so a hostile URL can neither inject a
    shell nor resolve a rogue ``git`` from the cwd. GIT_TERMINAL_PROMPT/ASKPASS
    make a private or mistyped URL fail fast instead of blocking on a prompt.
    """
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
        "GCM_INTERACTIVE": "never",
    }
    try:
        # Hardened: fixed argv (no shell), git path resolved via shutil.which.
        proc = subprocess.run(  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:  # git vanished between which() and run()
        raise GitCloneError("git executable not found; install git to index a URL") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitCloneError(f"git {action} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " / ".join(detail[-3:]) if detail else f"exit {proc.returncode}"
        raise GitCloneError(f"git {action} failed: {tail}")


def _base(depth: int) -> list[str]:
    """Git argv prefix: hooks disabled, no tags, shallow unless depth==0 (full)."""
    argv = [_git_exe(), "-c", "core.hooksPath=/dev/null", "clone", "--no-tags", "--single-branch"]
    if depth and depth > 0:
        argv += ["--depth", str(depth)]
    return argv


def _clone(url: str, dest: Path, *, ref: str | None, depth: int, timeout: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ref:
        # First try ref as a branch/tag (the common case, and shallow-cloneable).
        try:
            _run_git(
                [*_base(depth), "--branch", ref, "--", url, str(dest)],
                timeout=timeout,
                action=f"clone {url}@{ref}",
            )
            return
        except GitCloneError:
            # ref may be a commit SHA, which --branch can't name: clone the default
            # branch, then fetch and check out the specific commit.
            shutil.rmtree(dest, ignore_errors=True)
            _clone_then_fetch(url, dest, ref=ref, depth=depth, timeout=timeout)
            return
    _run_git([*_base(depth), "--", url, str(dest)], timeout=timeout, action=f"clone {url}")


def _clone_then_fetch(url: str, dest: Path, *, ref: str, depth: int, timeout: int) -> None:
    git = _git_exe()
    _run_git(
        [git, "-c", "core.hooksPath=/dev/null", "clone", "--no-tags", "--", url, str(dest)],
        timeout=timeout,
        action=f"clone {url}",
    )
    _fetch_ref(dest, ref=ref, depth=depth, timeout=timeout)


def _fetch_ref(dest: Path, *, ref: str, depth: int, timeout: int) -> None:
    """Fetch ``ref`` (branch/tag/SHA) into an existing checkout and hard-reset to it."""
    git = _git_exe()
    depth_args = ["--depth", str(depth)] if depth and depth > 0 else []
    _run_git(
        [git, "-C", str(dest), "fetch", "--no-tags", *depth_args, "origin", ref],
        timeout=timeout,
        action=f"fetch {ref}",
    )
    _run_git(
        [git, "-c", "core.hooksPath=/dev/null", "-C", str(dest), "reset", "--hard", "FETCH_HEAD"],
        timeout=timeout,
        action="reset to fetched ref",
    )


def _update(dest: Path, *, ref: str | None, depth: int, timeout: int) -> None:
    """Refresh an existing checkout to the latest ``ref`` (or remote HEAD)."""
    _fetch_ref(dest, ref=ref or "HEAD", depth=depth, timeout=timeout)


def _origin_url(dest: Path) -> str | None:
    """The existing checkout's ``origin`` remote URL, or None if unset/unreadable."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell, git path from which
            [git, "-C", str(dest), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None


def _same_remote(requested: str, existing: str | None) -> bool:
    """True if two git URLs point at the same repo, ignoring scheme, ``.git``
    suffix, and https-vs-scp form (compared by host + path segments)."""
    if existing is None:
        return False
    return _parse_url(requested) == _parse_url(existing)


@contextmanager
def prepare_source(
    path_or_url: str,
    *,
    ref: str | None = None,
    depth: int = 1,
    clone_dir: str | Path | None = None,
    ephemeral: bool = False,
    timeout: int = 600,
) -> Iterator[CloneResult]:
    """Yield the directory to index for a local path or a git URL.

    A local path is yielded unchanged (no clone). A URL is cloned into a
    persistent, reused workspace (``./.entrygraph/clones/…`` by default, so
    downstream ``paths`` snippet reads and incremental re-index keep working), or
    into a self-deleting temp dir when ``ephemeral`` is set. Re-indexing an
    existing persistent checkout fetches and resets instead of re-cloning.
    """
    if not is_git_url(path_or_url):
        yield CloneResult(root=Path(path_or_url).resolve(), url=None, ref=None, ephemeral=False)
        return

    url = path_or_url
    if ephemeral:
        workspace = tempfile.mkdtemp(prefix="entrygraph-clone-")
        dest = Path(workspace) / repo_name(url)
        try:
            _clone(url, dest, ref=ref, depth=depth, timeout=timeout)
            yield CloneResult(root=dest.resolve(), url=url, ref=ref, ephemeral=True)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        return

    dest = Path(clone_dir) if clone_dir else default_clone_dir(url)
    if (dest / ".git").exists():
        # Refuse to reuse a checkout of a *different* repo — otherwise we would fetch
        # and index that repo while reporting success for the requested URL (a silent
        # wrong-repo scan). Verify the origin remote matches before updating. #116 QA
        origin = _origin_url(dest)
        if not _same_remote(url, origin):
            raise GitCloneError(
                f"{dest} already holds a checkout of a different repository "
                f"({origin or 'unknown remote'}); refusing to reuse it for {url}. "
                "Pass a fresh --clone-dir or remove that directory."
            )
        _update(dest, ref=ref, depth=depth, timeout=timeout)
    else:
        _clone(url, dest, ref=ref, depth=depth, timeout=timeout)
    yield CloneResult(root=dest.resolve(), url=url, ref=ref, ephemeral=False)
