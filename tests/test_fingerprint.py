"""Path fingerprint stability and semantics (#116 phase 0)."""

from __future__ import annotations

import re
from pathlib import Path

from entrygraph.api import CodeGraph
from entrygraph.graph.fingerprint import fingerprint
from entrygraph.results import CallPath, PathEdge, Symbol


def _sym(qname: str, line: int = 1) -> Symbol:
    return Symbol(
        id=0,
        kind="function",
        name=qname.rsplit(".", 1)[-1],
        qname=qname,
        file="f.py",
        start_line=line,
        end_line=line,
    )


def _path(qnames, sink_id, *, lines=None, source_category=None) -> CallPath:
    syms = tuple(_sym(q, line=10 * (i + 1)) for i, q in enumerate(qnames))
    n = len(qnames) - 1
    lines = lines or [10 * (i + 1) for i in range(n)]
    edges = tuple(
        PathEdge(
            kind="calls", line=lines[i], confidence=3, sink_id=(sink_id if i == n - 1 else None)
        )
        for i in range(n)
    )
    return CallPath(symbols=syms, edges=edges, source_category=source_category)


# ---------------- pure-function semantics ----------------


def test_fingerprint_is_32_hex_and_deterministic():
    p = _path(["app.h", "app.run", "py:subprocess.run"], "py.command-exec.subprocess")
    fp1 = fingerprint(p)
    fp2 = fingerprint(p)
    assert fp1 == fp2
    for h in (fp1.strict, fp1.endpoint):
        assert re.fullmatch(r"[0-9a-f]{32}", h)
    assert fp1.strict != fp1.endpoint


def test_line_numbers_do_not_change_fingerprint():
    a = _path(["app.h", "app.run", "py:subprocess.run"], "py.command-exec.subprocess", lines=[6, 8])
    b = _path(
        ["app.h", "app.run", "py:subprocess.run"], "py.command-exec.subprocess", lines=[40, 91]
    )
    assert fingerprint(a) == fingerprint(b)  # only lines differ -> same identity


def test_interior_hop_changes_strict_but_not_endpoint():
    direct = _path(["app.h", "py:subprocess.run"], "py.command-exec.subprocess")
    via_mid = _path(["app.h", "app.run", "py:subprocess.run"], "py.command-exec.subprocess")
    other_mid = _path(["app.h", "app.exec", "py:subprocess.run"], "py.command-exec.subprocess")
    # same source + sink -> same endpoint (fuzzy fallback survives a mid-path refactor)
    assert direct.symbols[0].qname == via_mid.symbols[0].qname
    assert (
        fingerprint(direct).endpoint
        == fingerprint(via_mid).endpoint
        == fingerprint(other_mid).endpoint
    )
    # but the full chains differ -> distinct strict fingerprints
    assert (
        len(
            {fingerprint(direct).strict, fingerprint(via_mid).strict, fingerprint(other_mid).strict}
        )
        == 3
    )


def test_different_sink_differs_on_both():
    a = _path(["app.h", "app.run", "py:subprocess.run"], "py.command-exec.subprocess")
    b = _path(["app.h", "app.run", "py:os.system"], "py.command-exec.os-system")
    assert fingerprint(a).strict != fingerprint(b).strict
    assert fingerprint(a).endpoint != fingerprint(b).endpoint


def test_source_category_participates_and_param_overrides():
    p = _path(
        ["app.h", "py:subprocess.run"], "py.command-exec.subprocess", source_category="cli_arg"
    )
    q = _path(
        ["app.h", "py:subprocess.run"], "py.command-exec.subprocess", source_category="http_input"
    )
    assert fingerprint(p) != fingerprint(q)  # category is part of identity
    # explicit param overrides the category recorded on the path
    assert fingerprint(p, source_category="http_input") == fingerprint(q)


# ---------------- golden: stable across a real refactor ----------------

_APP = (
    "import subprocess\n"
    "from flask import Flask, request\n"
    "app = Flask(__name__)\n"
    "@app.route('/x')\n"
    "def h():\n"
    "    q = request.args.get('q')\n"
    "    return run(q)\n"
    "def run(cmd):\n"
    "    subprocess.run(cmd)\n"
)

# same symbols, refactored: blank lines added and the two functions reordered, so
# every line number shifts but the source->sink structure is identical.
_APP_MOVED = (
    "import subprocess\n"
    "from flask import Flask, request\n"
    "\n\n\n"
    "app = Flask(__name__)\n"
    "\n\n"
    "def run(cmd):\n"
    "    subprocess.run(cmd)\n"
    "\n\n\n"
    "@app.route('/x')\n"
    "def h():\n"
    "    q = request.args.get('q')\n"
    "    return run(q)\n"
)


def _fingerprint_the_path(tmp_path: Path, src: str, name: str):
    repo = tmp_path / name
    repo.mkdir()
    (repo / "app.py").write_text(src)
    graph = CodeGraph.index(repo, db=tmp_path / f"{name}.db")
    try:
        paths = graph.paths(source_category="http_input", sink_category="command_exec")
        assert paths, "expected an http_input -> command_exec path"
        return fingerprint(paths[0])
    finally:
        graph.close()


def test_fingerprint_stable_across_refactor(tmp_path):
    before = _fingerprint_the_path(tmp_path, _APP, "before")
    after = _fingerprint_the_path(tmp_path, _APP_MOVED, "after")
    # a pure move/reindent must not read as a new finding
    assert before == after
