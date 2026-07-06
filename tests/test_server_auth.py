"""Authentik OIDC auth: login flow (stubbed issuer), sessions, roles, CSRF,
and the API-key path through the same principal seam."""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("authlib")

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select

from entrygraph.server.app import create_app
from entrygraph.server.auth.oidc import role_for_groups, safe_next, upsert_user
from entrygraph.server.auth.sessions import create_session, revoke_session
from entrygraph.server.config import ServerConfig
from entrygraph.server.models import ApiKey, User, UserSession, utcnow

OIDC_ENV = {
    "EG_OIDC_ISSUER": "https://auth.example.com/application/o/entrygraph",
    "EG_OIDC_CLIENT_ID": "entrygraph-client",
    "EG_OIDC_CLIENT_SECRET": "s3cret",
    "EG_OIDC_ADMIN_GROUPS": "platform-admins",
    "EG_SESSION_SECRET": "test-signing-secret",
}


def _config(tmp_path, extra: dict | None = None) -> ServerConfig:
    env = {
        "EG_DB": str(tmp_path / "graph.db"),
        "EG_APP_DB": str(tmp_path / "app.db"),
        **OIDC_ENV,
        **(extra or {}),
    }
    return ServerConfig.from_env(env)


@pytest.fixture()
def oidc_client(tmp_path) -> TestClient:
    with TestClient(create_app(_config(tmp_path), serve_ui=False)) as c:
        yield c


def _login_as(client: TestClient, claims: dict) -> None:
    """Complete the OIDC dance with a stubbed token exchange."""

    async def fake_authorize_access_token(_request):
        return {"userinfo": claims}

    oauth_app = client.app.state.oauth.authentik
    oauth_app.authorize_access_token = fake_authorize_access_token  # type: ignore[method-assign]
    resp = client.get("/auth/callback?code=x&state=y", follow_redirects=False)
    assert resp.status_code == 302, resp.text


ADMIN_CLAIMS = {
    "sub": "ak-user-1",
    "email": "brett@example.com",
    "name": "Brett",
    "groups": ["platform-admins", "engineering"],
}
VIEWER_CLAIMS = {"sub": "ak-user-2", "email": "v@example.com", "groups": ["engineering"]}


# ---------------- pure helpers ----------------


def test_role_mapping():
    cfg = _config.__wrapped__ if hasattr(_config, "__wrapped__") else None  # noqa: F841
    config = ServerConfig.from_env({**OIDC_ENV})
    assert role_for_groups(config, ["platform-admins"]) == "admin"
    assert role_for_groups(config, ["engineering"]) == "viewer"
    # viewer allowlist gates access entirely
    gated = ServerConfig.from_env({**OIDC_ENV, "EG_OIDC_VIEWER_GROUPS": "eng-read"})
    assert role_for_groups(gated, ["eng-read"]) == "viewer"
    assert role_for_groups(gated, ["unrelated"]) is None
    assert role_for_groups(gated, ["platform-admins"]) == "admin"


def test_safe_next_blocks_open_redirects():
    assert safe_next("/repos/3?tab=graph") == "/repos/3?tab=graph"
    assert safe_next("https://evil.example") == "/"
    assert safe_next("//evil.example") == "/"
    assert safe_next(None) == "/"


# ---------------- login flow ----------------


def test_unauthenticated_api_is_401(oidc_client):
    assert oidc_client.get("/api/v1/me").status_code == 401
    assert oidc_client.get("/api/v1/repos").status_code == 401


