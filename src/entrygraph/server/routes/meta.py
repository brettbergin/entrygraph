"""Liveness, version, and the SPA's auth bootstrap."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from entrygraph.server.auth.deps import CurrentPrincipal
from entrygraph.server.models import User

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
    # principal.name is the stable audit identity (the OIDC `sub`, which Authentik
    # can hash). For the UI, resolve the human display name + email from the User
    # row so the header shows "bergs", not a hashed sub. Dev/keyless principals
    # (no user_id) fall back to the principal name.
    display_name = principal.name
    email: str | None = None
    if principal.user_id is not None:
        with request.app.state.app_session_factory() as session:
            user = session.get(User, principal.user_id)
            if user is not None:
                display_name = user.name or user.email or user.sub
                email = user.email
    return {
        "user": {
            "id": principal.user_id,
            "name": display_name,
            "email": email,
            "role": principal.role,
            "via": principal.via,
        },
        "auth_mode": config.auth_mode,
        "auth_disabled": config.auth_mode == "none",
        "sentinel_enabled": bool(getattr(request.app.state, "sentinel_enabled", False)),
    }
