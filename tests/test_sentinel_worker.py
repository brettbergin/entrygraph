"""Sentinel worker: check-run mapping + end-to-end scan (#126 M2)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from entrygraph.db import models
from entrygraph.gate.engine import GateResult
from entrygraph.gate.store import GateFinding, save_baseline
from entrygraph.sentinel import store
from entrygraph.sentinel.github import InstallationToken
from entrygraph.sentinel.worker import build_check_run, run_scan

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


def _finding(risk=0.9):
    return GateFinding(
        strict="fp",
        endpoint="ep",
        source_category="http_input",
        sink_id="py.command-exec.subprocess",
        sink_category="command_exec",
        risk=risk,
    )


# ---------------- build_check_run (pure) ----------------


def test_check_run_no_baseline_is_neutral():
    spec = build_check_run(GateResult(passed=True, mode="block", has_baseline=False))
    assert spec.conclusion == "neutral"
    assert "No baseline" in spec.title


def test_check_run_clean_is_success():
    spec = build_check_run(
        GateResult(passed=True, mode="block", has_baseline=True, known=[_finding()])
    )
    assert spec.conclusion == "success"
    assert "No new" in spec.title
    assert "known: 1" in spec.summary


def test_check_run_gating_block_is_failure():
    spec = build_check_run(
        GateResult(
            passed=False, mode="block", has_baseline=True, new=[_finding()], gating=[_finding()]
        )
    )
    assert spec.conclusion == "failure"
    assert "gated" in spec.title
    assert "Gating paths" in spec.summary


def test_check_run_gating_warn_is_neutral():
    spec = build_check_run(
        GateResult(
            passed=True, mode="warn", has_baseline=True, new=[_finding()], gating=[_finding()]
        )
    )
    assert spec.conclusion == "neutral"
    assert "would gate" in spec.title


# ---------------- run_scan (end to end, local fetch + fake GitHub) ----------------


class _LocalFetcher:
    """Copies a fixture tree into the scan dir instead of cloning over the network."""

    def __init__(self, source: Path) -> None:
        self._source = source

    def fetch(self, *, clone_url, head_sha, token, dest) -> None:
        shutil.copytree(self._source, dest, dirs_exist_ok=True)


class _FakeGitHub:
    def __init__(self) -> None:
        self.check_runs: list[dict] = []

    def installation_token(self, installation_id, *, now):
        return InstallationToken(token="ghs_test", expires_at=now)

    def create_check_run(self, **kwargs):
        self.check_runs.append(kwargs)
        return 4242

    def upload_sarif(self, **kwargs):
        return "sarif-x"


def _payload(**over):
    base = {
        "installation_id": 500,
        "repo_full_name": "octo/app",
        "repo_clone_url": "https://github.com/octo/app.git",
        "default_branch": "main",
        "pr_number": 3,
        "head_sha": "headsha",
        "base_sha": "basesha",
        "base_ref": "main",
    }
    base.update(over)
    return base


@pytest.fixture
def session_factory(tmp_path):
    engine = store.make_store_engine(f"sqlite:///{tmp_path / 'sentinel.db'}")
    return store.init_store(engine)


def test_run_scan_no_baseline_posts_neutral_and_persists(session_factory):
    gh = _FakeGitHub()
    outcome = run_scan(
        _payload(),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    assert outcome.result.status == "no-baseline"
    assert outcome.check_run_id == 4242
    # the check run was posted on the head sha with a neutral conclusion
    assert len(gh.check_runs) == 1
    assert gh.check_runs[0]["head_sha"] == "headsha"
    assert gh.check_runs[0]["conclusion"] == "neutral"
    # a ScanRun was persisted for the installation-scoped repo
    with session_factory() as s:
        scans = s.execute(select(models.ScanRun)).scalars().all()
        assert len(scans) == 1
        assert scans[0].pr_number == 3
        assert scans[0].head_sha == "headsha"


def test_run_scan_new_paths_against_empty_baseline_fail(session_factory):
    # seed an empty baseline for the same installation-scoped repo, so the head's
    # real reachable paths all read as new and gate the block-mode build
    with session_factory() as s:
        store.ensure_installation(s, 500, "octo", now=_NOW)
        repo_id = store.resolve_repo(s, 500, "octo/app", now=_NOW)
        save_baseline(s, repo_id, [], branch="main", commit_sha="base", now=_NOW)
        # threshold 0 so any new reachable path gates, independent of exact scoring
        s.add(models.RepoPolicy(repo_id=repo_id, risk_threshold=0.0, mode="block"))
        s.commit()

    gh = _FakeGitHub()
    outcome = run_scan(
        _payload(),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    assert outcome.result.has_baseline is True
    assert outcome.result.new, "head has reachable dangerous paths not in the empty baseline"
    assert outcome.result.status == "failed"
    assert gh.check_runs[0]["conclusion"] == "failure"


def test_run_scan_uses_min_scope_token_and_head_sha(session_factory):
    # regression: run_scan must fetch head + post the check run on the same sha
    gh = _FakeGitHub()
    run_scan(
        _payload(head_sha="deadbeef"),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    assert gh.check_runs[0]["head_sha"] == "deadbeef"
    assert gh.check_runs[0]["name"] == "entrygraph reachability gate"


def test_run_scan_findings_are_installation_scoped(session_factory):
    # two installations with the same repo name must not share findings
    run_scan(
        _payload(installation_id=1),
        github=_FakeGitHub(),
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    run_scan(
        _payload(installation_id=2),
        github=_FakeGitHub(),
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    with session_factory() as s:
        r1 = store.resolve_repo(s, 1, "octo/app", now=_NOW)
        r2 = store.resolve_repo(s, 2, "octo/app", now=_NOW)
        assert r1 != r2
        # each scan is attributed to its own installation-scoped repo
        n1 = s.execute(
            select(func.count()).select_from(models.ScanRun).where(models.ScanRun.repo_id == r1)
        ).scalar()
        n2 = s.execute(
            select(func.count()).select_from(models.ScanRun).where(models.ScanRun.repo_id == r2)
        ).scalar()
        assert n1 == 1 and n2 == 1
