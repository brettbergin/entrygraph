"""Parse-pool start method selection and graceful fallback (issue #5)."""

from __future__ import annotations

import multiprocessing as mp

import pytest

from entrygraph.fs.walker import WalkedFile
from entrygraph.pipeline import scanner


def _walked(n: int) -> list[WalkedFile]:
    return [
        WalkedFile(
            path=f"f{i}.py",
            abs_path=f"/repo/f{i}.py",
            language="python",
            size_bytes=1,
            mtime_ns=0,
        )
        for i in range(n)
    ]


def test_pool_context_avoids_fork_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    ctx = scanner._pool_context()
    assert ctx.get_start_method() != "fork"


def test_pool_context_uses_fork_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    if "fork" not in mp.get_all_start_methods():
        pytest.skip("fork start method unavailable on this platform")
    monkeypatch.setattr("sys.platform", "linux")
    ctx = scanner._pool_context()
    assert ctx.get_start_method() == "fork"


def test_parse_phase_falls_back_when_pool_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    to_index = _walked(scanner._PARALLEL_THRESHOLD + 5)

    def fake_extract_one(wf: WalkedFile, include_tests: bool = False):
        return (wf.path, object(), True, "deadbeef")  # (path, extraction, is_package, hash)

    class _BrokenPool:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def map(self, *args, **kwargs):
            raise scanner.BrokenProcessPool("simulated worker death")

    monkeypatch.setattr(scanner, "extract_one", fake_extract_one)
    monkeypatch.setattr(scanner, "ProcessPoolExecutor", _BrokenPool)

    extractions, hashes = scanner._parse_phase(to_index, max_workers=4)

    assert [e[0] for e in extractions] == [wf.path for wf in to_index]
    assert hashes == {wf.path: "deadbeef" for wf in to_index}
