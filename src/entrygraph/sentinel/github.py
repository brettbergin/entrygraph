"""GitHub App authentication for Sentinel (#126).

An App authenticates in two steps: it signs a short-lived JWT with its private key
(proving it is the App), then exchanges that JWT for a per-installation access
token (scoped to one org/user's granted repos). Sentinel only ever requests the
minimum scopes it needs — ``contents:read``, ``checks:write``,
``pull_requests:read`` — and installation tokens are short-lived by construction.

The JWT builder is pure and unit-testable; the token exchange takes an injectable
``httpx.Client`` so tests drive it with a mock transport (no network).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import jwt

# GitHub rejects a JWT whose `iat` is in the future due to clock skew; backdate it
# a minute. Max allowed expiry is 10 minutes.
_JWT_BACKDATE = timedelta(seconds=60)
_JWT_TTL = timedelta(minutes=9)


class GitHubAuthError(RuntimeError):
    """The App JWT or installation-token exchange failed."""


def app_jwt(app_id: str, private_key_pem: str, *, now: datetime) -> str:
    """A signed App JWT valid from ~now for ~9 minutes (RS256).

    ``now`` is passed in (not read from the clock) so the token is deterministic
    and testable; callers use ``datetime.now(timezone.utc)``."""
    issued = now - _JWT_BACKDATE
    payload = {
        "iat": int(issued.timestamp()),
        "exp": int((now + _JWT_TTL).timestamp()),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


@dataclass(frozen=True, slots=True)
class InstallationToken:
    token: str
    expires_at: datetime


class GitHubApp:
    """Mints installation tokens for an App. Holds no per-installation state; the
    caller supplies the installation id."""

    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        *,
        api_base_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
    ) -> None:
        self._app_id = app_id
        self._private_key = private_key_pem
        self._api = api_base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=10.0)

    def installation_token(self, installation_id: int, *, now: datetime) -> InstallationToken:
        """Exchange an App JWT for a scoped installation access token.

        Requests only the permissions Sentinel uses, so a leaked token can do
        nothing but read code and write checks on the granted repos."""
        token = app_jwt(self._app_id, self._private_key, now=now)
        resp = self._client.post(
            f"{self._api}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "permissions": {
                    "contents": "read",
                    "checks": "write",
                    "pull_requests": "read",
                }
            },
        )
        if resp.status_code != 201:
            raise GitHubAuthError(
                f"installation token exchange failed ({resp.status_code}) for "
                f"installation {installation_id}"
            )
        body = resp.json()
        return InstallationToken(
            token=body["token"],
            expires_at=_parse_expiry(body.get("expires_at")),
        )


def _parse_expiry(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC) + timedelta(hours=1)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
