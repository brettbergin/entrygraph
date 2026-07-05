"""arq/Redis job wiring for Sentinel (#126, milestone 2).

This is the deployment glue between the webhook (producer) and the scan worker
(consumer): the webhook enqueues a ``scan_pull_request`` job onto Redis, and an
arq worker process consumes it and calls :func:`entrygraph.sentinel.worker.run_scan`.

It requires ``redis`` + ``arq`` (the runtime-only half of the ``sentinel`` extra),
so it is imported only by a running deployment — never by the test suite, whose
coverage lives on :func:`run_scan`, the store, and the webhook receiver directly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from entrygraph.sentinel.config import SentinelConfig
from entrygraph.sentinel.github import GitHubApp
from entrygraph.sentinel.store import init_store, make_store_engine
from entrygraph.sentinel.webhook import ScanQueue
from entrygraph.sentinel.worker import DulwichFetcher, run_scan
from entrygraph.sentinel.worker import refresh_baseline as _refresh_impl


class ArqScanQueue(ScanQueue):
    """Producer adapter: enqueues jobs onto Redis via an arq pool. Called from the
    async webhook handler, so the enqueue coroutine is scheduled on the running
    loop (fire-and-forget — the webhook acks with 202 before the job runs)."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def enqueue(self, job: str, payload: dict[str, Any]) -> None:
        asyncio.ensure_future(self._pool.enqueue_job(job, payload))


async def make_queue(config: SentinelConfig) -> ArqScanQueue:
    pool = await create_pool(RedisSettings.from_dsn(config.redis_url))
    return ArqScanQueue(pool)


async def scan_pull_request(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """arq job: run one PR scan. Heavy work (index + gate) runs in a thread so the
    worker's event loop stays responsive."""
    github: GitHubApp = ctx["github"]
    session_factory = ctx["session_factory"]
    outcome = await asyncio.to_thread(
        run_scan,
        payload,
        github=github,
        fetcher=DulwichFetcher(),
        session_factory=session_factory,
        now=datetime.now(UTC),
    )
    status = outcome.result.status if outcome.result is not None else outcome.skipped_reason
    return {"status": status, "check_run_id": outcome.check_run_id}


# arq dispatches by function __name__, so these coroutine names must match the
# webhook's job constants (scan_pull_request / refresh_baseline).
async def refresh_baseline(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """arq job: re-cut a repo's baseline from a default-branch push."""
    count = await asyncio.to_thread(
        _refresh_impl,
        payload,
        github=ctx["github"],
        fetcher=DulwichFetcher(),
        session_factory=ctx["session_factory"],
        now=datetime.now(UTC),
    )
    return {"baseline_paths": count}


async def _on_startup(ctx: dict[str, Any]) -> None:
    config = SentinelConfig.from_env()
    engine = make_store_engine(config.database_url)
    ctx["github"] = GitHubApp(
        config.app_id, config.private_key_pem, api_base_url=config.api_base_url
    )
    ctx["session_factory"] = init_store(engine)


class WorkerSettings:
    """arq worker entrypoint: ``arq entrygraph.sentinel.queue.WorkerSettings``."""

    functions = [scan_pull_request, refresh_baseline]
    on_startup = _on_startup

    @staticmethod
    def redis_settings() -> RedisSettings:
        return RedisSettings.from_dsn(SentinelConfig.from_env().redis_url)
