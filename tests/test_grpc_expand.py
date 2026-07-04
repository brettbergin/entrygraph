"""gRPC per-method entrypoint expansion via the binding table (#98 P2 / #37)."""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

GRPC_APP = Path(__file__).parent / "fixtures" / "go" / "grpc_app"


@pytest.fixture
def graph(tmp_path):
    (tmp_path / "go.mod").write_text("module ex\n")
    src = (GRPC_APP / "server.go").read_text()
    (tmp_path / "server.go").write_text(src)
    g = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    yield g
    g.close()


def test_registration_expands_to_per_method_entrypoints(graph):
    rpc = [e for e in graph.entrypoints() if e.kind == "rpc_handler"]
    routes = {e.route for e in rpc}
    assert "/Ingester/Push" in routes
    assert "/Ingester/Query" in routes
    # the coarse service-level marker is replaced, not kept alongside
    assert "/Ingester" not in routes


def test_unexported_methods_excluded(graph):
    rpc = [e for e in graph.entrypoints() if e.kind == "rpc_handler"]
    assert all("helper" not in (e.route or "") for e in rpc)


def test_per_method_entrypoints_bind_to_the_real_method(graph):
    rpc = {e.route: e for e in graph.entrypoints() if e.kind == "rpc_handler"}
    push = rpc["/Ingester/Push"]
    assert push.symbol is not None
    assert push.symbol.qname == "_root.Ingester.Push"


def test_unresolvable_registration_keeps_service_marker(tmp_path):
    # impl arg is an opaque expression the binding table can't type -> the coarse
    # service-level marker survives as the fallback
    (tmp_path / "go.mod").write_text("module ex\n")
    (tmp_path / "s.go").write_text(
        "package server\nfunc reg(g int) { RegisterFooServer(g, buildImpl()) }\n"
    )
    g = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    try:
        routes = {e.route for e in g.entrypoints() if e.kind == "rpc_handler"}
        assert "/Foo" in routes  # service marker fallback
    finally:
        g.close()
