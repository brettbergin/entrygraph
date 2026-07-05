"""Sentinel scan worker (#126, milestone 2).

Runs one enqueued PR scan: mint an installation token, fetch the head checkout,
index it into an *ephemeral* graph DB, diff against the central baseline via the
existing gate engine, persist the scan + findings to the central store, and post
a GitHub Check Run with the new/known/fixed/suppressed verdict.

The head fetch is the only networked step and is hidden behind the
:class:`RepoFetcher` protocol, so the whole orchestration is testable on a local
checkout with a mocked GitHub client — no Redis, no live GitHub. The arq/Redis
job wiring lives in :mod:`entrygraph.sentinel.queue` and just calls
:func:`run_scan`.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from entrygraph.api import CodeGraph
from entrygraph.gate import sarif as sarif_mod
from entrygraph.gate import store as gate_store
from entrygraph.gate.engine import GateResult, run_gate
from entrygraph.sentinel import store
from entrygraph.sentinel.github import GitHubApp
from entrygraph.sentinel.webhook import ScanRequest

_CHECK_RUN_NAME = "entrygraph reachability gate"

# gate status -> Check Run conclusion. `warn` mode and a missing baseline are
# informational (neutral), never a red X; only a `block`-mode fail is a failure.
_CONCLUSION = {
    "passed": "success",
    "failed": "failure",
    "warned": "neutral",
    "no-baseline": "neutral",
}


class RepoFetcher(Protocol):
    """Materializes a repo's head commit as a working tree at ``dest``."""

    def fetch(self, *, clone_url: str, head_sha: str, token: str, dest: Path) -> None: ...


class DulwichFetcher:
    """Runtime fetcher: shallow-clone the installation-granted repo over HTTPS with
    the installation token, then check out the PR head. Pure-Python (dulwich), no
    shelling out. Only ever fetches the ``clone_url`` GitHub gave us for a granted
    repo — never a user-supplied URL."""

    def fetch(self, *, clone_url: str, head_sha: str, token: str, dest: Path) -> None:
        from dulwich import porcelain

        # x-access-token is GitHub's documented Basic-auth username for App tokens
        authed = clone_url.replace("https://", f"https://x-access-token:{token}@", 1)
        repo = porcelain.clone(authed, target=str(dest), checkout=False)
        try:
            porcelain.reset(repo, mode="hard", treeish=head_sha.encode())
        finally:
            repo.close()


@dataclass(frozen=True, slots=True)
class CheckRunSpec:
    """The Check Run fields derived from a gate result (pure, testable)."""

    name: str
    conclusion: str
    title: str
    summary: str


def build_check_run(result: GateResult) -> CheckRunSpec:
    """Map a :class:`GateResult` to a Check Run. The summary breaks down the
    counts so it matches the CLI gate exactly."""
    conclusion = _CONCLUSION.get(result.status, "neutral")
    if result.status == "no-baseline":
        title = "No baseline — nothing gated"
    elif result.gating:
        verb = "would gate" if result.mode == "warn" else "gated"
        title = f"{len(result.gating)} new reachable path(s) {verb}"
    else:
        title = "No new reachable dangerous paths"
    summary = (
        f"**{result.status.upper()}** ({result.mode} mode)\n\n"
        f"- new: {len(result.new)}\n"
        f"- known: {len(result.known)}\n"
        f"- fixed: {len(result.fixed)}\n"
        f"- suppressed: {len(result.suppressed)}\n"
    )
    if result.gating:
        summary += "\n**Gating paths**\n"
        for f in result.gating[:20]:
            summary += f"- `{f.sink_id or f.sink_category}` (risk {f.risk:.2f})\n"
    return CheckRunSpec(name=_CHECK_RUN_NAME, conclusion=conclusion, title=title, summary=summary)


# A hard cap on the fetched working tree, since Sentinel indexes untrusted PR
# code. Bounds the parse/index cost of a pathological or hostile repo; entrygraph
# never executes the code, so this is a resource guard, not a safety boundary.
_DEFAULT_MAX_REPO_BYTES = 512 * 1024 * 1024  # 512 MiB


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    result: GateResult | None
    check_run_id: int | None
    sarif_id: str | None = None
    skipped_reason: str | None = None


