"""Speculative-frontier precision + deeper interprocedural taint (#136).

When the precise pass finds nothing, `paths()` widens to a speculative frontier
(fuzzy binds + unresolved wildcards) that can stitch unrelated components into a
spurious chain. These tests pin: (1) a stitched cross-component chain ranks below
a clean single-guess lead, and (2) a genuine deeper flow — through extra hops and
a collection element — is confirmed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def frontier_graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "sf.db"
    g = CodeGraph.index(FIX / "python" / "speculative_frontier", db=db)
    yield g
    g.close()


def test_stitched_chain_ranks_below_clean_lead(frontier_graph):
    # both reach the same unresolved `*.execute` sink, but the stitched chain adds
    # a fuzzy cross-component hop (a longer, weaker path), so it must rank below
    # the direct lead in the returned order
    paths = frontier_graph.paths(source_category="http_input", sink_category="sql")
    order = [p.symbols[0].qname for p in paths]
    assert order.index("app.routes.direct") < order.index("app.routes.stitched")


# --------------------------- deeper confirmation (#136 goal 2) ---------------------------

_COLLECTION_FLOW = """
import subprocess
from flask import Flask, request
app = Flask(__name__)

@app.route("/x")
def handler():
    data = request.get_json()
    item = data["id"]
    return forward(item)

def forward(x):
    return sink_it(x)

def sink_it(y):
    subprocess.run(y, shell=True)
"""


def test_deep_collection_flow_confirmed(tmp_path):
    (tmp_path / "app.py").write_text(_COLLECTION_FLOW)
    g = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    try:
        paths = g.paths(source_category="http_input", sink_category="command_exec")
        handler = next(p for p in paths if p.symbols[0].qname == "app.handler")
        # request body -> collection element -> two hops -> sink, confirmed within
        # the default hop limit (5)
        assert handler.source_channel == "body"
        assert handler.taint_verified is True
    finally:
        g.close()