def test_login_redirects_to_issuer(oidc_client, monkeypatch):
    oauth_app = oidc_client.app.state.oauth.authentik

    async def fake_metadata(*_a, **_k):
        return {"authorization_endpoint": "https://auth.example.com/application/o/authorize/"}

    monkeypatch.setattr(oauth_app, "load_server_metadata", fake_metadata)
    resp = oidc_client.get("/auth/login?next=/repos/1", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://auth.example.com/application/o/authorize/")
    assert "client_id=entrygraph-client" in location
    assert "state=" in location


def test_callback_creates_user_session_and_cookie(oidc_client):
    _login_as(oidc_client, ADMIN_CLAIMS)
    # cookie flags: HttpOnly + SameSite Lax (not Secure — http base URL)
    raw = None
    for name, value in oidc_client.cookies.items():
        if name == "eg_session":
            raw = value
    assert raw

    me = oidc_client.get("/api/v1/me").json()
    assert me["user"]["name"] == "ak-user-1"
    assert me["user"]["role"] == "admin"
    assert me["user"]["via"] == "session"
    assert me["auth_disabled"] is False

    # the DB stores only the hash of the cookie token
    with oidc_client.app.state.app_session_factory() as s:
        row = s.execute(select(UserSession)).scalar_one()
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert row.token_hash != raw
        user = s.execute(select(User)).scalar_one()
        assert user.email == "brett@example.com"
        assert user.role == "admin"


def test_group_denial_is_403(tmp_path):
    cfg = _config(tmp_path, {"EG_OIDC_VIEWER_GROUPS": "eg-users"})
    with TestClient(create_app(cfg, serve_ui=False)) as c:

        async def fake(_request):
            return {"userinfo": {"sub": "outsider", "groups": ["randoms"]}}

        c.app.state.oauth.authentik.authorize_access_token = fake  # type: ignore[method-assign]
        resp = c.get("/auth/callback?code=x&state=y", follow_redirects=False)
        assert resp.status_code == 403


def test_viewer_cannot_mutate(oidc_client):
    _login_as(oidc_client, VIEWER_CLAIMS)
    assert oidc_client.get("/api/v1/repos").status_code == 200
    resp = oidc_client.post("/api/v1/repos", json={"source": "https://github.com/o/r.git"})
    assert resp.status_code == 403


def test_logout_revokes_session(oidc_client):
    _login_as(oidc_client, ADMIN_CLAIMS)
    assert oidc_client.get("/api/v1/me").status_code == 200
    assert oidc_client.post("/auth/logout").json() == {"ok": True}
    assert oidc_client.get("/api/v1/me").status_code == 401


def test_bad_callback_is_401(oidc_client):
    async def boom(_request):
        raise ValueError("state mismatch")

    oidc_client.app.state.oauth.authentik.authorize_access_token = boom  # type: ignore[method-assign]
    assert oidc_client.get("/auth/callback?code=x&state=bad").status_code == 401


# ---------------- sessions ----------------


def test_expired_and_revoked_sessions_reject(oidc_client):
    _login_as(oidc_client, ADMIN_CLAIMS)
    factory = oidc_client.app.state.app_session_factory
    with factory() as s:
        row = s.execute(select(UserSession)).scalar_one()
        row.expires_at = utcnow() - timedelta(hours=1)
        s.commit()
    assert oidc_client.get("/api/v1/me").status_code == 401


def test_revoke_session_helper(tmp_path):
    cfg = _config(tmp_path)
    app = create_app(cfg, serve_ui=False)
    factory = app.state.app_session_factory
    with factory() as s:
        s.add(User(sub="u1", role="viewer"))
        s.commit()
        uid = s.execute(select(User.id)).scalar()
    token = create_session(factory, uid, ttl_hours=1)
    assert revoke_session(factory, token) is True
    assert revoke_session(factory, token) is False  # already revoked
    assert revoke_session(factory, "nonexistent") is False


def test_disabled_user_rejected(oidc_client):
    _login_as(oidc_client, ADMIN_CLAIMS)
    with oidc_client.app.state.app_session_factory() as s:
        user = s.execute(select(User)).scalar_one()
        user.disabled = True
        s.commit()
    assert oidc_client.get("/api/v1/me").status_code == 401


# ---------------- API keys through the same seam ----------------


def test_api_key_bearer_works_and_respects_role_cap(oidc_client):
    _login_as(oidc_client, ADMIN_CLAIMS)
    factory = oidc_client.app.state.app_session_factory
    with factory() as s:
        uid = s.execute(select(User.id)).scalar()
        s.add(
            ApiKey(
                user_id=uid,
                name="ci",
                prefix="egk_test",
                key_hash=hashlib.sha256(b"egk_secret123").hexdigest(),
                role="viewer",
            )
        )
        s.commit()
    fresh = TestClient(oidc_client.app)  # no cookies
    headers = {"Authorization": "Bearer egk_secret123"}
    me = fresh.get("/api/v1/me", headers=headers).json()
    assert me["user"]["via"] == "api_key"
    assert me["user"]["role"] == "viewer"  # key role, not owner's admin
    denied = fresh.post(
        "/api/v1/repos", json={"source": "https://github.com/o/r.git"}, headers=headers
    )
    assert denied.status_code == 403
    assert fresh.get("/api/v1/me", headers={"Authorization": "Bearer egk_wrong"}).status_code == 401


# ---------------- CSRF ----------------


def test_csrf_blocks_cross_origin_cookie_mutations(oidc_client):
    _login_as(oidc_client, ADMIN_CLAIMS)
    resp = oidc_client.post(
        "/api/v1/repos",
        json={"source": "https://github.com/o/r.git"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert "cross-origin" in resp.json()["detail"]


def test_csrf_allows_same_origin_and_no_origin(oidc_client):
    _login_as(oidc_client, ADMIN_CLAIMS)
    # same-origin (testserver) — passes CSRF, fails later on validation (422)
    ok = oidc_client.post(
        "/api/v1/repos",
        json={"source": "not a url"},
        headers={"Origin": "http://testserver"},
    )
    assert ok.status_code == 422
    # non-browser client (no Origin/Referer) — passes CSRF
    ok2 = oidc_client.post("/api/v1/repos", json={"source": "not a url"})
    assert ok2.status_code == 422


# ---------------- upsert refresh ----------------


def test_upsert_refreshes_role_on_login(tmp_path):
    config = _config(tmp_path)
    app = create_app(config, serve_ui=False)
    factory = app.state.app_session_factory
    u1 = upsert_user(factory, config, ADMIN_CLAIMS)
    assert u1 is not None and u1.role == "admin"
    demoted = {**ADMIN_CLAIMS, "groups": ["engineering"]}
    u2 = upsert_user(factory, config, demoted)
    assert u2 is not None and u2.role == "viewer"
    with factory() as s:
        assert s.execute(select(User)).scalar_one().role == "viewer"
