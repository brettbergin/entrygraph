"""Durable app-DB models: users, sessions, api keys, jobs, repo sources.

These live on a **separate** DeclarativeBase from the graph tables on purpose:
the graph DB is a rebuildable cache whose schema-version mismatch handler drops
every table (:mod:`entrygraph.db.meta`), and nothing here may ever be at the
mercy of that. App rows reference graph repositories by ``root_path`` string —
stable across full rebuilds — never by cross-database foreign key.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class AppBase(DeclarativeBase):
    pass


class AppMeta(AppBase):
    """Key/value bookkeeping for the app DB (schema version, generated secrets)."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class User(AppBase):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sub: Mapped[str] = mapped_column(String(255), unique=True)  # OIDC sub; "dev:local" in dev
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(255))
    groups_json: Mapped[str] = mapped_column(Text, default="[]")
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # "admin" | "viewer"
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(AppBase):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex of cookie token
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(AppBase):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16))  # display: "egk_ab12…"
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the full key
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # capped at owner's role
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(AppBase):
    """One unit of background work (repo indexing, later gate runs)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4 hex
    type: Mapped[str] = mapped_column(String(32))  # "index"
    status: Mapped[str] = mapped_column(
        String(16), default="queued", index=True
    )  # queued|running|succeeded|failed|cancelled
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    repo_root: Mapped[str | None] = mapped_column(Text, index=True)  # graph root_path (string)
    repo_id: Mapped[int | None] = mapped_column(Integer)  # informational snapshot
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    phase: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    stats_json: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    worker_token: Mapped[str | None] = mapped_column(String(32))  # runner boot id (recovery)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepoSource(AppBase):
    """Durable origin of a registered repo — what makes UI "Reindex" possible.

    The graph DB's Repository row stores only ``root_path``; the URL/ref it was
    cloned from lives here, surviving graph rebuilds."""

    __tablename__ = "repo_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    root_path: Mapped[str] = mapped_column(Text, unique=True)
    url: Mapped[str | None] = mapped_column(Text)  # NULL for a local-path repo
    ref: Mapped[str | None] = mapped_column(String(255))
    depth: Mapped[int] = mapped_column(Integer, default=1)
    include_tests: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
