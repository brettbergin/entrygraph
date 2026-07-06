"""Unified server config (EG_* environment surface)."""

from __future__ import annotations

import pytest

from entrygraph.server.config import ConfigError, ServerConfig, origin_of


def test_defaults_are_local_dev():
    cfg = ServerConfig.from_env({})
    assert cfg.auth_mode == "none"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8100
    assert cfg.base_url == "http://127.0.0.1:8100"
    assert cfg.app_db_url.startswith("sqlite:///")
    assert cfg.local_paths_allowed  # dev mode defaults to allowing local paths
    assert not cfg.secure_cookies


def test_oidc_mode_inferred_from_issuer():
    cfg = ServerConfig.from_env(
        {
            "EG_OIDC_ISSUER": "https://auth.example.com/application/o/entrygraph/",
            "EG_OIDC_CLIENT_ID": "abc",
            "EG_OIDC_CLIENT_SECRET": "s3cret",
        }
    )
    assert cfg.auth_mode == "oidc"
    assert cfg.oidc_issuer == "https://auth.example.com/application/o/entrygraph"
    assert not cfg.local_paths_allowed  # oidc mode defaults local paths off


def test_oidc_mode_requires_client_credentials():
    with pytest.raises(ConfigError, match="EG_OIDC_CLIENT_ID"):
        ServerConfig.from_env({"EG_AUTH_MODE": "oidc", "EG_OIDC_ISSUER": "https://a.example"})
    with pytest.raises(ConfigError, match="client secret"):
        ServerConfig.from_env(
            {
                "EG_AUTH_MODE": "oidc",
                "EG_OIDC_ISSUER": "https://a.example",
                "EG_OIDC_CLIENT_ID": "abc",
            }
        )


def test_secret_file_resolution(tmp_path):
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    cfg = ServerConfig.from_env(
        {
            "EG_AUTH_MODE": "oidc",
            "EG_OIDC_ISSUER": "https://a.example",
            "EG_OIDC_CLIENT_ID": "abc",
            "EG_OIDC_CLIENT_SECRET_FILE": str(f),
        }
    )
    assert cfg.oidc_client_secret == "from-file"


def test_group_and_origin_lists_parse():
    cfg = ServerConfig.from_env(
        {
            "EG_OIDC_ADMIN_GROUPS": "platform-admins, security ",
            "EG_CORS_ORIGINS": "https://a.example,https://b.example",
        }
    )
    assert cfg.oidc_admin_groups == ("platform-admins", "security")
    assert cfg.cors_origins == ("https://a.example", "https://b.example")


def test_invalid_auth_mode_rejected():
    with pytest.raises(ConfigError, match="EG_AUTH_MODE"):
        ServerConfig.from_env({"EG_AUTH_MODE": "basic"})


def test_bind_safety_refuses_open_noauth():
    cfg = ServerConfig.from_env({"EG_HOST": "0.0.0.0"})
    with pytest.raises(ConfigError, match="refusing to serve"):
        cfg.check_bind_safety()
    # loopback is fine
    ServerConfig.from_env({}).check_bind_safety()
    # explicit override is honored
    ServerConfig.from_env({"EG_HOST": "0.0.0.0", "EG_AUTH_INSECURE": "1"}).check_bind_safety()


def test_redacted_never_leaks_secrets():
    cfg = ServerConfig.from_env(
        {
            "EG_AUTH_MODE": "oidc",
            "EG_OIDC_ISSUER": "https://a.example",
            "EG_OIDC_CLIENT_ID": "abc",
            "EG_OIDC_CLIENT_SECRET": "supersecret",
            "EG_SESSION_SECRET": "alsosecret",
            "EG_APP_DATABASE_URL": "postgresql://eg:dbpass@db/eg",
        }
    )
    dumped = str(cfg.redacted())
    assert "supersecret" not in dumped
    assert "alsosecret" not in dumped
    assert "dbpass" not in dumped
    assert cfg.redacted()["app_db_url"] == "postgresql://***@db/eg"


def test_origin_of():
    assert origin_of("https://eg.example.com:8443/some/path") == "https://eg.example.com:8443"
