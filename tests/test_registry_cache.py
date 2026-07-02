"""The repo sink/source/sanitizer registry is cached per instance (Phase 3, M4).

Regression: `registry_for_repo` was rebuilt (recompiling every pattern's regex)
up to three times per `paths()` call whenever an entrygraph.toml or user pattern
was present.
"""

from __future__ import annotations

from pathlib import Path

from entrygraph import CodeGraph


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    pass\n")
    return repo


def test_registry_cached_across_calls(tmp_path: Path):
    g = CodeGraph.index(_repo(tmp_path), db=tmp_path / "g.db")
    with g._session_factory() as s:
        assert g._registry(s) is g._registry(s)  # same object, not rebuilt
    g.close()


def test_registry_cache_invalidates_on_new_config(tmp_path: Path):
    repo = _repo(tmp_path)
    g = CodeGraph.index(repo, db=tmp_path / "g.db")
    with g._session_factory() as s:
        first = g._registry(s)
        assert "custom.x" not in first.sinks

        # a new entrygraph.toml (mtime None -> set) must invalidate the cache
        (repo / "entrygraph.toml").write_text(
            '[[sink]]\nid = "custom.x"\ncategory = "custom"\ncallee = "py:custom.sink"\n'
        )
        second = g._registry(s)
        assert second is not first
        assert "custom.x" in second.sinks
    g.close()
