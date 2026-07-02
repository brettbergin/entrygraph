from __future__ import annotations

from pathlib import Path

from entrygraph.fs.walker import MAX_FILE_BYTES, _collect_gitignores, walk_repo


def _make(root: Path, rel: str, content: bytes = b"print('hi')\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_walk_finds_source_and_prunes_junk(tmp_path: Path):
    _make(tmp_path, "app/main.py")
    _make(tmp_path, "node_modules/lib/index.js")
    _make(tmp_path, ".venv/lib/thing.py")
    _make(tmp_path, "vendor/pkg/x.go")
    _make(tmp_path, "docs/readme.md", b"# hi\n")

    files, profile = walk_repo(tmp_path)
    paths = [f.path for f in files]
    assert "app/main.py" in paths
    assert "docs/readme.md" in paths  # recognized for stats
    assert not any("node_modules" in p or ".venv" in p or "vendor" in p for p in paths)


def test_walk_respects_gitignore(tmp_path: Path):
    _make(tmp_path, "keep.py")
    _make(tmp_path, "secret.py")
    _make(tmp_path, "generated/gen.py")
    _make(tmp_path, "sub/ignored_here.py")
    (tmp_path / ".gitignore").write_text("secret.py\ngenerated/\n")
    (tmp_path / "sub" / ".gitignore").write_text("ignored_here.py\n")

    files, _ = walk_repo(tmp_path)
    paths = [f.path for f in files]
    assert "keep.py" in paths
    assert "secret.py" not in paths
    assert "generated/gen.py" not in paths
    assert "sub/ignored_here.py" not in paths


def test_collect_gitignores_skips_pruned_dirs(tmp_path: Path):
    # .gitignore collection must not descend into .git / node_modules / vendor
    # (the whole point of not using rglob) — those trees are never indexed.
    (tmp_path / ".gitignore").write_text("x\n")
    _make(tmp_path, "sub/.gitignore", b"y\n")
    _make(tmp_path, "node_modules/.gitignore", b"z\n")
    _make(tmp_path, ".git/.gitignore", b"w\n")
    _make(tmp_path, "vendor/nested/.gitignore", b"v\n")

    found = {p.relative_to(tmp_path).as_posix() for p in _collect_gitignores(tmp_path)}
    assert found == {".gitignore", "sub/.gitignore"}


def test_walk_gates_cheap_only(tmp_path: Path):
    # walk_repo applies only the cheap (no-read) gates: size and filename. The
    # byte-peek content gate (binary / minified-by-content) is deferred to the
    # diff phase, so those files come back with skip_reason=None from the walk.
    _make(tmp_path, "big.py", b"x = 1\n" * (MAX_FILE_BYTES // 6 + 10))
    _make(tmp_path, "bin.py", b"\x00\x01\x02binary")
    _make(tmp_path, "lib.min.js", b"var a=1;")
    _make(tmp_path, "long.js", b"var a" + b"a" * 6000 + b"=1;")
    _make(tmp_path, "ok.py")

    files, _ = walk_repo(tmp_path)
    by_path = {f.path: f for f in files}
    assert by_path["big.py"].skip_reason == "too_large"  # cheap: size
    assert by_path["lib.min.js"].skip_reason == "minified"  # cheap: name suffix
    assert by_path["bin.py"].skip_reason is None  # content gate deferred
    assert by_path["long.js"].skip_reason is None  # content gate deferred
    assert by_path["ok.py"].skip_reason is None


def test_content_gate_applied_during_diff(tmp_path: Path):
    # the deferred content gate must still fire for files being indexed: a binary
    # and a long-line-minified file get their skip_reason set in diff_files.
    from entrygraph.fs.hashing import diff_files

    _make(tmp_path, "bin.py", b"\x00\x01\x02binary")
    _make(tmp_path, "long.js", b"var a" + b"a" * 6000 + b"=1;")
    _make(tmp_path, "ok.py")

    walked, _ = walk_repo(tmp_path)
    diff_files(walked, {})  # all added -> content gate finalized
    by_path = {f.path: f for f in walked}
    assert by_path["bin.py"].skip_reason == "binary"
    assert by_path["long.js"].skip_reason == "minified"
    assert by_path["ok.py"].skip_reason is None


def test_walk_fixture_repo():
    fixtures = Path(__file__).parent / "fixtures" / "python" / "flask_app"
    files, profile = walk_repo(fixtures)
    paths = [f.path for f in files]
    assert "app/routes.py" in paths
    assert "app/services.py" in paths
    assert "cli.py" in paths
    stats = {s.name for s in profile.stats()}
    assert "python" in stats
