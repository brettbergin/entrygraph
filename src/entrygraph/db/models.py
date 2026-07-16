"""SQLAlchemy 2.0 typed ORM models — the single definition of the on-disk schema.

Bump ``meta.SCHEMA_VERSION`` whenever anything here changes; the database is a
rebuildable cache, so a version mismatch triggers a full re-index rather than a
migration.

There are deliberately no relationship() constructs: all writes go through the
bulk-insert writer with app-assigned PKs, which inserts tables in dependency
order itself (repositories -> files -> symbols -> edges/entrypoints). Without
relationships the unit of work cannot order cross-table inserts within one
flush, so ad-hoc sessions must flush between dependency levels.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from entrygraph.kinds import EdgeKind, EntrypointKind, SymbolKind


def _values(enum_cls: type[enum.Enum]) -> list[str]:
    return [m.value for m in enum_cls]


class Base(DeclarativeBase):
    pass


class Meta(Base):
    """Key/value schema metadata; checked on every open()."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    root_path: Mapped[str] = mapped_column(Text, unique=True)
    indexed_at: Mapped[datetime | None]
    # Bumped once per index run; drives adjacency-cache invalidation.
    index_generation: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # app-assigned
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)  # repo-relative, posix separators
    language: Mapped[str | None] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))  # blake2b-128 hex
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)
    generation: Mapped[int] = mapped_column(Integer)  # index_generation at last (re)index
    skip_reason: Mapped[str | None] = mapped_column(
        String(32)
    )  # test/too_large/binary/minified/parse_error

    __table_args__ = (
        UniqueConstraint("repo_id", "path", name="uq_files_repo_path"),
        Index("ix_files_repo_lang", "repo_id", "language"),
    )


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # app-assigned
    # Owning repository. Denormalized onto the row (not only via file_id) so every
    # read can scope to one repo in a global multi-repo database, and external
    # placeholder symbols (file_id NULL) still carry a repo. (#116)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    # NULL for kind=external placeholder symbols (no defining file in the repo).
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    kind: Mapped[SymbolKind] = mapped_column(
        Enum(SymbolKind, native_enum=False, length=16, values_callable=_values)
    )
    name: Mapped[str] = mapped_column(Text)  # unqualified: "run"
    qname: Mapped[str] = mapped_column(Text)  # "pkg.mod.Class.run" / external: "py:subprocess.run"
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"))
    start_line: Mapped[int] = mapped_column(Integer, default=0)
    end_line: Mapped[int] = mapped_column(Integer, default=0)
    start_col: Mapped[int] = mapped_column(Integer, default=0)
    signature: Mapped[str | None] = mapped_column(Text)
    docstring: Mapped[str | None] = mapped_column(Text)
    is_exported: Mapped[bool] = mapped_column(default=True)
    # Resolved type reference for this symbol, kind-dependent (#98): FIELD/PROPERTY
    # = declared field type qname; VARIABLE/CONSTANT = module-level binding type;
    # FUNCTION/METHOD = resolved return type (#113). NULL when none was resolved.
    type_ref: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_symbols_file", "file_id"),
        Index("ix_symbols_repo_qname", "repo_id", "qname"),
        Index("ix_symbols_repo_kind_name", "repo_id", "kind", "name"),
        Index("ix_symbols_parent", "parent_id"),
    )


class Edge(Base):
    __tablename__ = "edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # app-assigned
    # Owning repository — denormalized for one-repo scoping in a global DB (#116).
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    kind: Mapped[EdgeKind] = mapped_column(
        Enum(EdgeKind, native_enum=False, length=16, values_callable=_values)
    )
    src_symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"))
    # SET NULL so a deleted target degrades the edge to "unresolved" instead of
    # deleting it; dst_qname keeps the textual target for later re-resolution.
    dst_symbol_id: Mapped[int | None] = mapped_column(ForeignKey("symbols.id", ondelete="SET NULL"))
    dst_qname: Mapped[str] = mapped_column(Text)
    # Denormalized so incremental re-index can wipe a file's edges in one statement.
    src_file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    line: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[int] = mapped_column(Integer, default=0)  # kinds.Confidence value
    arg_preview: Mapped[str | None] = mapped_column(Text)
    sink_id: Mapped[str | None] = mapped_column(String(64))  # pre-tagged at index time
    # A call to a taint-source function (request/env/stdin/...) tags its calling
    # edge; the src symbol is then a taint origin. Pre-tagged at index time.
    source_id: Mapped[str | None] = mapped_column(String(64))
    # The specific input identifier at a source call — query param / header /
    # flag name — extracted from the first string-literal argument (#87).
    source_key: Mapped[str | None] = mapped_column(String(128))
    # Edge provenance for edges not resolved by the direct import/scope pass:
    # "cha" (class-hierarchy candidate), "dynamic" (getattr/computed call),
    # "reexport" (chased through a barrel file). NULL for directly-resolved edges.
    via: Mapped[str | None] = mapped_column(String(12))

    __table_args__ = (
        Index("ix_edges_repo_kind", "repo_id", "kind"),
        Index("ix_edges_src_kind", "src_symbol_id", "kind"),
        Index("ix_edges_dst_kind", "dst_symbol_id", "kind"),
        Index("ix_edges_srcfile", "src_file_id"),
        Index("ix_edges_unresolved", "dst_qname", sqlite_where=text("dst_symbol_id IS NULL")),
        Index("ix_edges_sink", "sink_id", sqlite_where=text("sink_id IS NOT NULL")),
        Index("ix_edges_source", "source_id", sqlite_where=text("source_id IS NOT NULL")),
        Index("ix_edges_via", "via", sqlite_where=text("via IS NOT NULL")),
    )


class Entrypoint(Base):
    __tablename__ = "entrypoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # app-assigned
    # Owning repository — denormalized for one-repo scoping in a global DB (#116).
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    kind: Mapped[EntrypointKind] = mapped_column(
        Enum(EntrypointKind, native_enum=False, length=24, values_callable=_values)
    )
    framework: Mapped[str | None] = mapped_column(String(32))
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"))
    route: Mapped[str | None] = mapped_column(Text)
    http_method: Mapped[str | None] = mapped_column(String(16))
    extra: Mapped[str | None] = mapped_column(Text)  # JSON blob: decorator args etc.

    __table_args__ = (
        Index("ix_entrypoints_repo_kind_fw", "repo_id", "kind", "framework"),
        Index("ix_entrypoints_symbol", "symbol_id"),
    )


class Detection(Base):
    """Per-repo detected languages and frameworks."""

    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(16))  # "language" | "framework"
    name: Mapped[str] = mapped_column(String(48))
    version: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(default=1.0)
    evidence: Mapped[str | None] = mapped_column(Text)  # JSON: file counts, manifest paths

    __table_args__ = (UniqueConstraint("repo_id", "category", "name", name="uq_detections"),)
