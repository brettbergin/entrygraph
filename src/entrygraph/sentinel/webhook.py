"""Sentinel webhook receiver (#126, milestone 1).

Receives GitHub ``pull_request`` webhooks, verifies the ``X-Hub-Signature-256``
HMAC, deduplicates on ``X-GitHub-Delivery`` (GitHub retries deliveries), and
enqueues a scan job for the head commit. It does not run the gate inline — that is
the worker's job (milestone 2) — so the receiver stays fast and the response is a
plain 202.

The queue and delivery log are injectable protocols: milestone 1 ships in-memory
defaults (so the app is testable without Redis), and milestone 2 swaps in an
arq/Redis queue and a store-backed delivery log without touching this module.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI, Header, HTTPException, Request, Response

from entrygraph.sentinel.config import SentinelConfig

# pull_request actions that warrant a (re)scan of the head commit
_SCAN_ACTIONS = frozenset({"opened", "reopened", "synchronize", "ready_for_review"})
_SCAN_JOB = "scan_pull_request"
_REFRESH_JOB = "refresh_baseline"
_NULL_SHA = "0" * 40


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time check of GitHub's ``sha256=<hex>`` HMAC over the raw body.

    A missing/malformed header is a failure, never an exception — an unsigned or
    forged delivery must be rejected, not crash the receiver."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256=") :]
    return hmac.compare_digest(expected, provided)


class DeliveryLog(Protocol):
    """Records processed delivery ids so a retried webhook isn't scanned twice."""

    def seen(self, delivery_id: str) -> bool:
        """Mark ``delivery_id`` processed; return True if it was already seen."""
        ...


class ScanQueue(Protocol):
    """Enqueues a scan job. Milestone 2 backs this with arq/Redis."""

    def enqueue(self, job: str, payload: dict[str, Any]) -> None: ...


class InMemoryDeliveryLog:
    """Bounded LRU of recent delivery ids — a single-process dedupe for milestone
    1. Milestone 2 replaces it with a store-backed log that survives restarts and
    is shared across workers."""

    def __init__(self, capacity: int = 4096) -> None:
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def seen(self, delivery_id: str) -> bool:
        if delivery_id in self._seen:
            self._seen.move_to_end(delivery_id)
            return True
        self._seen[delivery_id] = None
        if len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return False


