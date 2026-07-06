"""Server-side sessions behind an opaque HttpOnly cookie.

The cookie value is a 256-bit random token; only its sha256 lands in the DB,
so a database leak yields no usable credentials. Revocation is a row update —
logout actually works, unlike signed-cookie sessions.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from fastapi import Response
from sqlalchemy import select

from entrygraph.server.auth.deps import SESSION_COOKIE
from entrygraph.server.models import UserSession, utcnow


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    session_factory,
    user_id: int,
    *,
    ttl_hours: int,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Insert a session row and return the (never-stored) cookie token."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    with session_factory() as session:
        session.add(
            UserSession(
                token_hash=_hash_token(token),
                user_id=user_id,
                created_at=now,
                expires_at=now + timedelta(hours=ttl_hours),
                last_seen_at=now,
                ip=ip,
                user_agent=(user_agent or "")[:255] or None,
            )
        )
        session.commit()
    return token


def revoke_session(session_factory, token: str) -> bool:
    """Revoke the session for a cookie token; True if a live session was found."""
    with session_factory() as session:
        row = session.execute(
            select(UserSession).where(UserSession.token_hash == _hash_token(token))
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = utcnow()
        session.commit()
        return True


def set_session_cookie(response: Response, token: str, *, secure: bool, ttl_hours: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
