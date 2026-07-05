"""Findings store + path enumeration for the Continuous Reachability Gate (#116).

Enumerates a checkout's reachable *dangerous* paths (risk-ranked source -> sink),
fingerprints them, and persists baselines / scan runs / findings in the same
(global) database as the graph, keyed by ``repo_id``. Pure query + ORM writes;
no code execution, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from entrygraph.api import CodeGraph
from entrygraph.db import models
from entrygraph.detect.taint import registry_for_repo
from entrygraph.graph.fingerprint import fingerprint
from entrygraph.kinds import Confidence

# Confidence floor names accepted in policy.min_confidence. `unresolved` is
# intentionally the loosest; the gate never *defaults* to it (too noisy).
_CONFIDENCE = {
    "exact": int(Confidence.EXACT),
    "import": int(Confidence.IMPORT),
    "fuzzy": int(Confidence.FUZZY),
    "unresolved": int(Confidence.UNRESOLVED),
}
# Per-category enumeration cap. High enough to capture every real path on normal
# repos while bounding a pathological fan-out.
_MAX_PATHS_PER_CATEGORY = 500


@dataclass(frozen=True, slots=True)
class Policy:
    """Effective gate policy for a repo (a RepoPolicy row, or these defaults)."""

    risk_threshold: float = 0.5  # gate paths whose risk is at/above this
    gated_categories: tuple[str, ...] | None = None  # None = every sink category
    mode: str = "block"  # block|warn
    min_confidence: str = "fuzzy"  # never gate below this resolution tier


@dataclass(frozen=True, slots=True)
class GateFinding:
    """One reachable dangerous path, addressed by its stable fingerprints."""

    strict: str
    endpoint: str
    source_category: str | None
    sink_id: str | None
    sink_category: str | None
    risk: float
    hops: list[dict] = field(default_factory=list)  # [{qname, file, line}]

    def path_json(self) -> str:
        return json.dumps(
            {
                "sink_id": self.sink_id,
                "sink_category": self.sink_category,
                "source_category": self.source_category,
                "risk": self.risk,
                "hops": self.hops,
            }
        )


def _sink_categories(root: str | None) -> list[str]:
    """Every sink category the repo's catalog knows (built-ins + entrygraph.toml)."""
    registry = registry_for_repo(root)
    return sorted({p.category for p in registry.sinks.values()})


def _hops(path) -> list[dict]:
    out: list[dict] = []
    syms = path.symbols
    for i, sym in enumerate(syms):
        # the call into hop i+1 happens at the i-th edge's line, in sym's file
        line = path.edges[i].line if i < len(path.edges) else sym.start_line
        out.append({"qname": sym.qname, "file": sym.file, "line": line})
    return out


def enumerate_findings(graph: CodeGraph, policy: Policy) -> list[GateFinding]:
    """Every reachable dangerous path in the bound repo, deduped by strict
    fingerprint. Enumerates per sink category so each finding carries its category;
    ``source_category='all'`` seeds from every taint source."""
    categories = policy.gated_categories or tuple(_sink_categories(graph.repo_root))
    floor = _CONFIDENCE.get(policy.min_confidence, int(Confidence.FUZZY))
    by_fp: dict[str, GateFinding] = {}
    for category in categories:
        paths = graph.paths(
            source_category="all",
            sink_category=category,
            min_confidence=floor,
            max_paths=_MAX_PATHS_PER_CATEGORY,
        )
        for path in paths:
            fp = fingerprint(path, source_category=path.source_category)
            if fp.strict in by_fp:
                continue
            sink_id = path.edges[-1].sink_id if path.edges else None
            by_fp[fp.strict] = GateFinding(
                strict=fp.strict,
                endpoint=fp.endpoint,
                source_category=path.source_category,
                sink_id=sink_id,
                sink_category=category,
                risk=round(path.risk_score or 0.0, 4),
                hops=_hops(path),
            )
    return list(by_fp.values())


# ---------------- policy ----------------


def get_policy(session: Session, repo_id: int) -> Policy:
    row = session.get(models.RepoPolicy, repo_id)
    if row is None:
        return Policy()
    cats = tuple(json.loads(row.gated_categories)) if row.gated_categories else None
    return Policy(
        risk_threshold=row.risk_threshold,
        gated_categories=cats,
        mode=row.mode,
        min_confidence=row.min_confidence,
    )


# ---------------- baselines ----------------


