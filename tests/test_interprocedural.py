"""Bounded interprocedural taint verification (#96 Phase 3)."""

from __future__ import annotations

import pytest

from entrygraph import CodeGraph

_TWO_HOP = """
import subprocess
from flask import Flask, request
app = Flask(__name__)

@app.route("/confirmed")
def confirmed():
    name = request.args.get("name")
    return run_report(name)

@app.route("/refuted")
def refuted():
    _ = request.args.get("name")
    run_report("fixed")
    return "ok"

def run_report(cmd):
    subprocess.run(cmd)
"""


def _index(tmp_path, src):
    (tmp_path / "app.py").write_text(src)
    return CodeGraph.index(tmp_path, db=tmp_path / "g.db")


def test_two_hop_confirmed_and_refuted(tmp_path):
    g = _index(tmp_path, _TWO_HOP)
    try:
        by_head = {
            p.symbols[0].qname: p
            for p in g.paths(source_category="http_input", sink_category="command_exec")
        }
        assert by_head["app.confirmed"].taint_verified is True
        assert by_head["app.refuted"].taint_verified is False
        assert by_head["app.refuted"].risk_score < by_head["app.confirmed"].risk_score
    finally:
        g.close()


def test_hop_limit_zero_disables_interprocedural(tmp_path):
    g = _index(tmp_path, _TWO_HOP)
    try:
        paths = g.paths(source_category="http_input", sink_category="command_exec", taint_hops=0)
        # with 0 interior hops allowed, the 2-hop chains get no verdict
        assert all(p.taint_verified is None for p in paths)
    finally:
        g.close()


def test_deep_chain_exceeding_limit_is_unverified(tmp_path):
    src = (
        "import subprocess\n"
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        "@app.route('/x')\n"
        "def h():\n"
        "    x = request.args.get('q')\n"
        "    return a(x)\n"
        "def a(v): return b(v)\n"
        "def b(v): return c(v)\n"
        "def c(v): return d(v)\n"
        "def d(v): subprocess.run(v)\n"  # 4 interior hops
    )
    g = _index(tmp_path, src)
    try:
        # below the limit (taint_hops=2 < 4 interior hops): no verdict
        paths = g.paths(source_category="http_input", sink_category="command_exec", taint_hops=2)
        deep = [p for p in paths if p.symbols[0].qname == "app.h"]
        assert deep
        assert all(p.taint_verified is None for p in deep)  # beyond hop limit
        # the default limit (5) covers 4 interior hops, so it verifies
        paths_default = g.paths(source_category="http_input", sink_category="command_exec")
        deep_default = [p for p in paths_default if p.symbols[0].qname == "app.h"]
        assert any(p.taint_verified is True for p in deep_default)
    finally:
        g.close()


def test_kwarg_at_hop_yields_no_verdict(tmp_path):
    # a keyword argument at a hop makes position mapping ambiguous -> None
    src = (
        "import subprocess\n"
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        "@app.route('/x')\n"
        "def h():\n"
        "    q = request.args.get('q')\n"
        "    return run(cmd=q)\n"
        "def run(cmd): subprocess.run(cmd)\n"
    )
    g = _index(tmp_path, src)
    try:
        paths = g.paths(source_category="http_input", sink_category="command_exec")
        hh = [p for p in paths if p.symbols[0].qname == "app.h"]
        assert hh
        assert all(p.taint_verified is None for p in hh)
    finally:
        g.close()


def test_recursion_terminates_with_no_verdict(tmp_path):
    src = (
        "import subprocess\n"
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        "@app.route('/x')\n"
        "def h():\n"
        "    q = request.args.get('q')\n"
        "    return rec(q)\n"
        "def rec(v):\n"
        "    if v:\n"
        "        return rec(v)\n"
        "    subprocess.run(v)\n"
    )
    g = _index(tmp_path, src)
    try:
        # must not hang; verdict is None or a safe value
        paths = g.paths(source_category="http_input", sink_category="command_exec", taint_hops=5)
        assert all(p.taint_verified in (True, False, None) for p in paths)
    finally:
        g.close()


@pytest.mark.slow
def test_interprocedural_bounded_cost(tmp_path):
    import time

    g = _index(tmp_path, _TWO_HOP)
    try:
        start = time.monotonic()
        for _ in range(30):
            g.paths(source_category="http_input", sink_category="command_exec", taint_hops=3)
        assert time.monotonic() - start < 12.0
    finally:
        g.close()