def _tree_size_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def run_scan(
    payload: dict,
    *,
    github: GitHubApp,
    fetcher: RepoFetcher,
    session_factory,
    now: datetime,
    max_repo_bytes: int = _DEFAULT_MAX_REPO_BYTES,
) -> ScanOutcome:
    """Execute one PR scan end to end. Returns the gate result and the posted
    Check Run id.

    The graph is indexed into a throwaway SQLite DB in a temp dir and discarded;
    only the baseline diff and findings persist, in the central store keyed by the
    installation-scoped repo id. A working tree over ``max_repo_bytes`` is skipped
    with a neutral Check Run rather than indexed."""
    request = ScanRequest(**payload)
    token = github.installation_token(request.installation_id, now=now).token

    with tempfile.TemporaryDirectory(prefix="sentinel-scan-") as tmp:
        head_dir = Path(tmp) / "head"
        head_dir.mkdir()
        fetcher.fetch(
            clone_url=request.repo_clone_url,
            head_sha=request.head_sha,
            token=token,
            dest=head_dir,
        )
        if _tree_size_bytes(head_dir) > max_repo_bytes:
            check_run_id = github.create_check_run(
                token=token,
                repo_full_name=request.repo_full_name,
                head_sha=request.head_sha,
                name=_CHECK_RUN_NAME,
                conclusion="neutral",
                title="Repository too large to scan",
                summary=f"The checkout exceeds the {max_repo_bytes // (1024 * 1024)} MiB "
                "scan cap; skipped.",
            )
            return ScanOutcome(result=None, check_run_id=check_run_id, skipped_reason="too_large")
        graph = CodeGraph.index(head_dir, db=Path(tmp) / "scan.db")
        try:
            with session_factory() as session:
                owner = request.repo_full_name.split("/", 1)[0]
                store.ensure_installation(session, request.installation_id, owner, now=now)
                repo_id = store.resolve_repo(
                    session, request.installation_id, request.repo_full_name, now=now
                )
                # run_gate resolves the effective RepoPolicy from the store itself
                result = run_gate(
                    graph,
                    session,
                    repo_id,
                    branch=request.base_ref,
                    head_sha=request.head_sha,
                    base_sha=request.base_sha,
                    pr_number=request.pr_number,
                    now=now,
                )
        finally:
            graph.close()

    spec = build_check_run(result)
    check_run_id = github.create_check_run(
        token=token,
        repo_full_name=request.repo_full_name,
        head_sha=request.head_sha,
        name=spec.name,
        conclusion=spec.conclusion,
        title=spec.title,
        summary=spec.summary,
    )

    # publish the current reachable findings to code scanning; the stable
    # partialFingerprints let GitHub track each finding across pushes. Best-effort:
    # a repo with code scanning disabled just gets None back.
    from entrygraph import __version__

    report = sarif_mod.to_sarif(result.new + result.known, threshold=0.5, tool_version=__version__)
    sarif_id = github.upload_sarif(
        token=token,
        repo_full_name=request.repo_full_name,
        commit_sha=request.head_sha,
        ref=f"refs/pull/{request.pr_number}/head",
        sarif=report,
    )
    return ScanOutcome(result=result, check_run_id=check_run_id, sarif_id=sarif_id)


@dataclass(frozen=True, slots=True)
class RefreshRequest:
    """A push to the protected default branch: refresh the repo's baseline."""

    installation_id: int
    repo_full_name: str
    repo_clone_url: str
    branch: str
    head_sha: str


def refresh_baseline(
    payload: dict,
    *,
    github: GitHubApp,
    fetcher: RepoFetcher,
    session_factory,
    now: datetime,
) -> int:
    """Re-cut the repo's baseline from the just-merged default-branch head.

    Baselines only ever move forward from the protected default branch (never a PR
    head), so a PR can't poison what it is measured against. Returns the new
    baseline's path count."""
    request = RefreshRequest(**payload)
    token = github.installation_token(request.installation_id, now=now).token

    with tempfile.TemporaryDirectory(prefix="sentinel-baseline-") as tmp:
        head_dir = Path(tmp) / "head"
        head_dir.mkdir()
        fetcher.fetch(
            clone_url=request.repo_clone_url,
            head_sha=request.head_sha,
            token=token,
            dest=head_dir,
        )
        graph = CodeGraph.index(head_dir, db=Path(tmp) / "baseline.db")
        try:
            with session_factory() as session:
                owner = request.repo_full_name.split("/", 1)[0]
                store.ensure_installation(session, request.installation_id, owner, now=now)
                repo_id = store.resolve_repo(
                    session, request.installation_id, request.repo_full_name, now=now
                )
                policy = gate_store.get_policy(session, repo_id)
                findings = gate_store.enumerate_findings(graph, policy)
                return gate_store.save_baseline(
                    session,
                    repo_id,
                    findings,
                    branch=request.branch,
                    commit_sha=request.head_sha,
                    now=now,
                )
        finally:
            graph.close()
