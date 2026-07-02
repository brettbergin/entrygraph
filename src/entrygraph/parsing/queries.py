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
def load_query_for(grammar_lang: str, source_lang: str, name: str) -> Query:
    """Compile a `.scm` source (shipped under queries/<source_lang>/) against a
    possibly-different grammar.

    A tree-sitter Query is bound to one Language and only matches trees from that
    same grammar. TypeScript/TSX are supersets of JavaScript with the same node
    names for the constructs we harvest, so their extractor reuses the JavaScript
    query sources compiled against the TS/TSX grammars — without this, a `.ts`
    tree queried with JS-compiled queries silently matches nothing.
    """
    source = (resource_files("entrygraph") / "queries" / source_lang / f"{name}.scm").read_text()
    return Query(language(grammar_lang), source)


@cache
def load_query(lang_id: str, name: str) -> Query:
    return load_query_for(lang_id, lang_id, name)


def captures(query: Query, node: Node) -> dict[str, list[Node]]:
    return QueryCursor(query).captures(node)
