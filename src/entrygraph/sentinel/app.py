"""Deployable Sentinel ASGI app (#126, milestone 5).

Composes the webhook receiver (root) and the REST API (mounted at ``/api``) into a
single app, wired to the findings store and — in a real deployment — the arq
queue. ``uvicorn entrygraph.sentinel.app:app`` serves it.

``create_service_app`` takes injectable pieces so it is testable with an in-memory
queue and a SQLite store; ``build_from_env`` assembles the production wiring from
:class:`SentinelConfig`.
"""

from __future__ import annotations

from fastapi import FastAPI

from entrygraph.sentinel.api import create_api
from entrygraph.sentinel.config import SentinelConfig
from entrygraph.sentinel.webhook import ScanQueue, create_app


def create_service_app(
    config: SentinelConfig,
    *,
    session_factory,
    queue: ScanQueue | None = None,
) -> FastAPI:
    """Webhook at ``/`` + REST API at ``/api``, sharing one findings store."""
    service = create_app(config, queue=queue, session_factory=session_factory)
    service.mount("/api", create_api(config, session_factory))
    return service


def build_from_env() -> FastAPI:  # pragma: no cover - production wiring
    """Assemble the service from the environment: real store + arq queue."""
    import asyncio

    from entrygraph.sentinel.queue import make_queue
    from entrygraph.sentinel.store import init_store, make_store_engine

    config = SentinelConfig.from_env()
    session_factory = init_store(make_store_engine(config.database_url))
    queue = asyncio.get_event_loop().run_until_complete(make_queue(config))
    return create_service_app(config, session_factory=session_factory, queue=queue)
