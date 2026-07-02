"""The only bridge between extraction IR and the ORM.

All inserts are SQLAlchemy 2.0 ORM-enabled bulk inserts —
``session.execute(insert(Model), list_of_dicts)`` — with application-assigned
integer primary keys so edge rows can reference symbol ids without RETURNING
round-trips. This is the fastest write path that still goes through the ORM.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from entrygraph.db.models import Base, Edge, Entrypoint, File, Symbol

_BATCH = 5000


class IdAllocator:
    """Per-table monotonically increasing ids, seeded from MAX(id)."""

    def __init__(self, session: Session) -> None:
        self._next: dict[str, int] = {}
        for model in (File, Symbol, Edge, Entrypoint):
            current = session.execute(select(func.max(model.id))).scalar() or 0
            self._next[model.__tablename__] = current + 1

    def take(self, model: type[Base]) -> int:
        table = model.__tablename__
        value = self._next[table]
        self._next[table] = value + 1
        return value


def bulk_insert(session: Session, model: type[Base], rows: Iterable[dict]) -> int:
    rows = list(rows)
    for start in range(0, len(rows), _BATCH):
        session.execute(insert(model), rows[start : start + _BATCH])
    return len(rows)
