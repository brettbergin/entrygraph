"""Authentik OIDC: authorization-code flow handled entirely server-side.

The SPA never sees a token. ``/auth/login`` 302s to Authentik, the callback
exchanges the code (Authlib validates state/nonce and the ID token against the
issuer's JWKS), the user row is upserted with a role computed from the groups
claim, and an opaque session cookie is set. Starlette's SessionMiddleware
(signed cookie) carries only the transient OIDC state during the handshake.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from entrygraph.server.auth.deps import SESSION_COOKIE
from entrygraph.server.auth.sessions import (
    clear_session_cookie,
    create_session,
    revoke_session,
    set_session_cookie,
)
from entrygraph.server.config import ServerConfig
from entrygraph.server.models import User, utcnow

audit = logging.getLogger("entrygraph.server.audit")

router = APIRouter(prefix="/auth")


def make_oauth(config: ServerConfig):
    """The Authlib client registry for the configured Authentik issuer."""
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        "authentik",
        server_metadata_url=f"{config.oidc_issuer}/.well-known/openid-configuration",
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        client_kwargs={"scope": config.oidc_scopes},
    )
    return oauth


def safe_next(next_param: str | None) -> str:
    """Only same-origin absolute paths survive — no open redirects."""
    if not next_param or not next_param.startswith("/") or next_param.startswith("//"):
        return "/"
    return next_param


def role_for_groups(config: ServerConfig, groups: list[str]) -> str | None:
    """Map the Authentik groups claim to a role; None means access denied."""
    member = set(groups)
    if member & set(config.oidc_admin_groups):
        return "admin"
    if config.oidc_viewer_groups and not (member & set(config.oidc_viewer_groups)):
        return None
    return "viewer"


def upsert_user(session_factory, config: ServerConfig, claims: dict) -> User | None:
    """Create/update the user row from ID-token claims. None = access denied."""
    sub = claims.get("sub")
    if not sub:
        return None
    raw_groups = claims.get(config.oidc_groups_claim) or []
    groups = [str(g) for g in raw_groups] if isinstance(raw_groups, list) else [str(raw_groups)]
    role = role_for_groups(config, groups)
    if role is None:
        return None
    with session_factory() as session:
        user = session.execute(select(User).where(User.sub == sub)).scalar_one_or_none()
        if user is None:
            user = User(sub=sub)
            session.add(user)
        user.email = claims.get("email")
        user.name = claims.get("name") or claims.get("preferred_username") or sub
        user.groups_json = json.dumps(groups)
        user.role = role
        user.last_login_at = utcnow()
        session.commit()
        session.refresh(user)
        if user.disabled:
            return None
        return user


@router.get("/login")
async def login(request: Request, next: str | None = None):
    config = request.app.state.config
    if config.auth_mode == "none":
        return RedirectResponse(safe_next(next))
    request.session["next"] = safe_next(next)
    redirect_uri = f"{config.base_url}/auth/callback"
    oauth = request.app.state.oauth
    return await oauth.authentik.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request):
    config = request.app.state.config
    if config.auth_mode == "none":
        return RedirectResponse("/")
    oauth = request.app.state.oauth
    try:
        token = await oauth.authentik.authorize_access_token(request)
    except Exception as exc:
        audit.warning("oidc callback rejected: %s", exc)
        raise HTTPException(status_code=401, detail="OIDC authentication failed") from exc
    claims = dict(token.get("userinfo") or {})
    user = upsert_user(request.app.state.app_session_factory, config, claims)
    if user is None:
        audit.warning("oidc login denied sub=%s (groups/role)", claims.get("sub"))
        raise HTTPException(
            status_code=403,
            detail="your account is not authorized for entrygraph (group membership)",
        )
    cookie_token = create_session(
        request.app.state.app_session_factory,
        user.id,
        ttl_hours=config.session_ttl_hours,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    next_path = safe_next(request.session.pop("next", "/"))
    response = RedirectResponse(next_path, status_code=302)
    set_session_cookie(
        response, cookie_token, secure=config.secure_cookies, ttl_hours=config.session_ttl_hours
    )
    audit.info("login sub=%s role=%s", user.sub, user.role)
    return response


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        revoke_session(request.app.state.app_session_factory, token)
        audit.info("logout")
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response
