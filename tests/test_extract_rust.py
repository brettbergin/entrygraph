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
    ctx = FileContext(
        path=path, language="rust", module_path=module_path, source=src, is_package=is_package
    )
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
    assert any(
        r.callee_name == "Runner" and r.caller_qualified_name == "_root.Report" for r in inherit
    )
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
    # a `::`-scoped call is a qualified path, not a value.method — the receiver is
    # left unset so the resolver keeps the full path (rs:Command.new / the imported
    # rs:std.process.Command.new) rather than collapsing to rs:*.new.
    assert cmd.receiver_text is None
    assert cmd.caller_qualified_name == "handlers.run"


def test_function_value_argument_is_a_callback():
    # post(handler) passes `handler` as a value; it must be a callback so the
    # handler is reachable from the route registration.
    x = extract(
        """
fn register(app: Router) {
    app.route("/run", post(handler));
}

async fn handler() {}
"""
    )
    callbacks = {r.callee_name: r for r in x.references if r.kind == "callback"}
    assert "handler" in callbacks
    assert callbacks["handler"].caller_qualified_name == "_root.register"
    # string literal arg is not a callback
    assert "/run" not in callbacks


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
    assert any(e.route == "/reports" for e in routes), (
        f"expected /reports route, got {[e.route for e in routes]}"
    )

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


# ---------------- inline test exclusion (#100) ----------------


def extract_tests(source: str, path: str = "src/lib.rs", include_tests: bool = False):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(
        path=path,
        language="rust",
        module_path=module_path,
        source=src,
        is_package=is_package,
        include_tests=include_tests,
    )
    return EXTRACTOR.extract(parse("rust", src), ctx)


_CFG_TEST_SRC = """
use std::process::Command;

pub fn run(cmd: &str) {
    Command::new(cmd);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn helper() -> u32 { 3 }

    #[test]
    fn it_runs() {
        helper();
        Command::new("evil");
    }
}
"""


def test_cfg_test_mod_symbols_excluded_by_default():
    x = extract_tests(_CFG_TEST_SRC)
    qnames = {s.qualified_name for s in x.symbols}
    # production symbol kept
    assert "_root.run" in qnames
    # the test module and everything inside it is gone
    assert not any("tests" in q or "helper" in q or "it_runs" in q for q in qnames)


def test_cfg_test_mod_references_excluded_by_default():
    x = extract_tests(_CFG_TEST_SRC)
    callers = {r.caller_qualified_name for r in x.references}
    # the production Command::new call survives
    assert any(
        r.callee_name == "new" and r.caller_qualified_name == "_root.run" for r in x.references
    )
    # no call originates from inside the test module (helper()/Command::new in it_runs)
    assert not any(c and ("it_runs" in c or "helper" in c) for c in callers)


def test_include_tests_keeps_inline_tests():
    x = extract_tests(_CFG_TEST_SRC, include_tests=True)
    qnames = {s.qualified_name for s in x.symbols}
    assert any("it_runs" in q for q in qnames)
    assert any("tests" in q for q in qnames)


def test_bare_test_and_tokio_test_functions_excluded():
    x = extract_tests(
        """
pub fn keep() {}

#[test]
fn plain_test() { keep(); }

#[tokio::test]
async fn async_test() { keep(); }
"""
    )
    qnames = {s.qualified_name for s in x.symbols}
    assert "_root.keep" in qnames
    assert "_root.plain_test" not in qnames
    assert "_root.async_test" not in qnames


def test_cfg_feature_named_test_is_not_excluded():
    # #[cfg(feature = "test-utils")] must NOT be treated as #[cfg(test)]
    x = extract_tests(
        """
#[cfg(feature = "test-utils")]
pub mod helpers {
    pub fn scaffold() {}
}
"""
    )
    qnames = {s.qualified_name for s in x.symbols}
    assert any("scaffold" in q for q in qnames)


def test_cfg_not_test_is_production_and_kept():
    # #[cfg(not(test))] compiles in NON-test builds -> it is production code
    x = extract_tests(
        """
#[cfg(not(test))]
pub fn only_in_release() {}
"""
    )
    qnames = {s.qualified_name for s in x.symbols}
    assert "_root.only_in_release" in qnames


def test_cfg_test_impl_block_excluded():
    x = extract_tests(
        """
pub struct S;

impl S { pub fn real(&self) {} }

#[cfg(test)]
impl S {
    fn only_for_tests(&self) {}
}
"""
    )
    qnames = {s.qualified_name for s in x.symbols}
    assert "_root.S.real" in qnames
    assert "_root.S.only_for_tests" not in qnames


# ---------------- return types + call_result bindings (#113) ----------------


def test_rust_function_return_type_text():
    x = extract(
        """
pub struct Ingester;
pub fn make() -> Ingester { Ingester }
pub fn nothing() {}
""",
        path="src/lib.rs",
    )
    ret = {s.qualified_name: s.return_type_text for s in x.symbols if s.kind is SymbolKind.FUNCTION}
    assert ret["_root.make"] == "Ingester"
    assert ret["_root.nothing"] is None


def test_rust_free_call_emits_call_result_binding():
    x = extract(
        """
pub fn make() -> u32 { 3 }
pub fn run() {
    let v = make();
    let _ = v;
}
""",
        path="src/lib.rs",
    )
    b = next(b for b in x.bindings if b.name == "v")
    assert b.type_text == "make"
    assert b.kind == "call_result"
    assert b.scope == "_root.run"


def test_rust_type_new_stays_constructor():
    # Foo::new() / Foo::default() keep the direct-type "constructor" kind
    x = extract(
        """
pub struct Foo;
pub fn mk() {
    let a = Foo::new();
    let b = Foo::default();
    let _ = (a, b);
}
""",
        path="src/lib.rs",
    )
    kinds = {b.name: (b.type_text, b.kind) for b in x.bindings}
    assert kinds["a"] == ("Foo", "constructor")
    assert kinds["b"] == ("Foo", "constructor")