class InMemoryScanQueue:
    """Collects enqueued jobs in a list — used in milestone 1 and in tests to
    assert what would be dispatched, with no Redis dependency."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    def enqueue(self, job: str, payload: dict[str, Any]) -> None:
        self.jobs.append((job, payload))


@dataclass(frozen=True, slots=True)
class ScanRequest:
    """The minimal, installation-scoped descriptor a scan job needs."""

    installation_id: int
    repo_full_name: str
    repo_clone_url: str
    default_branch: str
    pr_number: int
    head_sha: str
    base_sha: str
    base_ref: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "repo_full_name": self.repo_full_name,
            "repo_clone_url": self.repo_clone_url,
            "default_branch": self.default_branch,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "base_ref": self.base_ref,
        }


def parse_pull_request_event(payload: dict[str, Any]) -> ScanRequest | None:
    """Extract a :class:`ScanRequest` from a ``pull_request`` webhook, or None if
    the action isn't one we scan or the payload is missing required fields.

    Draft PRs are skipped unless the action is ``ready_for_review``."""
    action = payload.get("action")
    if action not in _SCAN_ACTIONS:
        return None
    pr = payload.get("pull_request") or {}
    if action != "ready_for_review" and pr.get("draft"):
        return None
    installation = (payload.get("installation") or {}).get("id")
    repo = payload.get("repository") or {}
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    full_name = repo.get("full_name")
    clone_url = repo.get("clone_url")
    number = pr.get("number")
    head_sha = head.get("sha")
    base_sha = base.get("sha")
    if installation is None or number is None:
        return None
    if any(v in (None, "") for v in (full_name, clone_url, head_sha, base_sha)):
        return None
    default_branch = repo.get("default_branch", "main")
    return ScanRequest(
        installation_id=int(installation),
        repo_full_name=str(full_name),
        repo_clone_url=str(clone_url),
        default_branch=str(default_branch),
        pr_number=int(number),
        head_sha=str(head_sha),
        base_sha=str(base_sha),
        base_ref=str(base.get("ref", default_branch)),
    )


def _handle_installation_event(payload: dict[str, Any], session_factory) -> None:
    """Apply an ``installation`` lifecycle event to the store: created/unsuspend
    upsert the row, suspend flags it, deleted hard-deletes all of its data."""
    from datetime import UTC, datetime

    from entrygraph.sentinel import store

    action = payload.get("action")
    inst = payload.get("installation") or {}
    inst_id = inst.get("id")
    if inst_id is None:
        return
    login = (inst.get("account") or {}).get("login", "")
    now = datetime.now(UTC)
    with session_factory() as session:
        if action == "deleted":
            store.delete_installation(session, int(inst_id))
        elif action == "suspend":
            store.set_suspended(session, int(inst_id), True)
        elif action in ("created", "unsuspend", "new_permissions_accepted"):
            store.upsert_installation(session, int(inst_id), str(login), now=now)


def parse_push_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """A push to the protected default branch → a baseline-refresh payload, or None.

    Only the default branch refreshes a baseline (a feature branch must never move
    it), and a branch delete (null after-sha) is ignored."""
    repo = payload.get("repository") or {}
    default_branch = repo.get("default_branch", "main")
    if payload.get("ref") != f"refs/heads/{default_branch}":
        return None
    after = payload.get("after")
    if not after or after == _NULL_SHA or payload.get("deleted"):
        return None
    installation = (payload.get("installation") or {}).get("id")
    full_name = repo.get("full_name")
    clone_url = repo.get("clone_url")
    if installation is None:
        return None
    if any(v in (None, "") for v in (full_name, clone_url)):
        return None
    return {
        "installation_id": int(installation),
        "repo_full_name": str(full_name),
        "repo_clone_url": str(clone_url),
        "branch": str(default_branch),
        "head_sha": str(after),
    }


def create_app(
    config: SentinelConfig,
    *,
    queue: ScanQueue | None = None,
    delivery_log: DeliveryLog | None = None,
    session_factory=None,
) -> FastAPI:
    """Build the Sentinel FastAPI app. ``queue`` and ``delivery_log`` default to
    the in-memory implementations; a deployment injects the arq/store-backed
    versions. ``session_factory`` (when provided) enables ``installation``
    lifecycle handling — including hard-delete on uninstall."""
    app = FastAPI(title="entrygraph Sentinel", version="1")
    scan_queue = queue or InMemoryScanQueue()
    deliveries = delivery_log or InMemoryDeliveryLog()
    app.state.queue = scan_queue
    app.state.delivery_log = deliveries

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> Response:
        body = await request.body()
        if not verify_signature(config.webhook_secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="invalid signature")
        if not x_github_delivery:
            raise HTTPException(status_code=400, detail="missing delivery id")
        if deliveries.seen(x_github_delivery):
            return Response(status_code=200, content=b'{"status":"duplicate"}')
        if x_github_event == "ping":
            return Response(status_code=200, content=b'{"status":"pong"}')
        if x_github_event not in ("pull_request", "push", "installation"):
            return Response(status_code=202, content=b'{"status":"ignored"}')
        import json

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc
        if x_github_event == "installation":
            if session_factory is not None:
                _handle_installation_event(payload, session_factory)
            return Response(status_code=202, content=b'{"status":"installation"}')
        if x_github_event == "push":
            refresh = parse_push_event(payload)
            if refresh is None:
                return Response(status_code=202, content=b'{"status":"skipped"}')
            scan_queue.enqueue(_REFRESH_JOB, refresh)
            return Response(status_code=202, content=b'{"status":"refresh"}')
        scan = parse_pull_request_event(payload)
        if scan is None:
            return Response(status_code=202, content=b'{"status":"skipped"}')
        scan_queue.enqueue(_SCAN_JOB, scan.as_payload())
        return Response(status_code=202, content=b'{"status":"queued"}')

    return app
