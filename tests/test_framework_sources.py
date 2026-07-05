"""Param-level taint-source modeling for modern web frameworks (#134).

The handler-as-source fallback (#86) already lets any HTTP_ROUTE handler seed
taint, but it is coarse: the whole handler is tainted and the finding cannot say
*which* input feeds the sink. These tests pin the stronger, channel-bearing
accessor sources: FastAPI declarators (`Query`/`Path`/`Body`/`Header`) and
Express header/cookie reads, plus a guard that Spring/Rails/Express bodies keep
resolving.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

FIX = Path(__file__).parent / "fixtures"


# --------------------------- FastAPI (Python) ---------------------------


@pytest.fixture(scope="module")
def fastapi_graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "fastapi.db"
    g = CodeGraph.index(FIX / "python" / "fastapi_app", db=db)
    yield g
    g.close()


def test_fastapi_declarators_stamp_explicit_source_edges(fastapi_graph):
    # Query()/Path()/Body()/Header() declarators are catalog accessors, so they
    # stamp source edges (handler-as-source alone produces none).
    assert fastapi_graph.stats().source_edges >= 4


def test_fastapi_query_param_is_channel_level_source(fastapi_graph):
    paths = fastapi_graph.paths(source_category="http_input", sink_category="command_exec")
    by_head = {p.symbols[0].qname: p for p in paths}
    # each declarator handler flows to a command sink at its own channel
    assert by_head["main.search"].source_channel == "query"
    assert by_head["main.read_item"].source_channel == "path"
    assert by_head["main.do_exec"].source_channel == "body"
    assert by_head["main.agent"].source_channel == "header"


def test_fastapi_multiline_signature_declarator_verified(fastapi_graph):
    # a declarator in a multi-line signature is still a channel-level source and
    # is confirmed (not refuted) by the interprocedural verifier
    paths = fastapi_graph.paths(source_category="http_input", sink_category="command_exec")
    by_head = {p.symbols[0].qname: p for p in paths}
    multiline = by_head["main.multiline"]
    assert multiline.source_channel == "query"
    assert multiline.source_kind == "explicit"
    assert multiline.taint_verified is True


def test_fastapi_typed_param_only_falls_back_to_handler(fastapi_graph):
    # a bare typed path param (no declarator) has no accessor edge, so it is only
    # seen via handler-as-source -> no channel
    paths = fastapi_graph.paths(source_category="http_input", sink_category="command_exec")
    by_head = {p.symbols[0].qname: p for p in paths}
    assert by_head["main.run_typed"].source_channel is None


def test_fastapi_explicit_source_outranks_handler_fallback(fastapi_graph):
    # the declarator (explicit) path should score above the handler-as-source one,
    # since a demonstrable read is stronger evidence than "shaped like a source"
    paths = fastapi_graph.paths(source_category="http_input", sink_category="command_exec")
    by_head = {p.symbols[0].qname: p for p in paths}
    assert by_head["main.search"].risk_score > by_head["main.run_typed"].risk_score


# --------------------------- Express (JS) ---------------------------


@pytest.fixture(scope="module")
def express_channels_graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "express_ch.db"
    g = CodeGraph.index(FIX / "javascript" / "express_channels_app", db=db)
    yield g
    g.close()


def test_express_header_and_cookie_channels_resolve(express_channels_graph):
    paths = express_channels_graph.paths(source_category="http_input", sink_category="command_exec")
    by_head = {p.symbols[0].qname: p for p in paths}
    assert by_head["routes.fromHeader"].source_channel == "header"
    assert by_head["routes.fromHeader"].source_key == "x-cmd"
    assert by_head["routes.fromCookie"].source_channel == "cookie"
    assert by_head["routes.fromCookie"].source_key == "session"


# --------------------------- registry routing ---------------------------


def test_new_source_patterns_route_to_channels():
    from entrygraph.detect.taint import builtin_registry

    r = builtin_registry()
    cases = {
        "py:fastapi.Query": ("py.fastapi.query", "query"),
        "py:fastapi.Path": ("py.fastapi.path", "path"),
        "py:fastapi.Body": ("py.fastapi.body", "body"),
        "py:fastapi.Header": ("py.fastapi.header", "header"),
        "py:fastapi.Cookie": ("py.fastapi.cookie", "cookie"),
        "py:fastapi.Form": ("py.fastapi.form", "form"),
        "py:fastapi.File": ("py.fastapi.body", "body"),
        "py:starlette.requests.Query": ("py.fastapi.query", "query"),
        "js:*.headers": ("js.express.header", "header"),
        "js:*.cookies": ("js.express.cookie", "cookie"),
    }
    for callee, (expected_id, expected_channel) in cases.items():
        sid = r.match_source(callee)
        assert sid == expected_id, f"{callee} -> {sid}, want {expected_id}"
        assert r.sources[sid].channel == expected_channel, callee
    # the new FastAPI patterns join the http_input category
    http = r.source_ids_for_category("http_input")
    assert {"py.fastapi.query", "py.fastapi.body", "js.express.header"} <= http


# --------------------------- guard: existing frameworks still resolve ---------------------------


@pytest.mark.parametrize(
    ("fixture", "head"),
    [
        ("java/spring_app", "com.example.UserController.createReport"),
        ("ruby/rails_sql", "app.ReportsController.raw_lookup"),
        ("javascript/express_app", "routes.createReport"),
    ],
)
def test_handler_frameworks_still_reach_sinks(tmp_path, fixture, head):
    # Spring @RequestParam, Rails params[:x], Express req.body.x each resolve to a
    # sink (via handler-as-source or an accessor) — a regression guard for #134.
    g = CodeGraph.index(FIX / fixture, db=tmp_path / "g.db")
    try:
        paths = g.paths(source_category="http_input", sink_category="all")
        assert any(p.symbols[0].qname == head for p in paths), fixture
    finally:
        g.close()
