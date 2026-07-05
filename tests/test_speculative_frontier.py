"""Speculative-frontier precision + deeper interprocedural taint (#136).

When the precise pass finds nothing, `paths()` widens to a speculative frontier
(fuzzy binds + unresolved wildcards) that can stitch unrelated components into a
spurious chain. These tests pin: (1) a stitched cross-component chain ranks below
a clean single-guess lead via the compounding speculative discount, and (2) a
genuine deeper flow — through extra hops and a collection element — is confirmed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph
from entrygraph.graph.scoring import score_path
from entrygraph.kinds import Confidence

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def frontier_graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "sf.db"
    g = CodeGraph.index(FIX / "python" / "speculative_frontier", db=db)
    yield g
    g.close()


def test_stitched_chain_ranks_below_clean_lead(frontier_graph):
    # both reach the same unresolved `*.execute` sink, but the stitched chain adds
    # a fuzzy cross-component hop, so it must rank strictly below the direct lead
    paths = frontier_graph.paths(source_category="http_input", sink_category="sql")
    by_head = {p.symbols[0].qname: p for p in paths}
    direct = by_head["app.routes.direct"]
    stitched = by_head["app.routes.stitched"]
    assert stitched.risk_score < direct.risk_score


def test_stitched_chain_is_demoted(frontier_graph):
    # the multi-speculative-hop stitch is pushed to a clearly low band
    paths = frontier_graph.paths(source_category="http_input", sink_category="sql")
    stitched = next(p for p in paths if p.symbols[0].qname == "app.routes.stitched")
    assert stitched.risk_score < 0.2


def test_speculative_cost_compounds():
    # two paths of the SAME length and SAME weakest confidence, differing only in
    # how many hops are speculative: the one with an extra fuzzy interior hop must
    # score lower. Same length + same min-confidence isolates the compounding
    # discount from length_decay and confidence_factor.
    base = {
        "sink_severity": "high",
        "sanitized_effect": None,
        "constant_args": False,
        "source_kind": "explicit",
    }
    one_speculative = score_path(
        # a resolved interior hop + an unresolved sink -> 1 speculative hop
        hop_confidences=[int(Confidence.IMPORT), int(Confidence.UNRESOLVED)],
        hop_vias=[None, None],
        **base,
    )
    two_speculative = score_path(
        # a fuzzy interior bind + an unresolved sink -> 2 speculative hops
        hop_confidences=[int(Confidence.FUZZY), int(Confidence.UNRESOLVED)],
        hop_vias=[None, None],
        **base,
    )
    assert two_speculative < one_speculative


def test_cha_guess_costs_more_than_fuzzy_bind():
    # a class-hierarchy guess is weaker evidence than a unique-name fuzzy bind, so
    # it carries a heavier speculative cost (same confidence, differing via)
    base = {
        "sink_severity": "high",
        "sanitized_effect": None,
        "constant_args": False,
        "source_kind": "explicit",
    }
    fuzzy_bind = score_path(hop_confidences=[int(Confidence.FUZZY)], hop_vias=[None], **base)
    cha_guess = score_path(hop_confidences=[int(Confidence.FUZZY)], hop_vias=["cha"], **base)
    assert cha_guess < fuzzy_bind


def test_resolved_path_unaffected_by_speculative_discount():
    # a fully-resolved path (EXACT/IMPORT, no speculative via) keeps its score:
    # the discount only applies to speculative hops
    base = {
        "sink_severity": "high",
        "sanitized_effect": None,
        "constant_args": False,
        "source_kind": "explicit",
    }
    resolved = score_path(
        hop_confidences=[int(Confidence.IMPORT), int(Confidence.EXACT)],
        hop_vias=[None, None],
        **base,
    )
    # severity(high=0.85) * conf(min IMPORT=0.95) * length_decay(0.97) * 1.0...
    assert resolved == pytest.approx(0.85 * 0.95 * 0.97, abs=1e-4)


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
