"""Sentinel webhook receiver: HMAC verify, dedupe, enqueue (#126 M1)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from entrygraph.sentinel.config import SentinelConfig
from entrygraph.sentinel.webhook import (
    InMemoryScanQueue,
    parse_pull_request_event,
    verify_signature,
)

_SECRET = "webhook-secret"
_CONFIG = SentinelConfig(
    app_id="1", private_key_pem="-----BEGIN KEY-----\nx\n-----END KEY-----", webhook_secret=_SECRET
)


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------- signature ----------------


def test_verify_signature_accepts_valid():
    body = b'{"a":1}'
    assert verify_signature(_SECRET, body, _sign(body))


def test_verify_signature_rejects_tampered_body():
    body = b'{"a":1}'
    sig = _sign(body)
    assert not verify_signature(_SECRET, b'{"a":2}', sig)


def test_verify_signature_rejects_wrong_secret():
    body = b'{"a":1}'
    assert not verify_signature(_SECRET, body, _sign(body, "other-secret"))


@pytest.mark.parametrize("header", [None, "", "deadbeef", "sha1=abc", "sha256="])
def test_verify_signature_rejects_missing_or_malformed(header):
    assert not verify_signature(_SECRET, b"x", header)


# ---------------- event parsing ----------------


def _pr_payload(action="opened", *, draft=False, **overrides):
    payload = {
        "action": action,
        "installation": {"id": 555},
        "repository": {
            "full_name": "octo/repo",
            "clone_url": "https://github.com/octo/repo.git",
            "default_branch": "main",
        },
        "pull_request": {
            "number": 7,
            "draft": draft,
            "head": {"sha": "headsha"},
            "base": {"sha": "basesha", "ref": "main"},
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize", "ready_for_review"])
def test_parse_scannable_actions(action):
    scan = parse_pull_request_event(_pr_payload(action))
    assert scan is not None
    assert scan.installation_id == 555
    assert scan.repo_full_name == "octo/repo"
    assert scan.pr_number == 7
    assert scan.head_sha == "headsha"
    assert scan.base_sha == "basesha"


@pytest.mark.parametrize("action", ["closed", "labeled", "assigned", "edited"])
def test_parse_ignores_non_scan_actions(action):
    assert parse_pull_request_event(_pr_payload(action)) is None


def test_parse_skips_draft_pull_request():
    assert parse_pull_request_event(_pr_payload("opened", draft=True)) is None
    # ...but a draft marked ready IS scanned
    assert parse_pull_request_event(_pr_payload("ready_for_review", draft=True)) is not None


def test_parse_returns_none_on_missing_fields():
    payload = _pr_payload("opened")
    del payload["pull_request"]["head"]
    assert parse_pull_request_event(payload) is None


# ---------------- endpoint (TestClient) ----------------


def _client(queue=None):
    from entrygraph.sentinel.webhook import create_app

    app = create_app(_CONFIG, queue=queue or InMemoryScanQueue())
    return TestClient(app)


def _post(client, payload, *, event="pull_request", delivery="d1", secret=_SECRET, sign=True):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery}
    if sign:
        headers["X-Hub-Signature-256"] = _sign(body, secret)
    return client.post("/webhook", content=body, headers=headers)


def test_healthz():
    assert _client().get("/healthz").json() == {"status": "ok"}


def test_unsigned_webhook_is_rejected():
    resp = _post(_client(), _pr_payload(), sign=False)
    assert resp.status_code == 401


def test_forged_signature_is_rejected():
    resp = _post(_client(), _pr_payload(), secret="attacker", sign=True)
    assert resp.status_code == 401


def test_missing_delivery_id_is_rejected():
    body = json.dumps(_pr_payload()).encode()
    resp = _client().post(
        "/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)},
    )
    assert resp.status_code == 400


def test_opened_pr_enqueues_scan():
    queue = InMemoryScanQueue()
    client = _client(queue)
    resp = _post(client, _pr_payload("opened"))
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert len(queue.jobs) == 1
    job, payload = queue.jobs[0]
    assert job == "scan_pull_request"
    assert payload["head_sha"] == "headsha"
    assert payload["installation_id"] == 555


def test_replayed_delivery_is_deduped():
    queue = InMemoryScanQueue()
    client = _client(queue)
    first = _post(client, _pr_payload("opened"), delivery="dup")
    second = _post(client, _pr_payload("opened"), delivery="dup")
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len(queue.jobs) == 1  # only enqueued once


def test_ping_event_pongs():
    resp = _post(_client(), {"zen": "hi"}, event="ping")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pong"


def test_non_scan_action_is_skipped_not_enqueued():
    queue = InMemoryScanQueue()
    resp = _post(_client(queue), _pr_payload("closed"))
    assert resp.status_code == 202
    assert resp.json()["status"] == "skipped"
    assert queue.jobs == []


# ---------------- installation lifecycle events (M2) ----------------


def _install_payload(action, inst_id=900, login="octo"):
    return {"action": action, "installation": {"id": inst_id, "account": {"login": login}}}


def test_installation_created_upserts(tmp_path):
    from datetime import UTC, datetime

    from entrygraph.sentinel import store
    from entrygraph.sentinel.webhook import create_app

    sf = store.init_store(store.make_store_engine(f"sqlite:///{tmp_path / 's.db'}"))
    client = TestClient(create_app(_CONFIG, session_factory=sf))
    resp = _post(client, _install_payload("created"), event="installation", delivery="i1")
    assert resp.status_code == 202
    with sf() as s:
        inst = store.get_installation(s, 900)
        assert inst is not None and inst.account_login == "octo"
    # ...and a deleted event hard-removes it
    with sf() as s:
        store.resolve_repo(s, 900, "octo/repo", now=datetime(2026, 1, 1, tzinfo=UTC))
    resp = _post(client, _install_payload("deleted"), event="installation", delivery="i2")
    assert resp.status_code == 202
    with sf() as s:
        assert store.get_installation(s, 900) is None
        assert store.installation_repo_ids(s, 900) == []


def test_installation_event_ignored_without_store():
    # no session_factory -> installation events are acknowledged but not persisted
    resp = _post(_client(), _install_payload("created"), event="installation", delivery="i3")
    assert resp.status_code == 202
    assert resp.json()["status"] == "installation"


# ---------------- push -> baseline refresh (M3) ----------------


def _push_payload(ref="refs/heads/main", after="a" * 40):
    return {
        "ref": ref,
        "after": after,
        "deleted": False,
        "installation": {"id": 42},
        "repository": {
            "full_name": "octo/app",
            "clone_url": "https://github.com/octo/app.git",
            "default_branch": "main",
        },
    }


def test_push_to_default_enqueues_refresh():
    queue = InMemoryScanQueue()
    resp = _post(_client(queue), _push_payload(), event="push", delivery="p1")
    assert resp.status_code == 202
    assert resp.json()["status"] == "refresh"
    assert len(queue.jobs) == 1
    job, payload = queue.jobs[0]
    assert job == "refresh_baseline"
    assert payload["branch"] == "main"
    assert payload["head_sha"] == "a" * 40


def test_push_to_feature_branch_does_not_refresh():
    queue = InMemoryScanQueue()
    resp = _post(_client(queue), _push_payload(ref="refs/heads/wip"), event="push", delivery="p2")
    assert resp.status_code == 202
    assert resp.json()["status"] == "skipped"
    assert queue.jobs == []
