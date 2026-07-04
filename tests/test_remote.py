from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from entrygraph.errors import GitCloneError
from entrygraph.fs.remote import (
    default_clone_dir,
    is_git_url,
    prepare_source,
    repo_name,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _make_git_repo(path: Path) -> Path:
    """A tiny committed git repo (no network) to clone via a file:// URL."""
    path.mkdir(parents=True)
    (path / "app.py").write_text("import subprocess\ndef run(cmd):\n    subprocess.run(cmd)\n")
    env_git = [
        "git",
        "-C",
        str(path),
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=test",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*env_git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*env_git, "add", "-A"], check=True)
    subprocess.run([*env_git, "commit", "-q", "-m", "init"], check=True)
    return path


def _file_url(path: Path) -> str:
    return f"file://{path.resolve()}"


# ---------------- is_git_url ----------------


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/semgrep/semgrep",
        "https://github.com/semgrep/semgrep.git",
        "http://example.com/x/y",
        "git://example.com/x/y.git",
        "ssh://git@github.com/org/repo.git",
        "git@github.com:semgrep/semgrep.git",
        "file:///tmp/some-bare-repo",
    ],
)
def test_is_git_url_true(value):
    assert is_git_url(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "./semgrep",
        "/abs/path/to/repo",
        "../sibling",
        "semgrep",
        "org/repo",  # bare shorthand is NOT auto-expanded
        "C:/Users/x/proj",
    ],
)
def test_is_git_url_false(value):
    assert is_git_url(value) is False


def test_is_git_url_existing_path_wins(tmp_path):
    # a real local dir is never treated as a URL, even if named url-ishly
    weird = tmp_path / "https:"
    weird.mkdir()
    assert is_git_url(str(weird)) is False


# ---------------- url parsing helpers ----------------


@pytest.mark.parametrize(
    "url,name",
    [
        ("https://github.com/semgrep/semgrep", "semgrep"),
        ("https://github.com/semgrep/semgrep.git", "semgrep"),
        ("git@github.com:org/my-repo.git", "my-repo"),
    ],
)
def test_repo_name(url, name):
    assert repo_name(url) == name


def test_default_clone_dir_layout(tmp_path):
    d = default_clone_dir("https://github.com/semgrep/semgrep.git", base=tmp_path)
    assert d == tmp_path / ".entrygraph" / "clones" / "github.com" / "semgrep" / "semgrep"


def test_default_clone_dir_scp_style(tmp_path):
    d = default_clone_dir("git@github.com:org/repo.git", base=tmp_path)
    assert d == tmp_path / ".entrygraph" / "clones" / "github.com" / "org" / "repo"


# ---------------- prepare_source ----------------


def test_prepare_source_local_path_is_unchanged(tmp_path):
    with prepare_source(str(tmp_path)) as src:
        assert src.root == tmp_path.resolve()
        assert src.url is None
        assert src.ephemeral is False


def test_prepare_source_clones_to_clone_dir(tmp_path):
    origin = _make_git_repo(tmp_path / "origin")
    dest = tmp_path / "checkout"
    with prepare_source(_file_url(origin), clone_dir=str(dest)) as src:
        assert src.url is not None
        assert src.ephemeral is False
        assert (src.root / "app.py").exists()
        assert (src.root / ".git").exists()
    # persistent: the checkout survives after the context exits
    assert (dest / "app.py").exists()


def test_prepare_source_reuses_existing_checkout(tmp_path):
    origin = _make_git_repo(tmp_path / "origin")
    dest = tmp_path / "checkout"
    url = _file_url(origin)
    with prepare_source(url, clone_dir=str(dest)):
        pass
    # second run hits the fetch+reset update path, not a re-clone
    with prepare_source(url, clone_dir=str(dest)) as src:
        assert (src.root / "app.py").exists()


def test_prepare_source_ephemeral_cleans_up(tmp_path):
    origin = _make_git_repo(tmp_path / "origin")
    captured: dict[str, Path] = {}
    with prepare_source(_file_url(origin), ephemeral=True) as src:
        assert src.ephemeral is True
        assert (src.root / "app.py").exists()
        captured["root"] = src.root
    # the temp checkout is deleted on exit
    assert not captured["root"].exists()


def test_prepare_source_bad_url_raises(tmp_path):
    dest = tmp_path / "nope"
    with pytest.raises(GitCloneError):
        with prepare_source(
            _file_url(tmp_path / "does-not-exist"), clone_dir=str(dest), timeout=30
        ):
            pass
