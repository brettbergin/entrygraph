"""entrygraph — query your codebase like a graph.

Index a repository into SQLite (via the SQLAlchemy ORM) and query symbols,
classes, methods, entrypoints, and source-to-sink call paths across languages.

    from entrygraph import CodeGraph

    graph = CodeGraph.index("/path/to/repo")
    graph.entrypoints(framework="flask")
    graph.paths(source="app.routes.*", sink_category="command_exec")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from entrygraph.errors import (
    DatabaseNotFoundError,
    EntrygraphError,
    SchemaMismatchError,
    SymbolNotFoundError,
)
from entrygraph.kinds import Confidence, EdgeKind, EntrypointKind, SymbolKind
from entrygraph.results import (
    CallPath,
    DetectedFramework,
    DetectedLanguage,
    DetectionReport,
    Edge,
    Entrypoint,
    FileInfo,
    GraphStats,
    IndexStats,
    PathEdge,
    Symbol,
)

__version__ = "0.1.0"

if TYPE_CHECKING:  # pragma: no cover
    from entrygraph.api import CodeGraph

_LAZY = {"CodeGraph": ("entrygraph.api", "CodeGraph")}


def __getattr__(name: str) -> Any:
    # CodeGraph pulls in the full indexing stack; keep `import entrygraph` light.
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CallPath",
    "CodeGraph",
    "Confidence",
    "DatabaseNotFoundError",
    "DetectedFramework",
    "DetectedLanguage",
    "DetectionReport",
    "Edge",
    "EdgeKind",
    "Entrypoint",
    "EntrypointKind",
    "EntrygraphError",
    "FileInfo",
    "GraphStats",
    "IndexStats",
    "PathEdge",
    "SchemaMismatchError",
    "Symbol",
    "SymbolKind",
    "SymbolNotFoundError",
    "__version__",
]
