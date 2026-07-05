"""Sentinel configuration loading + secret redaction (#126 M1)."""

from __future__ import annotations

import pytest

from entrygraph.sentinel.config import ConfigError, SentinelConfig, _redact_url

_BASE_ENV = {
    "SENTINEL_GITHUB_APP_ID": "12345",
    "SENTINEL_WEBHOOK_SECRET": "s3cret",
    "SENTINEL_GITHUB_PRIVATE_KEY": "-----BEGIN KEY-----\nabc\n-----END KEY-----",
}


def test_from_env_reads_required_fields():
    cfg = SentinelConfig.from_env(dict(_BASE_ENV))
    assert cfg.app_id == "12345"
    assert cfg.webhook_secret == "s3cret"
    assert "BEGIN KEY" in cfg.private_key_pem
    # defaults
    assert cfg.database_url.startswith("sqlite")
    assert cfg.api_base_url == "https://api.github.com"


@pytest.mark.parametrize("missing", ["SENTINEL_GITHUB_APP_ID", "SENTINEL_WEBHOOK_SECRET"])
def test_missing_required_field_raises(missing):
    env = dict(_BASE_ENV)
    del env[missing]
    with pytest.raises(ConfigError):
        SentinelConfig.from_env(env)


def test_private_key_from_file(tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN KEY-----\nfromfile\n-----END KEY-----")
    env = {
        "SENTINEL_GITHUB_APP_ID": "1",
        "SENTINEL_WEBHOOK_SECRET": "x",
        "SENTINEL_GITHUB_PRIVATE_KEY_FILE": str(key),
    }
    cfg = SentinelConfig.from_env(env)
    assert "fromfile" in cfg.private_key_pem


def test_missing_private_key_raises():
    env = {"SENTINEL_GITHUB_APP_ID": "1", "SENTINEL_WEBHOOK_SECRET": "x"}
    with pytest.raises(ConfigError):
        SentinelConfig.from_env(env)


def test_optional_overrides():
    env = dict(_BASE_ENV)
    env["SENTINEL_DATABASE_URL"] = "postgresql://u:p@db/sentinel"
    env["SENTINEL_REDIS_URL"] = "redis://cache:6379"
    env["SENTINEL_GITHUB_API_URL"] = "https://ghe.example.com/api/v3/"
    cfg = SentinelConfig.from_env(env)
    assert cfg.database_url == "postgresql://u:p@db/sentinel"
    assert cfg.redis_url == "redis://cache:6379"
    assert cfg.api_base_url == "https://ghe.example.com/api/v3"  # trailing slash stripped


def test_redacted_hides_secrets():
    cfg = SentinelConfig.from_env(
        {**_BASE_ENV, "SENTINEL_DATABASE_URL": "postgresql://user:pw@db/sentinel"}
    )
    red = cfg.redacted()
    assert red["private_key_pem"] == "<set>"
    assert red["webhook_secret"] == "<set>"
    # no secret material leaks anywhere in the redacted view
    blob = repr(red)
    assert "s3cret" not in blob
    assert "BEGIN KEY" not in blob
    assert "pw" not in red["database_url"]


def test_redact_url_strips_credentials():
    assert _redact_url("postgresql://user:pw@host/db") == "postgresql://***@host/db"
    assert _redact_url("redis://cache:6379") == "redis://cache:6379"  # no creds -> unchanged
    assert _redact_url("sqlite:///x.db") == "sqlite:///x.db"
