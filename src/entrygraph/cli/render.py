"""Output rendering: aligned tables and JSON. No third-party deps."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Sequence


def to_json(obj: Any) -> str:
    def default(o):
        if is_dataclass(o):
            return asdict(o)
        raise TypeError(f"not serializable: {type(o)}")

    return json.dumps(obj, default=default, indent=2)


def render_table(rows: Sequence[dict], columns: Sequence[str]) -> str:
    if not rows:
        return "(no results)"
    widths = {c: len(c) for c in columns}
    string_rows: list[dict[str, str]] = []
    for row in rows:
        srow = {c: _cell(row.get(c)) for c in columns}
        string_rows.append(srow)
        for c in columns:
            widths[c] = max(widths[c], len(srow[c]))
    header = "  ".join(c.upper().ljust(widths[c]) for c in columns)
    sep = "  ".join("-" * widths[c] for c in columns)
    lines = [header, sep]
    for srow in string_rows:
        lines.append("  ".join(srow[c].ljust(widths[c]) for c in columns))
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
