"""Principal resolution — the single seam every route depends on.

Resolution order: ``Authorization: Bearer egk_…`` API key, then the
``eg_session`` cookie, then (auth mode ``none``) a synthetic local admin.
API keys and sessions are stored hashed; a DB leak yields no usable
credentials.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from entrygraph.server.models import ApiKey, User, UserSession, utcnow

SESSION_COOKIE = "eg_session"
API_KEY_PREFIX = "egk_"

_ROLE_ORDER = {"viewer": 0, "admin": 1}


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int | None
    name: str
    role: str  # "admin" | "viewer"
    via: str  # "session" | "api_key" | "dev"

    def has_role(self, role: str) -> bool:
        return _ROLE_ORDER.get(self.role, -1) >= _ROLE_ORDER.get(role, 99)


_DEV_PRINCIPAL = Principal(user_id=None, name="dev:local", role="admin", via="dev")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def current_principal(request: Request) -> Principal:
    config = request.app.state.config
    app_session_factory = request.app.state.app_session_factory

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token.startswith(API_KEY_PREFIX):
            principal = _api_key_principal(app_session_factory, token)
            if principal is not None:
                return principal
            raise HTTPException(status_code=401, detail="invalid API key")

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        principal = _session_principal(app_session_factory, cookie, config.session_ttl_hours)
        if principal is not None:
            return principal
        if config.auth_mode != "none":
            raise HTTPException(status_code=401, detail="session expired")

    if config.auth_mode == "none":
        return _DEV_PRINCIPAL
    raise HTTPException(status_code=401, detail="not authenticated")


def _api_key_principal(session_factory, token: str) -> Principal | None:
    now = utcnow()
    with session_factory() as session:
        key = session.execute(
            select(ApiKey).where(ApiKey.key_hash == _hash_token(token))
        ).scalar_one_or_none()
        if key is None or key.revoked_at is not None:
            return None
        if key.expires_at is not None and key.expires_at < now:
            return None
        user = session.get(User, key.user_id)
        if user is None or user.disabled:
            return None
        key.last_used_at = now
        session.commit()
        # a key never exceeds its owner's current role
        role = key.role if _ROLE_ORDER[key.role] <= _ROLE_ORDER[user.role] else user.role
        return Principal(user_id=user.id, name=f"{user.sub}#{key.name}", role=role, via="api_key")


def _session_principal(session_factory, token: str, ttl_hours: int) -> Principal | None:
    now = utcnow()
    with session_factory() as session:
        row = session.execute(
            select(UserSession).where(UserSession.token_hash == _hash_token(token))
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None or row.expires_at < now:
            return None
        user = session.get(User, row.user_id)
        if user is None or user.disabled:
            return None
        # sliding expiry, written at most once a minute to avoid a write per request
        if row.last_seen_at is None or (now - row.last_seen_at) > timedelta(minutes=1):
            row.last_seen_at = now
            row.expires_at = now + timedelta(hours=ttl_hours)
            session.commit()
        return Principal(user_id=user.id, name=user.sub, role=user.role, via="session")


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require_role(role: str):
    async def dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(role):
            raise HTTPException(status_code=403, detail=f"requires {role} role")
        return principal

    return dependency
