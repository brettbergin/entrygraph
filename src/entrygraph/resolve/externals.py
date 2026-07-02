"""Placeholder symbols for callees defined outside the repo.

Sinks are overwhelmingly external calls (subprocess.run, child_process.exec),
and reachability needs real graph nodes to terminate on — so the first time an
external qualified name is referenced we mint a Symbol row with kind=external
and no file. Created lazily from actual references, never pre-seeded.
"""

from __future__ import annotations

from typing import Callable

from entrygraph.kinds import SymbolKind

LANG_PREFIX = {
    "python": "py",
    "javascript": "js",
    "typescript": "js",
    "tsx": "js",
    "go": "go",
    "java": "java",
    "ruby": "rb",
    "csharp": "cs",
    "php": "php",
    "rust": "rs",
}


class ExternalRegistry:
    def __init__(self, allocate_id: Callable[[], int]) -> None:
        self._allocate_id = allocate_id
        self.by_qname: dict[str, int] = {}
        self.new_rows: list[dict] = []  # Symbol insert dicts, drained by the writer

    def preload(self, existing: dict[str, int]) -> None:
        """Seed with external symbols already in the DB (incremental runs)."""
        self.by_qname.update(existing)

    def get_or_create(self, qname: str) -> int:
        symbol_id = self.by_qname.get(qname)
        if symbol_id is not None:
            return symbol_id
        symbol_id = self._allocate_id()
        self.by_qname[qname] = symbol_id
        self.new_rows.append(
            {
                "id": symbol_id,
                "file_id": None,
                "kind": SymbolKind.EXTERNAL,
                "name": qname.split(":", 1)[-1].rsplit(".", 1)[-1],
                "qname": qname,
                "parent_id": None,
                "start_line": 0,
                "end_line": 0,
                "start_col": 0,
                "signature": None,
                "docstring": None,
                "is_exported": True,
            }
        )
        return symbol_id
