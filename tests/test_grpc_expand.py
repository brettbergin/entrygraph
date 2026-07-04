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


def test_local_constructor_impl_expands(tmp_path):
    # `impl := &Ingester{}; RegisterIngesterServer(s, impl)` — the impl is a local
    # bound to a construction, resolved via the binding table (#98 P3)
    (tmp_path / "go.mod").write_text("module ex\n")
    (tmp_path / "s.go").write_text(
        "package server\n"
        "type Ingester struct{}\n"
        "func (i *Ingester) Push(r int) {}\n"
        "func (i *Ingester) Query(r int) {}\n"
        "func Setup(s int) {\n"
        "\timpl := &Ingester{}\n"
        "\tRegisterIngesterServer(s, impl)\n"
        "}\n"
    )
    g = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    try:
        routes = {e.route for e in g.entrypoints() if e.kind == "rpc_handler"}
        assert routes == {"/Ingester/Push", "/Ingester/Query"}
    finally:
        g.close()


def _write_cross_package_grpc(root):
    (root / "go.mod").write_text("module ex\n")
    (root / "ingester").mkdir()
    (root / "ingester" / "ingester.go").write_text(
        "package ingester\n"
        "type Ingester struct{}\n"
        "func (i *Ingester) Push(r int) {}\n"
        "func (i *Ingester) Query(r int) {}\n"
        "func (i *Ingester) helper() {}\n"
        "func New(cfg int) *Ingester { return &Ingester{} }\n"
    )
    (root / "server.go").write_text(
        "package main\n"
        'import "ex/ingester"\n'
        "func Setup(s int) {\n"
        "\timpl := ingester.New(0)\n"
        "\tRegisterIngesterServer(s, impl)\n"
        "}\n"
    )


def test_cross_package_constructor_impl_expands(tmp_path):
    # `impl := ingester.New(cfg)` where New lives in another package and returns
    # *ingester.Ingester — typed via the callee's return type (#113)
    _write_cross_package_grpc(tmp_path)
    g = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    try:
        rpc = {e.route: e for e in g.entrypoints() if e.kind == "rpc_handler"}
        assert set(rpc) == {
            "/Ingester/Push",
            "/Ingester/Query",
        }  # helper unexported; /Ingester gone
        assert rpc["/Ingester/Push"].symbol.qname == "ingester.Ingester.Push"
    finally:
        g.close()


def test_cross_package_impl_survives_incremental_reindex(tmp_path):
    # the ingester package is unchanged on the second pass, so its return type must
    # be rebuilt from the persisted type_ref for the impl to still type (#113)
    from entrygraph.db.engine import make_engine
    from entrygraph.db.meta import create_schema
    from entrygraph.pipeline.scanner import index_repository

    _write_cross_package_grpc(tmp_path)
    engine = make_engine(tmp_path / "g.db")
    create_schema(engine)
    index_repository(tmp_path, engine)
    # touch only server.go, then re-index incrementally
    server = tmp_path / "server.go"
    server.write_text(server.read_text() + "\n// touched\n")
    index_repository(tmp_path, engine, incremental=True)
    g = CodeGraph.open(tmp_path / "g.db")
    try:
        routes = {e.route for e in g.entrypoints() if e.kind == "rpc_handler"}
        assert routes == {"/Ingester/Push", "/Ingester/Query"}
    finally:
        g.close()
    engine.dispose()
