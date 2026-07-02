"""Robustness: malformed input, skipped files, empty repos, self-index."""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make(root: Path, rel: str, content: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def test_syntax_error_file_still_indexes(tmp_path):
    _make(tmp_path, "good.py", b"def ok():\n    return 1\n")
    _make(tmp_path, "broken.py", b"def broken(:\n    this is not python !!!\n")
    with CodeGraph.index(tmp_path, db=tmp_path / "g.db") as g:
        qnames = {s.qname for s in g.symbols()}
        assert "good.ok" in qnames  # the healthy file is unaffected
        # broken.py is recorded as a file even if extraction is partial
        assert any(f.path == "broken.py" for f in g.files())


def test_binary_and_minified_skipped(tmp_path):
    _make(tmp_path, "app.py", b"x = 1\n")
    _make(tmp_path, "blob.py", b"\x00\x01\x02\x03 not text")
    _make(tmp_path, "vendor.min.js", b"var a=1;")
    with CodeGraph.index(tmp_path, db=tmp_path / "g.db") as g:
        skips = {f.path: f.skip_reason for f in g.files() if f.skip_reason}
        assert skips.get("blob.py") == "binary"
        assert skips.get("vendor.min.js") == "minified"


def test_empty_repo(tmp_path):
    (tmp_path / "readme.txt").write_text("nothing to index")
    with CodeGraph.index(tmp_path, db=tmp_path / "g.db") as g:
        assert g.symbols() == []
        assert g.stats().symbols == 0


def test_gitignored_files_excluded(tmp_path):
    _make(tmp_path, "keep.py", b"def keep(): pass\n")
    _make(tmp_path, "skip.py", b"def skip(): pass\n")
    (tmp_path / ".gitignore").write_text("skip.py\n")
    with CodeGraph.index(tmp_path, db=tmp_path / "g.db") as g:
        qnames = {s.qname for s in g.symbols()}
        assert "keep.keep" in qnames
        assert "skip.skip" not in qnames


def test_self_index_smoke(tmp_path):
    """entrygraph can index its own source tree without error."""
    with CodeGraph.index(REPO_ROOT / "src", db=tmp_path / "self.db") as g:
        stats = g.stats()
        assert stats.files > 20
        assert stats.symbols > 100
        # its own CodeGraph facade is discoverable
        assert any(s.name == "CodeGraph" for s in g.symbols(kind="class"))
        # incremental refresh on an unchanged tree is a no-op
        refresh = g.refresh()
        assert refresh.files_indexed == 0
