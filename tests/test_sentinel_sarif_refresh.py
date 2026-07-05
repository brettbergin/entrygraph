"""Sentinel M3: SARIF upload + baseline refresh (#126)."""

from __future__ import annotations

import base64
import gzip
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("httpx")
pytest.importorskip("cryptography")

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from entrygraph.gate.store import load_baseline
from entrygraph.sentinel import store
from entrygraph.sentinel.github import GitHubApp, InstallationToken
from entrygraph.sentinel.webhook import parse_push_event
from entrygraph.sentinel.worker import refresh_baseline, run_scan

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


def _private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


# ---------------- SARIF upload (github.py) ----------------


def test_upload_sarif_gzips_and_encodes():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(202, json={"id": "sarif-123"})

    app = GitHubApp(
        "1", _private_key(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    sarif = {"version": "2.1.0", "runs": []}
    sid = app.upload_sarif(
        token="t", repo_full_name="octo/repo", commit_sha="abc", ref="refs/pull/5/head", sarif=sarif
    )
    assert sid == "sarif-123"
    assert captured["url"].endswith("/repos/octo/repo/code-scanning/sarifs")
    body = captured["body"]
    assert body["commit_sha"] == "abc" and body["ref"] == "refs/pull/5/head"
    # the sarif field round-trips through gzip+base64 back to our log
    decoded = json.loads(gzip.decompress(base64.b64decode(body["sarif"])))
    assert decoded == sarif


def test_upload_sarif_none_when_declined():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "code scanning not enabled"})

    app = GitHubApp(
        "1", _private_key(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert (
        app.upload_sarif(
            token="t", repo_full_name="o/r", commit_sha="a", ref="refs/pull/1/head", sarif={}
        )
        is None
    )


# ---------------- push event parsing ----------------


def _push(ref="refs/heads/main", after="a" * 40, default_branch="main", **over):
    payload = {
        "ref": ref,
        "after": after,
        "deleted": False,
        "installation": {"id": 42},
        "repository": {
            "full_name": "octo/app",
            "clone_url": "https://github.com/octo/app.git",
            "default_branch": default_branch,
        },
    }
    payload.update(over)
    return payload


def test_push_to_default_branch_yields_refresh():
    refresh = parse_push_event(_push())
    assert refresh is not None
    assert refresh["branch"] == "main"
    assert refresh["head_sha"] == "a" * 40
    assert refresh["installation_id"] == 42


def test_push_to_feature_branch_ignored():
    assert parse_push_event(_push(ref="refs/heads/feature")) is None


def test_branch_delete_push_ignored():
    assert parse_push_event(_push(after="0" * 40, deleted=True)) is None


# ---------------- run_scan uploads SARIF ----------------


class _LocalFetcher:
    def __init__(self, source: Path) -> None:
        self._source = source

    def fetch(self, *, clone_url, head_sha, token, dest) -> None:
        shutil.copytree(self._source, dest, dirs_exist_ok=True)


class _FakeGitHub:
    def __init__(self) -> None:
        self.sarifs: list[dict] = []
        self.check_runs: list[dict] = []

    def installation_token(self, installation_id, *, now):
        return InstallationToken(token="t", expires_at=now)

    def create_check_run(self, **kwargs):
        self.check_runs.append(kwargs)
        return 1

    def upload_sarif(self, **kwargs):
        self.sarifs.append(kwargs)
        return "sarif-1"


@pytest.fixture
def session_factory(tmp_path):
    return store.init_store(store.make_store_engine(f"sqlite:///{tmp_path / 's.db'}"))


def _scan_payload(**over):
    base = {
        "installation_id": 42,
        "repo_full_name": "octo/app",
        "repo_clone_url": "https://github.com/octo/app.git",
        "default_branch": "main",
        "pr_number": 9,
        "head_sha": "headsha",
        "base_sha": "basesha",
        "base_ref": "main",
    }
    base.update(over)
    return base


def test_run_scan_uploads_sarif_on_pr_head_ref(session_factory):
    gh = _FakeGitHub()
    outcome = run_scan(
        _scan_payload(),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    assert outcome.sarif_id == "sarif-1"
    assert len(gh.sarifs) == 1
    assert gh.sarifs[0]["ref"] == "refs/pull/9/head"
    assert gh.sarifs[0]["commit_sha"] == "headsha"
    assert gh.sarifs[0]["sarif"]["version"] == "2.1.0"


# ---------------- refresh_baseline ----------------


def _refresh_payload(**over):
    base = {
        "installation_id": 42,
        "repo_full_name": "octo/app",
        "repo_clone_url": "https://github.com/octo/app.git",
        "branch": "main",
        "head_sha": "mergesha",
    }
    base.update(over)
    return base


def test_refresh_baseline_cuts_baseline_from_default_branch(session_factory):
    gh = _FakeGitHub()
    count = refresh_baseline(
        _refresh_payload(),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    assert count > 0  # flask_app has reachable dangerous paths
    with session_factory() as s:
        repo_id = store.resolve_repo(s, 42, "octo/app", now=_NOW)
        view = load_baseline(s, repo_id, "main")
        assert view is not None
        assert view.commit_sha == "mergesha"
        assert len(view.strict) == count


def test_refresh_makes_subsequent_scan_pass(session_factory):
    # after a refresh from the default branch, an identical PR head has no NEW paths
    gh = _FakeGitHub()
    refresh_baseline(
        _refresh_payload(head_sha="mergesha"),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    outcome = run_scan(
        _scan_payload(head_sha="prsha"),
        github=gh,
        fetcher=_LocalFetcher(FLASK_APP),
        session_factory=session_factory,
        now=_NOW,
    )
    # same code as the baseline -> everything is known, nothing new gates
    assert outcome.result.has_baseline is True
    assert outcome.result.new == []
    assert outcome.result.status == "passed"
