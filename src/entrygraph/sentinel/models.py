"""Sentinel-specific ORM tables (#126, milestone 2).

These reuse the core ``Base`` (and therefore the core ``MetaData``), so they only
exist in a database whose process has imported this module — a Sentinel
deployment. The core CLI never imports it, so its SQLite cache is unchanged and no
schema-version bump is required.

``Installation`` records a GitHub App installation; ``InstallationRepo`` maps an
installation to the central ``Repository`` row that owns its baselines/findings,
so every scan/finding/baseline query can be scoped by installation and an
uninstall can hard-delete all of an installation's data via the existing repo
cascade.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from entrygraph.db.models import Base


class Installation(Base):
    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # GitHub id
    account_login: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)


class InstallationRepo(Base):
    __tablename__ = "installation_repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    installation_id: Mapped[int] = mapped_column(ForeignKey("installations.id", ondelete="CASCADE"))
    # the central Repository row that owns this repo's baselines/findings; its
    # ondelete=CASCADE tears down graph + gate rows when the repo is removed
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    full_name: Mapped[str] = mapped_column(String(512))  # "octo/repo"

    __table_args__ = (
        UniqueConstraint("installation_id", "full_name", name="uq_installation_repo"),
    )
