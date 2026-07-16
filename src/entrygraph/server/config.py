"""Unified server runtime configuration.

Everything comes from ``EG_*`` environment variables (or a mounted secret
file), secrets are never logged, and ``ServerConfig.redacted()`` is what any
diagnostic should print.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


def _default_db() -> str:
    return str(Path.home() / ".entrygraph" / ".entrygraph.db")


def _default_app_db() -> str:
    return str(Path.home() / ".entrygraph" / "app.db")


def _default_clone_dir() -> str:
    return str(Path.home() / ".entrygraph" / "clones")


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Resolved settings for an ``entrygraph serve`` deployment.

    ``db_path`` is the rebuildable graph index; ``app_db`` holds durable state
    (users, sessions, api keys, jobs, repo sources) and is never dropped by a
    graph schema bump. ``auth_mode`` is ``"none"`` (local dev; synthetic admin)
    or ``"oidc"`` (Authentik authorization-code flow).
    """

    db_path: str = field(default_factory=_default_db)
    app_db_url: str = ""  # resolved in from_env / __post_init__ paths
    host: str = "127.0.0.1"
    port: int = 8100
    base_url: str = ""  # external URL; defaults to http://{host}:{port}
    auth_mode: str = "none"  # "none" | "oidc"
    auth_insecure: bool = False  # allow auth=none on a non-loopback bind
    # --- OIDC (Authentik) ---
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email groups"
    oidc_groups_claim: str = "groups"
    oidc_admin_groups: tuple[str, ...] = ()
    oidc_viewer_groups: tuple[str, ...] = ()  # empty = any authenticated user
    session_secret: str = ""  # signs the transient OIDC-state cookie
    session_ttl_hours: int = 72
    # --- jobs / clones ---
    jobs_concurrency: int = 1
    clone_dir: str = field(default_factory=_default_clone_dir)
    git_timeout_s: int = 600
    allowed_git_hosts: tuple[str, ...] = ()  # empty = any host
    allow_local_paths: bool | None = None  # None = default (true iff auth none)
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ServerConfig:
        env = env if env is not None else dict(os.environ)

        def get(name: str, default: str = "") -> str:
            return env.get(name, default).strip()

        def get_list(name: str) -> tuple[str, ...]:
            return tuple(v.strip() for v in env.get(name, "").split(",") if v.strip())

        def get_int(name: str, default: int) -> int:
            raw = get(name)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc

        issuer = get("EG_OIDC_ISSUER").rstrip("/")
        auth_mode = get("EG_AUTH_MODE") or ("oidc" if issuer else "none")
        if auth_mode not in ("none", "oidc"):
            raise ConfigError(f"EG_AUTH_MODE must be 'none' or 'oidc', got {auth_mode!r}")
        if auth_mode == "oidc":
            if not issuer:
                raise ConfigError("EG_OIDC_ISSUER is required when EG_AUTH_MODE=oidc")
            if not get("EG_OIDC_CLIENT_ID"):
                raise ConfigError("EG_OIDC_CLIENT_ID is required when EG_AUTH_MODE=oidc")
            if not cls._resolve_secret(env, "EG_OIDC_CLIENT_SECRET"):
                raise ConfigError(
                    "OIDC client secret required: set EG_OIDC_CLIENT_SECRET or "
                    "EG_OIDC_CLIENT_SECRET_FILE"
                )

        host = get("EG_HOST") or "127.0.0.1"
        port = get_int("EG_PORT", 8100)
        app_db = get("EG_APP_DATABASE_URL") or ""
        if not app_db:
            path = get("EG_APP_DB") or _default_app_db()
            app_db = f"sqlite:///{path}"

        allow_local_raw = get("EG_ALLOW_LOCAL_PATHS").lower()
        allow_local: bool | None
        if allow_local_raw in ("1", "true", "yes"):
            allow_local = True
        elif allow_local_raw in ("0", "false", "no"):
            allow_local = False
        elif allow_local_raw:
            raise ConfigError(f"EG_ALLOW_LOCAL_PATHS must be a boolean, got {allow_local_raw!r}")
        else:
            allow_local = None

        return cls(
            db_path=get("EG_DB") or _default_db(),
            app_db_url=app_db,
            host=host,
            port=port,
            base_url=(get("EG_BASE_URL") or f"http://{host}:{port}").rstrip("/"),
            auth_mode=auth_mode,
            auth_insecure=get("EG_AUTH_INSECURE") in ("1", "true", "yes"),
            oidc_issuer=issuer,
            oidc_client_id=get("EG_OIDC_CLIENT_ID"),
            oidc_client_secret=cls._resolve_secret(env, "EG_OIDC_CLIENT_SECRET"),
            oidc_scopes=get("EG_OIDC_SCOPES") or "openid profile email groups",
            oidc_groups_claim=get("EG_OIDC_GROUPS_CLAIM") or "groups",
            oidc_admin_groups=get_list("EG_OIDC_ADMIN_GROUPS"),
            oidc_viewer_groups=get_list("EG_OIDC_VIEWER_GROUPS"),
            session_secret=cls._resolve_secret(env, "EG_SESSION_SECRET"),
            session_ttl_hours=get_int("EG_SESSION_TTL_HOURS", 72),
            jobs_concurrency=max(1, get_int("EG_JOBS_CONCURRENCY", 1)),
            clone_dir=get("EG_CLONE_DIR") or _default_clone_dir(),
            git_timeout_s=get_int("EG_GIT_TIMEOUT_S", 600),
            allowed_git_hosts=get_list("EG_ALLOWED_GIT_HOSTS"),
            allow_local_paths=allow_local,
            cors_origins=get_list("EG_CORS_ORIGINS"),
        )

    @staticmethod
    def _resolve_secret(env: dict[str, str], name: str) -> str:
        inline = env.get(name, "")
        if inline.strip():
            return inline.strip()
        path = env.get(f"{name}_FILE", "").strip()
        if path:
            try:
                return Path(path).read_text().strip()
            except OSError as exc:
                raise ConfigError(f"cannot read secret file {path!r}: {exc}") from exc
        return ""

    @property
    def local_paths_allowed(self) -> bool:
        if self.allow_local_paths is not None:
            return self.allow_local_paths
        return self.auth_mode == "none"

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    def check_bind_safety(self) -> None:
        """Refuse auth=none on a non-loopback bind unless explicitly allowed —
        preserves the zero-setup local experience while making accidental open
        deployments hard."""
        if self.auth_mode == "none" and not _is_loopback(self.host) and not self.auth_insecure:
            raise ConfigError(
                f"refusing to serve without authentication on non-loopback host "
                f"{self.host!r}; configure Authentik OIDC (EG_OIDC_ISSUER, "
                f"EG_OIDC_CLIENT_ID, EG_OIDC_CLIENT_SECRET) or set EG_AUTH_INSECURE=1 "
                f"if you really mean it"
            )

    def redacted(self) -> dict[str, str]:
        """A log-safe view: secrets are presence markers only."""
        return {
            "db_path": self.db_path,
            "app_db_url": _redact_url(self.app_db_url),
            "host": self.host,
            "port": str(self.port),
            "base_url": self.base_url,
            "auth_mode": self.auth_mode,
            "oidc_issuer": self.oidc_issuer,
            "oidc_client_id": self.oidc_client_id,
            "oidc_client_secret": "<set>" if self.oidc_client_secret else "<unset>",
            "session_secret": "<set>" if self.session_secret else "<unset>",
            "jobs_concurrency": str(self.jobs_concurrency),
            "clone_dir": self.clone_dir,
            "allowed_git_hosts": ",".join(self.allowed_git_hosts) or "<any>",
            "allow_local_paths": str(self.local_paths_allowed),
            "cors_origins": ",".join(self.cors_origins) or "<same-origin>",
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


def origin_of(url: str) -> str:
    """The scheme://host[:port] origin of a URL, for CSRF Origin comparison."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
