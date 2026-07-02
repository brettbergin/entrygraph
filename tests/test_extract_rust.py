from __future__ import annotations

from pathlib import Path

from entrygraph.api import CodeGraph
from entrygraph.extract.base import FileContext
from entrygraph.extract.rust import RustExtractor
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.parsers import parse

EXTRACTOR = RustExtractor()

AXUM_APP = Path(__file__).parent / "fixtures" / "rust" / "axum_app"


def extract(source: str, path: str = "src/main.rs"):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(path=path, language="rust", module_path=module_path,
                      source=src, is_package=is_package)
    return EXTRACTOR.extract(parse("rust", src), ctx)


# ---------------- unit tests ----------------


def test_module_path_for():
    assert EXTRACTOR.module_path_for("src/main.rs") == ("_root", False)
    assert EXTRACTOR.module_path_for("src/lib.rs") == ("_root", False)
    assert EXTRACTOR.module_path_for("src/foo.rs") == ("foo", False)
    # mod.rs behaves like Python's __init__.py -> is_package
    assert EXTRACTOR.module_path_for("src/foo/mod.rs") == ("foo", True)
    assert EXTRACTOR.module_path_for("src/a/b.rs") == ("a.b", False)


def test_free_functions_and_visibility():
    x = extract(
        """
pub fn run() {}
fn helper() {}
const MAX: u32 = 3;
static NAME: &str = "x";
""",
        path="src/lib.rs",
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    assert by_qname["_root.run"].kind is SymbolKind.FUNCTION
    assert by_qname["_root.run"].is_exported is True
    assert by_qname["_root.helper"].is_exported is False
    assert by_qname["_root.MAX"].kind is SymbolKind.CONSTANT
    assert by_qname["_root.NAME"].kind is SymbolKind.CONSTANT


def test_impl_methods_and_trait_inherit():
    x = extract(
        """
pub struct Report { name: String }

pub trait Runner { fn run(&self); }

impl Report {
    pub fn new(name: String) -> Self { Report { name } }
}

impl Runner for Report {
    fn run(&self) {}
}
""",
        path="src/lib.rs",
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    # `impl Foo { fn bar }` -> method <mod>.Foo.bar parented to <mod>.Foo
    new = by_qname["_root.Report.new"]
    assert new.kind is SymbolKind.METHOD
    assert new.parent_qualified_name == "_root.Report"
    assert new.is_exported is True
    # `impl Trait for Foo` attaches methods to Foo
    run = by_qname["_root.Report.run"]
    assert run.kind is SymbolKind.METHOD
    assert run.parent_qualified_name == "_root.Report"
    # ...and emits an inherit ref to the trait
    inherit = [r for r in x.references if r.kind == "inherit"]
    assert any(r.callee_name == "Runner" and r.caller_qualified_name == "_root.Report"
               for r in inherit)
    # the trait itself is a symbol
    assert by_qname["_root.Runner"].kind is SymbolKind.INTERFACE


def test_use_declaration_unrolling():
    x = extract(
        """
use std::process::Command;
use std::fs::{self, write};
use serde::Deserialize as De;
use foo::bar::*;
""",
        path="src/lib.rs",
    )
    by_alias = {i.alias: i for i in x.imports}
    # scoped path -> alias is the last segment, `::` normalized to `.`
    assert by_alias["Command"].module == "std.process.Command"
    # scoped_use_list unrolls each member with the shared prefix
    assert by_alias["fs"].module == "std.fs"
    assert by_alias["write"].module == "std.fs.write"
    # `use x as y`
    assert by_alias["De"].module == "serde.Deserialize"
    # wildcard
    wild = [i for i in x.imports if i.imported_name == "*"]
    assert any(i.module == "foo.bar" for i in wild)
    # crate-root framework signals
    assert ("import", "std") in x.framework_signals
    assert ("import", "serde") in x.framework_signals


def test_attributes_captured_as_decorators():
    x = extract(
        """
#[tokio::main]
async fn main() {}

#[get("/x")]
pub async fn handler() {}

#[derive(Parser)]
pub struct Cli {}
""",
        path="src/main.rs",
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    assert by_qname["_root.main"].decorators == ["#[tokio::main]"]
    assert by_qname["_root.handler"].decorators == ['#[get("/x")]']
    assert by_qname["_root.Cli"].decorators == ["#[derive(Parser)]"]
    # decorators also emitted as decorator refs
    deco = {(r.callee_name, r.caller_qualified_name) for r in x.references if r.kind == "decorator"}
    assert ("main", "_root.main") in deco
    assert ("get", "_root.handler") in deco
    assert ("derive", "_root.Cli") in deco


def test_macro_invocation_emitted_as_call():
    x = extract(
        """
async fn q() {
    let r = sqlx::query!("SELECT 1");
}
""",
        path="src/lib.rs",
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}
    # macro path minus trailing `!`, `::` normalized to `.`
    assert "sqlx.query" in calls
    q = calls["sqlx.query"]
    assert q.callee_name == "query"
    assert q.receiver_text == "sqlx"
    assert q.caller_qualified_name == "_root.q"
    assert "SELECT 1" in q.arg_preview


def test_scoped_call_expression():
    x = extract(
        """
use std::process::Command;

fn run(cmd: &str) {
    Command::new(cmd);
}
""",
        path="src/handlers.rs",
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}
    cmd = calls["Command.new"]
    assert cmd.callee_name == "new"
    assert cmd.receiver_text == "Command"
    assert cmd.caller_qualified_name == "handlers.run"


def test_partial_tree_still_extracts():
    x = extract("fn good() {}\n\nfn broken( {\n", path="src/lib.rs")
    assert not x.parse_ok
    assert any(s.name == "good" for s in x.symbols)


# ---------------- end-to-end index test ----------------


def test_index_axum_app_reachability(tmp_path):
    graph = CodeGraph.index(AXUM_APP, db=tmp_path / "rust.db")

    # axum + tokio detected from the manifest + imports
    report = graph.detect()
    fw_names = {f.name for f in report.frameworks}
    assert "axum" in fw_names
    assert "tokio" in fw_names

    # the crate main entrypoint (#[tokio::main] async fn main)
    mains = graph.entrypoints(kind="main")
    assert mains, "expected a main entrypoint"

    # a route hint for /reports exists
    routes = graph.entrypoints(kind="http_route")
    assert any(e.route == "/reports" for e in routes), \
        f"expected /reports route, got {[e.route for e in routes]}"

    # the external command_exec sink resolves via the `use std::process::Command`
    # import as rs:std.process.Command.new at IMPORT confidence.
    exec_sink = graph.symbols(qname="rs:std.process.Command.new", include_external=True)
    assert exec_sink, "expected external symbol rs:std.process.Command.new"

    # source -> sink reachability: create_report -> run_report -> Command.new
    paths = graph.paths(source="handlers.create_report", sink="rs:std.process.Command.new")
    assert paths, "expected a path from create_report to rs:std.process.Command.new"
    chain = [s.qname for s in paths[0].symbols]
    assert chain[0] == "handlers.create_report"
    assert chain[-1] == "rs:std.process.Command.new"
