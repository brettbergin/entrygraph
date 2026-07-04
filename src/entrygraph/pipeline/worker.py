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
from entrygraph.fs.hashing import hash_bytes
from entrygraph.fs.walker import WalkedFile
from entrygraph.parsing.parsers import parse, supported


def extract_one(
    walked: WalkedFile, include_tests: bool = False
) -> tuple[str, FileExtraction, bool, str] | None:
    """Parse+extract one file -> (path, extraction, is_package, content_hash), or
    None to skip. The hash is computed from the bytes read here so the diff phase
    doesn't read the same file a second time just to hash it.

    ``include_tests`` reaches the extractor so it can keep or drop in-file test
    code the walker's file-level gate can't see (Rust ``#[cfg(test)]``). #100"""
    language = walked.language
    if walked.skip_reason or not language or not supported(language):
        return None
    extractor = extractor_for(language)
    if extractor is None:
        return None
    try:
        source = Path(walked.abs_path).read_bytes()
    except OSError:
        return None
    content_hash = hash_bytes(source)
    module_path, is_package = extractor.module_path_for(walked.path)
    tree = parse(language, source)
    ctx = FileContext(
        path=walked.path,
        language=language,
        module_path=module_path,
        source=source,
        is_package=is_package,
        include_tests=include_tests,
    )
    return walked.path, extractor.extract(tree, ctx), is_package, content_hash


def extract_batch(
    batch: list[WalkedFile], include_tests: bool = False
) -> list[tuple[str, FileExtraction, bool, str]]:
    results = []
    for walked in batch:
        result = extract_one(walked, include_tests)
        if result is not None:
            results.append(result)
    return results
