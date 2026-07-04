"""Extractor protocol and shared tree-walking helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol

from entrygraph.extract.ir import FileExtraction, Span

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Node, Tree


@dataclass(slots=True)
class FileContext:
    path: str  # repo-relative
    language: str
    module_path: str
    source: bytes
    is_package: bool = False  # e.g. Python __init__.py


class LanguageExtractor(Protocol):
    language_ids: ClassVar[tuple[str, ...]]

    def module_path_for(self, repo_relative_path: str) -> tuple[str, bool]:
        """Return (module_path, is_package) for a repo-relative path."""
        ...

    def extract(self, tree: Tree, ctx: FileContext) -> FileExtraction:
        """Run queries + shaping. Must not raise on partial trees and must
        return plain-data IR only (no tree-sitter objects)."""
        ...


def node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def span_of(node: Node) -> Span:
    return Span(
        start_line=node.start_point.row + 1,
        start_col=node.start_point.column,
        end_line=node.end_point.row + 1,
        end_col=node.end_point.column,
    )


def truncate(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Root identifiers that plausibly denote a request/input accessor. Subscript and
# member reads rooted here (`params[:id]`, `req.body.name`, `$_GET["x"]`) are not
# calls, so they emit no source edge on their own; the extractors synthesize an
# accessor-read reference for these so the taint catalog can match them and the
# specific key can be surfaced (#87 part C). The catalog still decides whether a
# synthesized reference is actually a source — a non-matching root just yields an
# ordinary, untagged external edge.
ACCESSOR_ROOTS: frozenset[str] = frozenset(
    {
        "request",
        "req",
        "params",
        "$request",
        "$_GET",
        "$_POST",
        "$_REQUEST",
        "$_COOKIE",
        "$_SERVER",
    }
)


# Second-segment accessor names worth synthesizing a source read for
# (`req.<prop>...`, `request.<prop>...`). Curated so ordinary member reads like
# `req.method` don't spawn phantom edges; the catalog maps these to channels.
REQUEST_ACCESSOR_PROPS: frozenset[str] = frozenset(
    {"body", "query", "params", "headers", "cookies", "args", "form", "json"}
)


def _clean_ident(key: str) -> str | None:
    return (
        key
        if key and all(c.isalnum() or c in "_-." for c in key) and not key[0].isdigit()
        else None
    )


def member_key(text: str) -> str | None:
    """A member-access property name (`req.body.name` -> `name`). The property is
    always a literal name in source, so a bare identifier is accepted."""
    return _clean_ident(text.strip())


def subscript_key(text: str) -> str | None:
    """A subscript index reduced to a literal name for `source_key`.

    Only a *literal* key carries a stable name: a quoted string (`"q"`, `'q'`) or
    a Ruby symbol (`:id`). A bare identifier is a variable (`params[key]`) — a
    computed key with no fixed name — so it returns None.
    """
    key = text.strip()
    if key.startswith(":") and len(key) > 1:  # Ruby symbol literal
        return _clean_ident(key[1:])
    if len(key) >= 2 and key[0] in "\"'`" and key[-1] == key[0]:
        return _clean_ident(key[1:-1])
    return None  # bare identifier / expression -> computed key
