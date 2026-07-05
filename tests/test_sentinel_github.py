"""GitHub App JWT + installation-token exchange (#126 M1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("jwt")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from entrygraph.sentinel.github import GitHubApp, GitHubAuthError, app_jwt


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def test_app_jwt_is_valid_and_signed(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    token = app_jwt("42", private_pem, now=now)
    # verify signature + claims against a fixed `now`; live-expiry is asserted
    # manually below, so don't reject the deterministic past-dated token
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_exp": False})
    assert decoded["iss"] == "42"
    # iat is backdated to tolerate clock skew; exp is within GitHub's 10-min cap
    assert decoded["iat"] < int(now.timestamp())
    assert 0 < decoded["exp"] - int(now.timestamp()) <= 600


def test_app_jwt_rejected_by_wrong_key(rsa_keypair):
    private_pem, _ = rsa_keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pub = (
        other.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    token = app_jwt("42", private_pem, now=datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, other_pub, algorithms=["RS256"])


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_installation_token_exchange_requests_min_scopes(rsa_keypair):
    private_pem, _ = rsa_keypair
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201, json={"token": "ghs_installtoken", "expires_at": "2026-01-01T13:00:00Z"}
        )

    app = GitHubApp("42", private_pem, client=_mock_client(handler))
    tok = app.installation_token(999, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert tok.token == "ghs_installtoken"
    assert captured["url"].endswith("/app/installations/999/access_tokens")
    assert captured["auth"].startswith("Bearer ")
    # only the scopes Sentinel actually needs are requested
    assert captured["body"]["permissions"] == {
        "contents": "read",
        "checks": "write",
        "pull_requests": "read",
    }


def test_installation_token_exchange_failure_raises(rsa_keypair):
    private_pem, _ = rsa_keypair

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    app = GitHubApp("42", private_pem, client=_mock_client(handler))
    with pytest.raises(GitHubAuthError):
        app.installation_token(999, now=datetime(2026, 1, 1, tzinfo=UTC))
