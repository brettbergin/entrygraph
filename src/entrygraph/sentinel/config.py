"""Sentinel runtime configuration (#126).

All secrets — the GitHub App private key and the webhook secret — come from the
environment (or a mounted secret file), never from the database or a checked-in
file, and are never logged. ``SentinelConfig.redacted()`` is what any diagnostic
should print.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


@dataclass(frozen=True, slots=True)
class SentinelConfig:
    """Resolved settings for a Sentinel deployment.

    ``app_id`` and ``private_key_pem`` authenticate the GitHub App; ``webhook_secret``
    verifies inbound webhook HMACs. ``database_url`` and ``redis_url`` locate the
    findings store and the job queue. ``api_base_url`` is GitHub's REST root
    (overridable for GitHub Enterprise)."""

    app_id: str
    private_key_pem: str
    webhook_secret: str
    database_url: str = "sqlite:///sentinel.db"
    redis_url: str = "redis://localhost:6379"
    api_base_url: str = "https://api.github.com"
    # bearer token guarding the REST API; empty means the API is disabled
    # (fail-closed — every request 503s until a token is configured)
    api_token: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SentinelConfig:
        """Build config from environment variables.

        Required: ``SENTINEL_GITHUB_APP_ID``, ``SENTINEL_WEBHOOK_SECRET``, and the
        App private key via either ``SENTINEL_GITHUB_PRIVATE_KEY`` (PEM inline) or
        ``SENTINEL_GITHUB_PRIVATE_KEY_FILE`` (path to a PEM file). Optional:
        ``SENTINEL_DATABASE_URL``, ``SENTINEL_REDIS_URL``, ``SENTINEL_GITHUB_API_URL``."""
        env = env if env is not None else dict(os.environ)
        app_id = env.get("SENTINEL_GITHUB_APP_ID", "").strip()
        if not app_id:
            raise ConfigError("SENTINEL_GITHUB_APP_ID is required")
        secret = env.get("SENTINEL_WEBHOOK_SECRET", "")
        if not secret:
            raise ConfigError("SENTINEL_WEBHOOK_SECRET is required")
        private_key = cls._resolve_private_key(env)

        kwargs: dict[str, str] = {}
        if env.get("SENTINEL_DATABASE_URL"):
            kwargs["database_url"] = env["SENTINEL_DATABASE_URL"]
        if env.get("SENTINEL_REDIS_URL"):
            kwargs["redis_url"] = env["SENTINEL_REDIS_URL"]
        if env.get("SENTINEL_GITHUB_API_URL"):
            kwargs["api_base_url"] = env["SENTINEL_GITHUB_API_URL"].rstrip("/")
        if env.get("SENTINEL_API_TOKEN"):
            kwargs["api_token"] = env["SENTINEL_API_TOKEN"]
        return cls(
            app_id=app_id,
            private_key_pem=private_key,
            webhook_secret=secret,
            **kwargs,
        )

    @staticmethod
    def _resolve_private_key(env: dict[str, str]) -> str:
        inline = env.get("SENTINEL_GITHUB_PRIVATE_KEY", "")
        if inline.strip():
            return inline
        path = env.get("SENTINEL_GITHUB_PRIVATE_KEY_FILE", "").strip()
        if path:
            try:
                return Path(path).read_text()
            except OSError as exc:
                raise ConfigError(f"cannot read private key file {path!r}: {exc}") from exc
        raise ConfigError(
            "App private key required: set SENTINEL_GITHUB_PRIVATE_KEY (PEM) or "
            "SENTINEL_GITHUB_PRIVATE_KEY_FILE (path)"
        )

    def redacted(self) -> dict[str, str]:
        """A log-safe view: secrets are never included, only presence markers."""
        return {
            "app_id": self.app_id,
            "private_key_pem": "<set>" if self.private_key_pem else "<unset>",
            "webhook_secret": "<set>" if self.webhook_secret else "<unset>",
            "database_url": _redact_url(self.database_url),
            "redis_url": _redact_url(self.redis_url),
            "api_base_url": self.api_base_url,
        }


def _redact_url(url: str) -> str:
    """Strip any ``user:password@`` credentials from a connection URL for logging."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, _, host = rest.partition("@")
    if ":" in creds:
        return f"{scheme}://***@{host}"
    return url
