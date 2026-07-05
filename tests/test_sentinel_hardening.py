"""Sentinel M5 hardening: retention purge, scan size cap, composed app (#126)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from entrygraph.gate.store import GateFinding, record_scan
from entrygraph.sentinel import store
from entrygraph.sentinel.app import create_service_app
from entrygraph.sentinel.config import SentinelConfig
from entrygraph.sentinel.github import InstallationToken
from entrygraph.sentinel.worker import run_scan

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture
def session_factory(tmp_path):
    return store.init_store(store.make_store_engine(f"sqlite:///{tmp_path / 's.db'}"))


# ---------------- retention purge ----------------


def test_purge_keeps_newest_scans(session_factory):
    with session_factory() as s:
        store.upsert_installation(s, 1, "a", now=_NOW)
        repo_id = store.resolve_repo(s, 1, "a/repo", now=_NOW)
        for i in range(5):
            record_scan(
                s,
                repo_id,
                status="passed",
                findings=[
                    (
                        GateFinding(
                            strict=f"fp{i}",
                            endpoint="e",
                            source_category=None,
                            sink_id=None,
                            sink_category=None,
                            risk=0.5,
                        ),
                        "known",
                    )
                ],
                head_sha=f"sha{i}",
                now=_NOW + timedelta(minutes=i),
            )
    with session_factory() as s:
        deleted = store.purge_scans(s, repo_id, keep=2)
    assert deleted == 3
    with session_factory() as s:
        remaining = store.list_scans(s, repo_id)
        assert len(remaining) == 2
        # the two newest (sha4, sha3) survive; their findings survived the cascade
        assert {r.head_sha for r in remaining} == {"sha4", "sha3"}
        assert store.scan_findings(s, remaining[0].id)  # findings still attached


def test_purge_keep_zero_removes_all(session_factory):
    with session_factory() as s:
        store.upsert_installation(s, 1, "a", now=_NOW)
        repo_id = store.resolve_repo(s, 1, "a/repo", now=_NOW)
        record_scan(s, repo_id, status="passed", findings=[], head_sha="x", now=_NOW)
    with session_factory() as s:
        assert store.purge_scans(s, repo_id, keep=0) == 1
        assert store.list_scans(s, repo_id) == []


# ---------------- scan size cap ----------------


class _LocalFetcher:
    def __init__(self, source: Path) -> None:
        self._source = source

    def fetch(self, *, clone_url, head_sha, token, dest) -> None:
        shutil.copytree(self._source, dest, dirs_exist_ok=True)


class _FakeGitHub:
    def __init__(self) -> None:
        self.check_runs: list[dict] = []

    def installation_token(self, installation_id, *, now):
        return InstallationToken(token="t", expires_at=now)

    def create_check_run(self, **kwargs):
        self.check_runs.append(kwargs)
        return 1

    def upload_sarif(self, **kwargs):
        return "s"


def _payload(**over):
    base = {
        "installation_id": 1,
        "repo_full_name": "a/app",
        "repo_clone_url": "https://github.com/a/app.git",
        "default_branch": "main",
        "pr_number": 1,
        "head_sha": "h",
        "base_sha": "b",
        "base_ref": "main",
    }
    base.update(over)
    return base


def test_oversized_repo_is_skipped_with_neutral_check(session_factory):
    gh = _FakeGitHub()
    # a tiny cap forces the skip path without a huge fixture
    outcome = run_scan(
        _payload(),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
        max_repo_bytes=10,
    )
    assert outcome.skipped_reason == "too_large"
    assert outcome.result is None
    assert gh.check_runs[0]["conclusion"] == "neutral"
    assert "too large" in gh.check_runs[0]["title"].lower()
    # nothing indexed or persisted — the repo was never even registered
    with session_factory() as s:
        assert store.repo_id_for(s, 1, "a/app") is None


def test_within_cap_scans_normally(session_factory):
    gh = _FakeGitHub()
    outcome = run_scan(
        _payload(),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
        max_repo_bytes=50 * 1024 * 1024,
    )
    assert outcome.skipped_reason is None
    assert outcome.result is not None


# ---------------- composed service app ----------------


def test_service_app_mounts_webhook_and_api(session_factory):
    config = SentinelConfig(app_id="1", private_key_pem="k", webhook_secret="w", api_token="tok")
    client = TestClient(create_service_app(config, session_factory=session_factory))
    # webhook health at root
    assert client.get("/healthz").json() == {"status": "ok"}
    # API mounted under /api, still token-guarded
    with session_factory() as s:
        store.upsert_installation(s, 1, "a", now=_NOW)
        store.resolve_repo(s, 1, "a/app", now=_NOW)
    unauth = client.get("/api/installations/1/repos/a/app/scans")
    assert unauth.status_code == 401
    ok = client.get(
        "/api/installations/1/repos/a/app/scans", headers={"Authorization": "Bearer tok"}
    )
    assert ok.status_code == 200


# ---------------- dashboard mount (M6) ----------------


def test_dashboard_mounts_when_built(tmp_path):
    from fastapi import FastAPI

    from entrygraph.sentinel.app import _mount_dashboard

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Sentinel</title>")
    app = FastAPI()
    _mount_dashboard(app, static)
    client = TestClient(app)
    r = client.get("/ui/")
    assert r.status_code == 200
    assert "Sentinel" in r.text


def test_dashboard_skipped_without_build(tmp_path):
    from fastapi import FastAPI

    from entrygraph.sentinel.app import _mount_dashboard

    app = FastAPI()
    _mount_dashboard(app, tmp_path / "does-not-exist")  # no build -> no mount
    assert TestClient(app).get("/ui/").status_code == 404
