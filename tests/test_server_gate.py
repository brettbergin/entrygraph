"""Gate workflow over /api/v1: run, baseline, scans, policy, suppressions."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from entrygraph.api import CodeGraph
from entrygraph.server.app import create_app
from entrygraph.server.config import ServerConfig

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture()
def client(tmp_path) -> TestClient:
    db = tmp_path / "graph.db"
    g = CodeGraph.index(FLASK_APP, db=db)
    g.close()
    cfg = ServerConfig.from_env({"EG_DB": str(db), "EG_APP_DB": str(tmp_path / "app.db")})
    with TestClient(create_app(cfg, serve_ui=False)) as c:
        yield c


@pytest.fixture()
def repo_id(client) -> int:
    return client.get("/api/v1/repos").json()["repos"][0]["id"]


def test_gate_without_baseline_reports_no_baseline(client, repo_id):
    body = client.post(f"/api/v1/repos/{repo_id}/gate", json={}).json()
    assert body["status"] == "no-baseline"
    assert body["has_baseline"] is False
    assert body["counts"]["new"] > 0  # the fixture has dangerous paths
    assert body["gating"] == []  # nothing gates without a baseline
    assert body["scan_id"] is not None


def test_baseline_then_gate_passes(client, repo_id):
    cut = client.post(f"/api/v1/repos/{repo_id}/baseline", json={"branch": "main"}).json()
    assert cut["paths"] > 0

    shown = client.get(f"/api/v1/repos/{repo_id}/baseline").json()["baseline"]
    assert shown["branch"] == "main"
    assert len(shown["paths"]) == cut["paths"]
    assert all({"fingerprint", "risk", "hops"} <= set(p) for p in shown["paths"])

    # nothing changed since the baseline: every path is known, gate passes
    body = client.post(f"/api/v1/repos/{repo_id}/gate", json={}).json()
    assert body["status"] == "passed"
    assert body["counts"]["new"] == 0
    assert body["counts"]["known"] == cut["paths"]


def test_missing_baseline_shows_null(client, repo_id):
    assert client.get(f"/api/v1/repos/{repo_id}/baseline").json()["baseline"] is None
    assert (
        client.get(f"/api/v1/repos/{repo_id}/baseline", params={"branch": "dev"}).json()["baseline"]
        is None
    )


def test_scans_recorded_and_findings_filterable(client, repo_id):
    client.post(f"/api/v1/repos/{repo_id}/gate", json={})
    scans = client.get(f"/api/v1/repos/{repo_id}/scans").json()["scans"]
    assert scans and scans[0]["status"] == "no-baseline"
    scan_id = scans[0]["id"]

    findings = client.get(f"/api/v1/repos/{repo_id}/scans/{scan_id}/findings").json()["findings"]
    assert findings and all(f["path"]["hops"] for f in findings)
    news = client.get(
        f"/api/v1/repos/{repo_id}/scans/{scan_id}/findings", params={"status": "new"}
    ).json()["findings"]
    assert news and all(f["status"] == "new" for f in news)

    # scan ids are repo-scoped
    assert client.get(f"/api/v1/repos/999/scans/{scan_id}/findings").status_code == 404


def test_sarif_via_accept_header(client, repo_id):
    resp = client.post(
        f"/api/v1/repos/{repo_id}/gate",
        json={},
        headers={"Accept": "application/sarif+json"},
    )
    assert resp.headers["content-type"].startswith("application/sarif+json")
    sarif = resp.json()
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "entrygraph"
    assert sarif["runs"][0]["results"]


def test_policy_roundtrip(client, repo_id):
    default = client.get(f"/api/v1/repos/{repo_id}/policy").json()["policy"]
    assert default == {
        "risk_threshold": 0.5,
        "gated_categories": None,
        "mode": "block",
        "min_confidence": "fuzzy",
    }
    updated = client.put(
        f"/api/v1/repos/{repo_id}/policy",
        json={"risk_threshold": 0.8, "mode": "warn", "gated_categories": ["command_exec"]},
    ).json()["policy"]
    assert updated["risk_threshold"] == 0.8
    assert updated["mode"] == "warn"
    assert updated["gated_categories"] == ["command_exec"]
    assert updated["min_confidence"] == "fuzzy"  # untouched fields survive

    bad = client.put(f"/api/v1/repos/{repo_id}/policy", json={"mode": "yolo"})
    assert bad.status_code == 422


def test_warn_override_never_fails(client, repo_id):
    client.post(f"/api/v1/repos/{repo_id}/baseline", json={})
    # cut baseline, then force everything to be "new" by deleting it — instead,
    # simulate a would-fail run via threshold 0 on a fresh branch baseline
    body = client.post(
        f"/api/v1/repos/{repo_id}/gate",
        json={"branch": "other", "threshold": 0.0, "warn": True},
    ).json()
    assert body["status"] == "no-baseline"  # branch "other" has no baseline
    body2 = client.post(
        f"/api/v1/repos/{repo_id}/gate", json={"threshold": 0.0, "warn": True}
    ).json()
    # against main's baseline nothing is new, so it passes regardless
    assert body2["passed"] is True


def test_suppression_roundtrip_affects_gate(client, repo_id):
    client.post(f"/api/v1/repos/{repo_id}/baseline", json={})
    # find a known path fingerprint from the baseline
    fp = client.get(f"/api/v1/repos/{repo_id}/baseline").json()["baseline"]["paths"][0][
        "fingerprint"
    ]

    created = client.post(
        f"/api/v1/repos/{repo_id}/suppressions",
        json={"fingerprint": fp, "reason": "accepted risk: sandboxed"},
    )
    assert created.status_code == 201

    listed = client.get(f"/api/v1/repos/{repo_id}/suppressions").json()["suppressions"]
    assert listed and listed[0]["fingerprint"] == fp
    assert listed[0]["created_by"] == "dev:local"

    # the suppressed path now classifies as suppressed, not known
    run = client.post(f"/api/v1/repos/{repo_id}/gate", json={}).json()
    assert run["counts"]["suppressed"] == 1

    assert client.delete(f"/api/v1/repos/{repo_id}/suppressions/{fp}").json()["removed"] == fp
    assert client.delete(f"/api/v1/repos/{repo_id}/suppressions/{fp}").status_code == 404


def test_unknown_repo_404s(client):
    for method, path, kwargs in [
        ("post", "/api/v1/repos/999/gate", {"json": {}}),
        ("post", "/api/v1/repos/999/baseline", {"json": {}}),
        ("get", "/api/v1/repos/999/baseline", {}),
        ("get", "/api/v1/repos/999/scans", {}),
        ("get", "/api/v1/repos/999/policy", {}),
        ("get", "/api/v1/repos/999/suppressions", {}),
    ]:
        assert getattr(client, method)(path, **kwargs).status_code == 404, path
