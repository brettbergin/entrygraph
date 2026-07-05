"""SELECT builders and row -> DTO conversion.

Globs use ``*`` and ``?`` (translated to SQL LIKE with escaping); a pattern
without glob characters is an exact match.
"""

from __future__ import annotations

import json

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from entrygraph.db import models
from entrygraph.kinds import EntrypointKind, SymbolKind
from entrygraph.results import Entrypoint, FileInfo, Symbol

_LIKE_ESCAPE = "\\"


def glob_to_like(pattern: str) -> str:
    escaped = (
        pattern.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return escaped.replace("*", "%").replace("?", "_")


def _match(column, pattern: str):
    if "*" in pattern or "?" in pattern:
        return column.like(glob_to_like(pattern), escape=_LIKE_ESCAPE)
    return column == pattern


def symbol_to_dto(row: models.Symbol, file_path: str | None) -> Symbol:
    return Symbol(
        id=row.id,
        kind=row.kind.value,
        name=row.name,
        qname=row.qname,
        file=file_path,
        start_line=row.start_line,
        end_line=row.end_line,
        signature=row.signature,
        docstring=row.docstring,
        is_exported=row.is_exported,
    )


def _symbol_select() -> Select:
    return select(models.Symbol, models.File.path).join(
        models.File, models.Symbol.file_id == models.File.id, isouter=True
    )


def select_symbols(
    session: Session,
    repo_id: int,
    *,
    kind: str | SymbolKind | None = None,
    name: str | None = None,
    qname: str | None = None,
    file: str | None = None,
    include_external: bool = False,
    limit: int | None = None,
    offset: int | None = None,
    after: tuple[str, int] | None = None,
) -> list[Symbol]:
    # (qname, id) is a total order (id breaks qname ties), so `after` supports
    # keyset pagination: WHERE (qname, id) > (:aq, :ai) walks ix_symbols_repo_qname
    # directly, unlike OFFSET which rescans and discards all prior rows (O(N^2)
    # over a full iteration).
    stmt = (
        _symbol_select()
        .where(models.Symbol.repo_id == repo_id)
        .order_by(models.Symbol.qname, models.Symbol.id)
    )
    if kind is not None:
        stmt = stmt.where(models.Symbol.kind == SymbolKind(kind))
    elif not include_external:
        stmt = stmt.where(models.Symbol.kind != SymbolKind.EXTERNAL)
    if name is not None:
        stmt = stmt.where(_match(models.Symbol.name, name))
    if qname is not None:
        stmt = stmt.where(_match(models.Symbol.qname, qname))
    if file is not None:
        stmt = stmt.where(_match(models.File.path, file))
    if after is not None:
        aq, ai = after
        stmt = stmt.where(
            (models.Symbol.qname > aq) | ((models.Symbol.qname == aq) & (models.Symbol.id > ai))
        )
    if limit is not None:
        stmt = stmt.limit(limit)
    if offset is not None:
        stmt = stmt.offset(offset)
    return [symbol_to_dto(sym, path) for sym, path in session.execute(stmt)]


def symbols_by_ids(session: Session, repo_id: int, ids: set[int]) -> dict[int, Symbol]:
    if not ids:
        return {}
    stmt = _symbol_select().where(models.Symbol.repo_id == repo_id, models.Symbol.id.in_(ids))
    return {sym.id: symbol_to_dto(sym, path) for sym, path in session.execute(stmt)}


def symbol_ids_matching(session: Session, repo_id: int, pattern: str) -> set[int]:
    """Symbol ids whose qname matches a glob (or exact qname)."""
    return set(
        session.execute(
            select(models.Symbol.id).where(
                models.Symbol.repo_id == repo_id, _match(models.Symbol.qname, pattern)
            )
        ).scalars()
    )


def select_files(
    session: Session, repo_id: int, *, language: str | None = None, path: str | None = None
) -> list[FileInfo]:
    stmt = select(models.File).where(models.File.repo_id == repo_id).order_by(models.File.path)
    if language is not None:
        stmt = stmt.where(models.File.language == language)
    if path is not None:
        stmt = stmt.where(_match(models.File.path, path))
    return [
        FileInfo(
            id=f.id,
            path=f.path,
            language=f.language,
            size_bytes=f.size_bytes,
            skip_reason=f.skip_reason,
        )
        for f in session.execute(stmt).scalars()
    ]


def select_entrypoints(
    session: Session,
    repo_id: int,
    *,
    kind: str | EntrypointKind | None = None,
    framework: str | None = None,
    route: str | None = None,
    limit: int | None = None,
) -> list[Entrypoint]:
    stmt = (
        select(models.Entrypoint, models.Symbol, models.File.path)
        .join(models.Symbol, models.Entrypoint.symbol_id == models.Symbol.id)
        .join(models.File, models.Symbol.file_id == models.File.id, isouter=True)
        .where(models.Entrypoint.repo_id == repo_id)
        .order_by(models.Entrypoint.id)
    )
    if kind is not None:
        stmt = stmt.where(models.Entrypoint.kind == EntrypointKind(kind))
    if framework is not None:
        stmt = stmt.where(models.Entrypoint.framework == framework)
    if route is not None:
        stmt = stmt.where(_match(models.Entrypoint.route, route))
    if limit is not None:
        stmt = stmt.limit(limit)
    return [
        Entrypoint(
            id=ep.id,
            kind=ep.kind.value,
            framework=ep.framework,
            symbol=symbol_to_dto(sym, path),
            route=ep.route,
            http_method=ep.http_method,
            extra=json.loads(ep.extra) if ep.extra else {},
        )
        for ep, sym, path in session.execute(stmt)
    ]
