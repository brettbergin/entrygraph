"""The gate engine: diff a checkout's dangerous paths against a baseline (#116).

Classifies every currently-reachable dangerous path as **new**, **known**,
**fixed**, or **suppressed**, applies the repo policy, and produces a CI-friendly
verdict. A path is *known* when its strict fingerprint matches the baseline, or —
the fuzzy fallback — its endpoint fingerprint does (so a mid-path refactor of an
existing finding isn't reported as new). Only *new* paths at or above the risk
threshold gate the build, and only in ``block`` mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from entrygraph.api import CodeGraph
from entrygraph.gate import store
from entrygraph.gate.store import GateFinding, Policy


@dataclass(frozen=True, slots=True)
class GateResult:
    """The outcome of one gate run."""

    passed: bool
    mode: str  # block|warn
    has_baseline: bool
    new: list[GateFinding] = field(default_factory=list)
    known: list[GateFinding] = field(default_factory=list)
    fixed: list[GateFinding] = field(default_factory=list)
    suppressed: list[GateFinding] = field(default_factory=list)
    gating: list[GateFinding] = field(default_factory=list)  # new paths that drove a fail
    scan_id: int | None = None

    @property
    def status(self) -> str:
        if not self.has_baseline:
            return "no-baseline"
        if self.gating:
            return "failed" if self.mode == "block" else "warned"
        return "passed"

    @property
    def exit_code(self) -> int:
        """0 unless new gated paths were found in block mode."""
        return 1 if (self.gating and self.mode == "block") else 0


def run_gate(
    graph: CodeGraph,
    session,
    repo_id: int,
    *,
    policy: Policy | None = None,
    branch: str = "main",
    head_sha: str | None = None,
    base_sha: str | None = None,
    pr_number: int | None = None,
    now: datetime,
    persist: bool = True,
) -> GateResult:
    """Enumerate the bound repo's dangerous paths, diff against the ``branch``
    baseline, apply policy, and (optionally) persist a scan run."""
    policy = policy or store.get_policy(session, repo_id)
    head = store.enumerate_findings(graph, policy)
    baseline = store.load_baseline(session, repo_id, branch)
    suppressed_fps = store.active_suppressions(session, repo_id, now)

    new: list[GateFinding] = []
    known: list[GateFinding] = []
    suppressed: list[GateFinding] = []
    classified: list[tuple[GateFinding, str]] = []
    for f in head:
        if f.strict in suppressed_fps:
            status = "suppressed"
            suppressed.append(f)
        elif baseline and (f.strict in baseline.strict or f.endpoint in baseline.endpoint):
            status = "known"
            known.append(f)
        else:
            status = "new"
            new.append(f)
        classified.append((f, status))

    # fixed: baseline paths no longer reachable on head
    fixed: list[GateFinding] = []
    if baseline:
        head_strict = {f.strict for f in head}
        gone = baseline.strict - head_strict
        if gone:
            fixed = [
                bf for bf in store.baseline_findings(session, repo_id, branch) if bf.strict in gone
            ]

    # a new path gates only when it clears the risk threshold. Category is already
    # constrained by enumeration (policy.gated_categories drove it), so risk is the
    # remaining knob. With no baseline we can't distinguish new from pre-existing,
    # so nothing gates — the caller is told to cut a baseline first.
    gating = [f for f in new if f.risk >= policy.risk_threshold] if baseline is not None else []
    passed = not (gating and policy.mode == "block")

    result = GateResult(
        passed=passed,
        mode=policy.mode,
        has_baseline=baseline is not None,
        new=new,
        known=known,
        fixed=fixed,
        suppressed=suppressed,
        gating=gating,
    )

    if persist:
        classified.extend((f, "fixed") for f in fixed)
        scan_id = store.record_scan(
            session,
            repo_id,
            status=result.status,
            findings=classified,
            head_sha=head_sha,
            base_sha=base_sha,
            pr_number=pr_number,
            now=now,
        )
        result = GateResult(
            passed=passed,
            mode=policy.mode,
            has_baseline=baseline is not None,
            new=new,
            known=known,
            fixed=fixed,
            suppressed=suppressed,
            gating=gating,
            scan_id=scan_id,
        )
    return result
