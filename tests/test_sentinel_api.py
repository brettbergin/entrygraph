"""Sentinel REST API: auth, scoping, CRUD (#126 M4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from entrygraph.gate.store import GateFinding, record_scan
from entrygraph.sentinel import store
from entrygraph.sentinel.api import create_api
from entrygraph.sentinel.config import SentinelConfig

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TOKEN = "api-secret"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


def _config(token=_TOKEN):
    return SentinelConfig(app_id="1", private_key_pem="k", webhook_secret="w", api_token=token)


def _finding(fp="fp1", risk=0.9):
    return GateFinding(
        strict=fp,
        endpoint="ep",
        source_category="http_input",
        sink_id="py.command-exec.subprocess",
        sink_category="command_exec",
        risk=risk,
    )


@pytest.fixture
def seeded(tmp_path):
    """A store with installation 100 / octo/app carrying one scan + finding."""
    sf = store.init_store(store.make_store_engine(f"sqlite:///{tmp_path / 's.db'}"))
    with sf() as s:
        store.upsert_installation(s, 100, "octo", now=_NOW)
        repo_id = store.resolve_repo(s, 100, "octo/app", now=_NOW)
        record_scan(
            s,
            repo_id,
            status="failed",
            findings=[(_finding("fp-new"), "new"), (_finding("fp-known", 0.6), "known")],
            head_sha="abc",
            pr_number=5,
            now=_NOW,
        )
    return sf


def _client(sf, token=_TOKEN):
    return TestClient(create_api(_config(token), sf))


_BASE = "/installations/100/repos/octo/app"


# ---------------- auth ----------------


def test_missing_token_is_401(seeded):
    assert _client(seeded).get(f"{_BASE}/scans").status_code == 401


def test_wrong_token_is_401(seeded):
    r = _client(seeded).get(f"{_BASE}/scans", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_api_disabled_without_configured_token_is_503(seeded):
    r = _client(seeded, token="").get(f"{_BASE}/scans", headers=_AUTH)
    assert r.status_code == 503


# ---------------- scans + findings ----------------


def test_list_scans(seeded):
    r = _client(seeded).get(f"{_BASE}/scans", headers=_AUTH)
    assert r.status_code == 200
    scans = r.json()["scans"]
    assert len(scans) == 1
    assert scans[0]["pr_number"] == 5
    assert scans[0]["status"] == "failed"
    assert scans[0]["counts"] == {"new": 1, "known": 1, "fixed": 0, "suppressed": 0}


def test_scan_findings_filtered_by_status(seeded):
    scan_id = _client(seeded).get(f"{_BASE}/scans", headers=_AUTH).json()["scans"][0]["id"]
    r = _client(seeded).get(f"{_BASE}/scans/{scan_id}/findings?status=new", headers=_AUTH)
    findings = r.json()["findings"]
    assert [f["fingerprint"] for f in findings] == ["fp-new"]


def test_latest_findings(seeded):
    r = _client(seeded).get(f"{_BASE}/findings", headers=_AUTH)
    assert r.status_code == 200
    assert {f["fingerprint"] for f in r.json()["findings"]} == {"fp-new", "fp-known"}


# ---------------- scoping ----------------


def test_unknown_repo_is_404(seeded):
    r = _client(seeded).get("/installations/100/repos/octo/other/scans", headers=_AUTH)
    assert r.status_code == 404


def test_cross_installation_is_isolated(seeded):
    # installation 999 has no octo/app -> 404, cannot read installation 100's data
    r = _client(seeded).get("/installations/999/repos/octo/app/scans", headers=_AUTH)
    assert r.status_code == 404


# ---------------- suppressions CRUD ----------------


def test_suppression_crud(seeded):
    client = _client(seeded)
    # add
    r = client.post(
        f"{_BASE}/suppressions",
        headers=_AUTH,
        json={"fingerprint": "fp-new", "reason": "reviewed", "created_by": "alice"},
    )
    assert r.status_code == 201
    # list
    sups = client.get(f"{_BASE}/suppressions", headers=_AUTH).json()["suppressions"]
    assert len(sups) == 1 and sups[0]["reason"] == "reviewed"
    # delete
    assert client.delete(f"{_BASE}/suppressions/fp-new", headers=_AUTH).status_code == 200
    assert client.get(f"{_BASE}/suppressions", headers=_AUTH).json()["suppressions"] == []
    # deleting a missing one is 404
    assert client.delete(f"{_BASE}/suppressions/fp-new", headers=_AUTH).status_code == 404


# ---------------- policy ----------------


def test_policy_get_default_and_update(seeded):
    client = _client(seeded)
    default = client.get(f"{_BASE}/policy", headers=_AUTH).json()
    assert default["risk_threshold"] == 0.5 and default["mode"] == "block"
    r = client.put(
        f"{_BASE}/policy",
        headers=_AUTH,
        json={"risk_threshold": 0.8, "mode": "warn", "gated_categories": ["command_exec"]},
    )
    assert r.status_code == 200
    updated = client.get(f"{_BASE}/policy", headers=_AUTH).json()
    assert updated["risk_threshold"] == 0.8
    assert updated["mode"] == "warn"
    assert updated["gated_categories"] == ["command_exec"]
