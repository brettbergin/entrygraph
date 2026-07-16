"""API-key management."""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("authlib")

from fastapi.testclient import TestClient
from sqlalchemy import select

from entrygraph.server.app import create_app
from entrygraph.server.config import ServerConfig
from entrygraph.server.models import ApiKey, User

OIDC_ENV = {
    "EG_OIDC_ISSUER": "https://auth.example.com/application/o/entrygraph",
    "EG_OIDC_CLIENT_ID": "cid",
    "EG_OIDC_CLIENT_SECRET": "sec",
    "EG_OIDC_ADMIN_GROUPS": "admins",
    "EG_SESSION_SECRET": "sign",
}


def _client(tmp_path) -> TestClient:
    cfg = ServerConfig.from_env(
        {"EG_DB": str(tmp_path / "g.db"), "EG_APP_DB": str(tmp_path / "a.db"), **OIDC_ENV}
    )
    return TestClient(create_app(cfg, serve_ui=False))


def _login(client: TestClient, claims: dict) -> None:
    async def fake(_request):
        return {"userinfo": claims}

    client.app.state.oauth.authentik.authorize_access_token = fake  # type: ignore[method-assign]
    assert client.get("/auth/callback?code=x&state=y", follow_redirects=False).status_code == 302


ADMIN = {"sub": "admin-1", "name": "Admin", "groups": ["admins"]}
VIEWER = {"sub": "viewer-1", "name": "Viewer", "groups": ["other"]}


def test_dev_mode_has_no_keys(tmp_path):
    cfg = ServerConfig.from_env(
        {"EG_DB": str(tmp_path / "g.db"), "EG_APP_DB": str(tmp_path / "a.db")}
    )
    with TestClient(create_app(cfg, serve_ui=False)) as c:
        assert c.get("/api/v1/api-keys").status_code == 400
        assert c.post("/api/v1/api-keys", json={"name": "x"}).status_code == 400


def test_create_list_revoke(tmp_path):
    with _client(tmp_path) as c:
        _login(c, ADMIN)
        created = c.post("/api/v1/api-keys", json={"name": "ci", "role": "viewer"})
        assert created.status_code == 201
        body = created.json()
        token = body["token"]
        assert token.startswith("egk_")
        assert body["key"]["prefix"] == token[:12]

        # the token works as a bearer credential
        fresh = TestClient(c.app)
        me = fresh.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
        assert me["user"]["via"] == "api_key"
        assert me["user"]["role"] == "viewer"

        listed = c.get("/api/v1/api-keys").json()["keys"]
        assert len(listed) == 1
        assert "token" not in listed[0]  # never shown again
        key_id = listed[0]["id"]

        # only the hash is stored
        with c.app.state.app_session_factory() as s:
            row = s.execute(select(ApiKey)).scalar_one()
            assert row.key_hash == hashlib.sha256(token.encode()).hexdigest()

        assert c.delete(f"/api/v1/api-keys/{key_id}").json()["revoked"] == key_id
        assert c.get("/api/v1/api-keys").json()["keys"] == []
        assert c.delete(f"/api/v1/api-keys/{key_id}").status_code == 404
        # a revoked key no longer authenticates
        assert (
            fresh.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        )


def test_cannot_grant_more_than_own_role(tmp_path):
    with _client(tmp_path) as c:
        _login(c, VIEWER)
        resp = c.post("/api/v1/api-keys", json={"name": "esc", "role": "admin"})
        assert resp.status_code == 403


def test_keys_are_per_user(tmp_path):
    with _client(tmp_path) as c:
        _login(c, ADMIN)
        c.post("/api/v1/api-keys", json={"name": "admin-key"})
        with c.app.state.app_session_factory() as s:
            other = User(sub="someone-else", role="admin")
            s.add(other)
            s.commit()
            other_id = other.id
            s.add(
                ApiKey(
                    user_id=other_id,
                    name="theirs",
                    prefix="egk_theirs0",
                    key_hash="deadbeef",
                    role="viewer",
                )
            )
            s.commit()
        # the logged-in admin sees only their own key
        listed = c.get("/api/v1/api-keys").json()["keys"]
        assert [k["name"] for k in listed] == ["admin-key"]
