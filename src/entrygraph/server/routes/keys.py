"""API-key management for programmatic access (CLI/CI).

A key is ``egk_`` + random; only its sha256 is stored, and the full value is
shown exactly once at creation. A key's role is capped at its owner's role, and
in dev mode (no real user) keys aren't offered — the dev principal already has
full local access.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from entrygraph.server.auth.deps import API_KEY_PREFIX, CurrentPrincipal, current_principal
from entrygraph.server.models import ApiKey, utcnow

audit = logging.getLogger("entrygraph.server.audit")

router = APIRouter(dependencies=[Depends(current_principal)])

_ROLE_ORDER = {"viewer": 0, "admin": 1}


class CreateKey(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="viewer", pattern="^(admin|viewer)$")


def _require_user(principal: CurrentPrincipal) -> int:
    if principal.user_id is None:
        raise HTTPException(
            status_code=400,
            detail="API keys require a signed-in user; not available in dev (no-auth) mode",
        )
    return principal.user_id


def _key_json(k: ApiKey) -> dict[str, Any]:
    return {
        "id": k.id,
        "name": k.name,
        "prefix": k.prefix,
        "role": k.role,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
    }


@router.get("/api-keys")
def list_keys(request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    user_id = _require_user(principal)
    with request.app.state.app_session_factory() as session:
        rows = (
            session.execute(
                select(ApiKey)
                .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
                .order_by(ApiKey.created_at.desc())
            )
            .scalars()
            .all()
        )
        return {"keys": [_key_json(k) for k in rows]}


@router.post("/api-keys", status_code=201)
def create_key(request: Request, body: CreateKey, principal: CurrentPrincipal) -> dict[str, Any]:
    user_id = _require_user(principal)
    if _ROLE_ORDER[body.role] > _ROLE_ORDER[principal.role]:
        raise HTTPException(status_code=403, detail="cannot grant a key more than your own role")
    token = API_KEY_PREFIX + secrets.token_urlsafe(24)
    key = ApiKey(
        user_id=user_id,
        name=body.name,
        prefix=token[:12],
        key_hash=hashlib.sha256(token.encode()).hexdigest(),
        role=body.role,
        created_at=utcnow(),
    )
    with request.app.state.app_session_factory() as session:
        session.add(key)
        session.commit()
        session.refresh(key)
        payload = _key_json(key)
    audit.info("api-key create name=%s role=%s by=%s", body.name, body.role, principal.name)
    # the full token is returned exactly once — never stored, never shown again
    return {"key": payload, "token": token}


@router.delete("/api-keys/{key_id}")
def revoke_key(request: Request, key_id: int, principal: CurrentPrincipal) -> dict[str, Any]:
    user_id = _require_user(principal)
    with request.app.state.app_session_factory() as session:
        key = session.get(ApiKey, key_id)
        if key is None or key.user_id != user_id or key.revoked_at is not None:
            raise HTTPException(status_code=404, detail="key not found")
        key.revoked_at = utcnow()
        session.commit()
    audit.info("api-key revoke id=%d by=%s", key_id, principal.name)
    return {"revoked": key_id}
