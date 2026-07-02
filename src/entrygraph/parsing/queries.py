"""Compiled tree-sitter query cache.

The single seam between entrygraph and the py-tree-sitter query API — if the
API shifts again (as it did at 0.24/0.25), this is the only file to update.
`.scm` sources ship as package data under entrygraph/queries/<lang>/.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files as resource_files

from tree_sitter import Node, Query, QueryCursor

from entrygraph.parsing.parsers import language


@cache
def load_query(lang_id: str, name: str) -> Query:
    source = (resource_files("entrygraph") / "queries" / lang_id / f"{name}.scm").read_text()
    return Query(language(lang_id), source)


def captures(query: Query, node: Node) -> dict[str, list[Node]]:
    return QueryCursor(query).captures(node)
