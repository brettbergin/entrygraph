"""Continuous Reachability Gate: baseline diff, policy, exit codes, SARIF (#116)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from entrygraph.api import CodeGraph
from entrygraph.cli.main import main
from entrygraph.gate import store
from entrygraph.gate.engine import run_gate
from entrygraph.gate.sarif import to_sarif
from entrygraph.gate.store import Policy

NOW = datetime(2026, 7, 4, tzinfo=UTC)

_ONE_PATH = (
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

# same single path, reindented + reordered — line numbers change, structure doesn't
_ONE_PATH_MOVED = (
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

_TWO_PATHS = _ONE_PATH + (
    "@app.route('/y')\ndef h2():\n    c = request.args.get('c')\n    subprocess.run(c)\n"
)

_NO_PATH = (
    "from flask import Flask, request\n"
    "app = Flask(__name__)\n"
    "@app.route('/x')\n"
    "def h():\n"
    "    return request.args.get('q')\n"
)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "app"
    d.mkdir()
    (d / "app.py").write_text(_ONE_PATH)
    return d


def _db(tmp_path):
    return tmp_path / "global.db"


def _index(repo, db):
    return CodeGraph.index(repo, db=db)


def _baseline(repo, db, src=None):
    if src is not None:
        (repo / "app.py").write_text(src)
    graph = _index(repo, db)
    try:
        with graph.session() as s:
            findings = store.enumerate_findings(graph, Policy())
            n = store.save_baseline(s, graph.repo_id, findings, now=NOW)
    finally:
        graph.close()
    return n


def _gate(repo, db, src=None, policy=None):
    if src is not None:
        (repo / "app.py").write_text(src)
    graph = _index(repo, db)
    try:
        with graph.session() as s:
            return run_gate(graph, s, graph.repo_id, policy=policy, now=NOW)
    finally:
        graph.close()


# ---------------- enumeration ----------------


def test_enumerate_finds_the_command_exec_path(repo, tmp_path):
    graph = _index(repo, _db(tmp_path))
    try:
        findings = store.enumerate_findings(graph, Policy())
    finally:
        graph.close()
    assert len(findings) == 1
    f = findings[0]
    assert f.sink_category == "command_exec"
    assert f.strict and f.endpoint and f.strict != f.endpoint


# ---------------- diff / verdict ----------------


def test_unchanged_repo_passes(repo, tmp_path):
    db = _db(tmp_path)
    assert _baseline(repo, db) == 1
    result = _gate(repo, db)
    assert result.status == "passed"
    assert result.exit_code == 0
    assert len(result.known) == 1 and not result.new


def test_new_path_fails_the_gate(repo, tmp_path):
    db = _db(tmp_path)
    _baseline(repo, db)
    result = _gate(repo, db, src=_TWO_PATHS)
    assert result.status == "failed"
    assert result.exit_code == 1
    assert len(result.new) == 1 and len(result.gating) == 1
    assert len(result.known) == 1  # the original path is still known


def test_moved_function_is_known_not_new(repo, tmp_path):
    db = _db(tmp_path)
    _baseline(repo, db)
    result = _gate(repo, db, src=_ONE_PATH_MOVED)
    # a pure reindent/reorder must not read as a new finding
    assert not result.new
    assert len(result.known) == 1
    assert result.exit_code == 0


def test_removed_path_reported_as_fixed(repo, tmp_path):
    db = _db(tmp_path)
    _baseline(repo, db)
    result = _gate(repo, db, src=_NO_PATH)
    assert not result.new
    assert len(result.fixed) == 1
    assert result.exit_code == 0


def test_warn_mode_reports_but_does_not_fail(repo, tmp_path):
    db = _db(tmp_path)
    _baseline(repo, db)
    result = _gate(repo, db, src=_TWO_PATHS, policy=Policy(mode="warn"))
    assert result.status == "warned"
    assert result.exit_code == 0  # warn never fails
    assert len(result.gating) == 1  # but the new path is still surfaced


def test_threshold_above_risk_does_not_gate(repo, tmp_path):
    db = _db(tmp_path)
    _baseline(repo, db)
    # a threshold above the path's risk -> the new path doesn't gate
    result = _gate(repo, db, src=_TWO_PATHS, policy=Policy(risk_threshold=1.01))
    assert len(result.new) == 1 and not result.gating
    assert result.exit_code == 0


def test_no_baseline_never_gates(repo, tmp_path):
    # no baseline cut -> everything is "new" but the gate can't fail (nothing to diff)
    result = _gate(repo, _db(tmp_path))
    assert result.has_baseline is False
    assert result.status == "no-baseline"
    assert result.exit_code == 0
    assert result.new  # surfaced, but not gating


def test_suppression_excludes_a_finding(repo, tmp_path):
    from entrygraph.db import models

    db = _db(tmp_path)
    _baseline(repo, db)
    (repo / "app.py").write_text(_TWO_PATHS)  # introduce the new path
    graph = _index(repo, db)
    try:
        with graph.session() as s:
            head = store.enumerate_findings(graph, Policy())
            new_fp = next(f.strict for f in head if "h2" in (f.hops[0]["qname"] if f.hops else ""))
            s.add(models.Suppression(repo_id=graph.repo_id, fingerprint=new_fp, reason="accepted"))
            s.commit()
            result = run_gate(graph, s, graph.repo_id, now=NOW)
    finally:
        graph.close()
    assert len(result.suppressed) == 1
    assert not result.gating and result.exit_code == 0


# ---------------- SARIF ----------------


def test_sarif_shape_and_fingerprints(repo, tmp_path):
    graph = _index(repo, _db(tmp_path))
    try:
        findings = store.enumerate_findings(graph, Policy())
    finally:
        graph.close()
    log = to_sarif(findings, threshold=0.5, tool_version="1.2.3")
    assert log["version"] == "2.1.0"
    run = log["runs"][0]
    assert run["tool"]["driver"]["name"] == "entrygraph"
    assert run["tool"]["driver"]["version"] == "1.2.3"
    assert run["results"], "expected at least one SARIF result"
    res = run["results"][0]
    assert res["partialFingerprints"]["entrygraph/strict"] == findings[0].strict
    assert res["ruleId"] == "command_exec"
    assert res["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "app.py"


# ---------------- CLI exit codes ----------------


def test_cli_gate_exit_codes(repo, tmp_path, capsys):
    db = str(_db(tmp_path))
    assert main(["index", str(repo), "--db", db]) == 0
    assert main(["baseline", "update", "--db", db]) == 0
    capsys.readouterr()
    # clean: exit 0
    assert main(["gate", "--db", db]) == 0
    # new path: exit 1
    (repo / "app.py").write_text(_TWO_PATHS)
    assert main(["index", str(repo), "--db", db]) == 0
    capsys.readouterr()
    assert main(["gate", "--db", db]) == 1
    # same, warn mode: exit 0
    assert main(["gate", "--db", db, "--warn"]) == 0
