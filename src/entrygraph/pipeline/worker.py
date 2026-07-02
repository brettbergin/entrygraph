"""Process-pool extraction worker.

Workers parse and extract files into plain-data IR. tree-sitter parsers and
compiled queries are lazily cached per process (see parsing.parsers /
parsing.queries), so nothing unpicklable crosses the pool boundary — inputs are
WalkedFile records, outputs are FileExtraction dataclasses.
"""

from __future__ import annotations

from pathlib import Path

from entrygraph.extract.base import FileContext
from entrygraph.extract.ir import FileExtraction
from entrygraph.extract.registry import extractor_for
from entrygraph.fs.walker import WalkedFile
from entrygraph.parsing.parsers import parse, supported


def extract_one(walked: WalkedFile) -> tuple[str, FileExtraction, bool] | None:
    """Parse+extract one file -> (path, extraction, is_package), or None to skip."""
    if walked.skip_reason or not supported(walked.language or ""):
        return None
    extractor = extractor_for(walked.language)
    if extractor is None:
        return None
    try:
        source = Path(walked.abs_path).read_bytes()
    except OSError:
        return None
    module_path, is_package = extractor.module_path_for(walked.path)
    tree = parse(walked.language, source)
    ctx = FileContext(
        path=walked.path,
        language=walked.language,
        module_path=module_path,
        source=source,
        is_package=is_package,
    )
    return walked.path, extractor.extract(tree, ctx), is_package


def extract_batch(batch: list[WalkedFile]) -> list[tuple[str, FileExtraction, bool]]:
    results = []
    for walked in batch:
        result = extract_one(walked)
        if result is not None:
            results.append(result)
    return results