def save_baseline(
    session: Session,
    repo_id: int,
    findings: list[GateFinding],
    *,
    branch: str = "main",
    commit_sha: str | None = None,
    now: datetime,
) -> int:
    """Replace the repo/branch baseline with ``findings``. Returns the path count."""
    existing = session.execute(
        select(models.Baseline.id).where(
            models.Baseline.repo_id == repo_id, models.Baseline.branch == branch
        )
    ).scalar()
    if existing is not None:
        session.execute(delete(models.Baseline).where(models.Baseline.id == existing))
        session.flush()
    baseline = models.Baseline(
        repo_id=repo_id,
        branch=branch,
        commit_sha=commit_sha,
        created_at=now,
        path_count=len(findings),
    )
    session.add(baseline)
    session.flush()  # assign baseline.id
    session.add_all(
        models.BaselinePath(
            baseline_id=baseline.id,
            fingerprint=f.strict,
            endpoint_fingerprint=f.endpoint,
            source_category=f.source_category,
            sink_id=f.sink_id,
            risk=f.risk,
            path_json=f.path_json(),
        )
        for f in findings
    )
    session.commit()
    return len(findings)


@dataclass(frozen=True, slots=True)
class BaselineView:
    """The fingerprint sets of a stored baseline, for O(1) membership checks."""

    branch: str
    commit_sha: str | None
    strict: frozenset[str]
    endpoint: frozenset[str]

    def __bool__(self) -> bool:
        return bool(self.strict or self.endpoint)


def load_baseline(session: Session, repo_id: int, branch: str = "main") -> BaselineView | None:
    """The newest baseline for a repo/branch, or None if none has been cut."""
    baseline = session.execute(
        select(models.Baseline).where(
            models.Baseline.repo_id == repo_id, models.Baseline.branch == branch
        )
    ).scalar()
    if baseline is None:
        return None
    rows = session.execute(
        select(models.BaselinePath.fingerprint, models.BaselinePath.endpoint_fingerprint).where(
            models.BaselinePath.baseline_id == baseline.id
        )
    ).all()
    return BaselineView(
        branch=branch,
        commit_sha=baseline.commit_sha,
        strict=frozenset(r[0] for r in rows),
        endpoint=frozenset(r[1] for r in rows),
    )


def baseline_findings(session: Session, repo_id: int, branch: str = "main") -> list[GateFinding]:
    """Reconstruct the baseline's accepted paths (with hop detail), e.g. to report
    which ones a PR *fixed*."""
    baseline = session.execute(
        select(models.Baseline.id).where(
            models.Baseline.repo_id == repo_id, models.Baseline.branch == branch
        )
    ).scalar()
    if baseline is None:
        return []
    rows = (
        session.execute(
            select(models.BaselinePath).where(models.BaselinePath.baseline_id == baseline)
        )
        .scalars()
        .all()
    )
    out: list[GateFinding] = []
    for r in rows:
        meta = json.loads(r.path_json) if r.path_json else {}
        out.append(
            GateFinding(
                strict=r.fingerprint,
                endpoint=r.endpoint_fingerprint,
                source_category=r.source_category,
                sink_id=r.sink_id,
                sink_category=meta.get("sink_category"),
                risk=r.risk,
                hops=meta.get("hops", []),
            )
        )
    return out


# ---------------- suppressions ----------------


def active_suppressions(session: Session, repo_id: int, now: datetime) -> frozenset[str]:
    """Fingerprints with a non-expired waiver."""
    rows = session.execute(
        select(models.Suppression.fingerprint, models.Suppression.expires_at).where(
            models.Suppression.repo_id == repo_id
        )
    ).all()
    return frozenset(fp for fp, expires in rows if expires is None or expires > now)


# ---------------- scan runs ----------------


def record_scan(
    session: Session,
    repo_id: int,
    *,
    status: str,
    findings: list[tuple[GateFinding, str]],  # (finding, status)
    head_sha: str | None = None,
    base_sha: str | None = None,
    pr_number: int | None = None,
    duration_ms: int | None = None,
    now: datetime,
) -> int:
    """Persist a ScanRun plus its classified findings; return the scan run id."""
    counts = {"new": 0, "known": 0, "fixed": 0, "suppressed": 0}
    for _f, st in findings:
        counts[st] = counts.get(st, 0) + 1
    scan = models.ScanRun(
        repo_id=repo_id,
        pr_number=pr_number,
        head_sha=head_sha,
        base_sha=base_sha,
        status=status,
        new_count=counts["new"],
        known_count=counts["known"],
        fixed_count=counts["fixed"],
        suppressed_count=counts["suppressed"],
        duration_ms=duration_ms,
        created_at=now,
    )
    session.add(scan)
    session.flush()
    session.add_all(
        models.Finding(
            scan_run_id=scan.id,
            fingerprint=f.strict,
            endpoint_fingerprint=f.endpoint,
            source_category=f.source_category,
            sink_id=f.sink_id,
            risk=f.risk,
            status=st,
            path_json=f.path_json(),
        )
        for f, st in findings
    )
    session.commit()
    return scan.id
