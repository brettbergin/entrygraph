"""Rust + Go catalogs lifted to `full` coverage tier (#135).

Pins the new high-signal sinks (each fed from an env-var source so it produces a
source->sink path) and the resulting coverage tier, so a future catalog edit that
drops below `full` fails loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph

FIX = Path(__file__).parent / "fixtures"


def test_go_and_rust_are_full_tier():
    from entrygraph.detect.taint import builtin_registry, catalog_coverage

    cov = catalog_coverage(builtin_registry())
    assert cov["go"].tier == "full"
    assert cov["go"].sinks >= 12
    assert cov["rust"].tier == "full"
    assert cov["rust"].sinks >= 12
    # the new categories are represented
    assert {"deserialization", "dynamic_load", "xxe"} <= set(cov["go"].sink_categories)
    assert {"file_write", "network_out"} <= set(cov["rust"].sink_categories)


@pytest.fixture(scope="module")
def go_graph(tmp_path_factory) -> CodeGraph:
    g = CodeGraph.index(FIX / "go" / "catalog_full_app", db=tmp_path_factory.mktemp("db") / "go.db")
    yield g
    g.close()


@pytest.mark.parametrize(
    ("sink_category", "head"),
    [
        ("command_exec", "_root.runSyscall"),
        ("dynamic_load", "_root.loadPlugin"),
        ("xxe", "_root.parseXML"),
    ],
)
def test_go_new_sinks_reachable(go_graph, sink_category, head):
    paths = go_graph.paths(source_category="env_input", sink_category=sink_category)
    assert any(p.symbols[0].qname == head for p in paths), sink_category


@pytest.fixture(scope="module")
def rust_graph(tmp_path_factory) -> CodeGraph:
    g = CodeGraph.index(
        FIX / "rust" / "catalog_full_app", db=tmp_path_factory.mktemp("db") / "rs.db"
    )
    yield g
    g.close()


@pytest.mark.parametrize(
    ("sink_category", "head"),
    [
        ("file_write", "_root.write_dir"),
        ("network_out", "_root.fetch_url"),
        ("sql", "_root.run_query"),
        ("deserialization", "_root.load_msgpack"),
        ("path_traversal", "_root.read_async"),
    ],
)
def test_rust_new_sinks_reachable(rust_graph, sink_category, head):
    paths = rust_graph.paths(source_category="env_input", sink_category=sink_category)
    assert any(p.symbols[0].qname == head for p in paths), sink_category


def test_new_sink_patterns_match_expected_callees():
    from entrygraph.detect.taint import builtin_registry

    r = builtin_registry()
    # Go
    assert r.match("go:syscall.Exec") == "go.command-exec.syscall"
    assert r.match("go:encoding/gob.NewDecoder") == "go.deserialization"
    assert r.match("go:encoding/xml.Unmarshal") == "go.xxe"
    assert r.match("go:plugin.Open") == "go.dynamic-load"
    # Rust
    assert r.match("rs:std.fs.File.create") == "rust.file-write"
    assert r.match("rs:reqwest.Client.get") == "rust.network-out"
    assert r.match("rs:rmp_serde.from_slice") == "rust.deserialization.msgpack"
    assert r.match("rs:tokio.fs.read") == "rust.path-traversal.tokio"
    # raw-SQL sink is arg-hint gated: a dynamically-built query trips it, a
    # constant one does not
    assert r.match("rs:*.execute", '(&format!("x {}", v), [])') == "rust.sql-raw"
    assert r.match("rs:*.execute", '("select 1", [])') is None
