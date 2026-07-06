"""Liveness, version, and the SPA's auth bootstrap."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from entrygraph.server.auth.deps import CurrentPrincipal

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/version")
def version() -> dict[str, str]:
    from entrygraph import __version__

    return {"version": __version__}


@router.get("/me")
def me(request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    config = request.app.state.config
    return {
        "user": {
            "id": principal.user_id,
            "name": principal.name,
            "role": principal.role,
            "via": principal.via,
        },
        "auth_mode": config.auth_mode,
        "auth_disabled": config.auth_mode == "none",
        "sentinel_enabled": bool(getattr(request.app.state, "sentinel_enabled", False)),
    }
