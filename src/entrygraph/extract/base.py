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
