from __future__ import annotations

from pathlib import Path

from entrygraph.api import CodeGraph
from entrygraph.extract.base import FileContext
from entrygraph.extract.php import PhpExtractor
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.parsers import parse

EXTRACTOR = PhpExtractor()

FIXTURE = Path(__file__).parent / "fixtures" / "php" / "laravel_app"


def extract(source: str, path: str = "app/Http/Controllers/Mod.php"):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(
        path=path, language="php", module_path=module_path, source=src, is_package=is_package
    )
    return EXTRACTOR.extract(parse("php", src), ctx)


# ---------------- unit: definitions & namespaces ----------------


def test_namespace_scoped_qnames_and_separator_normalized():
    x = extract(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "class ReportController {\n"
        "    const LIMIT = 10;\n"
        "    public $field = 1;\n"
        "    public function store($cmd) { return $cmd; }\n"
        "}\n"
        "function bare() {}\n"
    )
    assert x.module_path == "App.Http.Controllers"
    by_qname = {s.qualified_name: s for s in x.symbols}
    cls = by_qname["App.Http.Controllers.ReportController"]
    assert cls.kind is SymbolKind.CLASS
    method = by_qname["App.Http.Controllers.ReportController.store"]
    assert method.kind is SymbolKind.METHOD
    assert method.parent_qualified_name == "App.Http.Controllers.ReportController"
    assert by_qname["App.Http.Controllers.ReportController.LIMIT"].kind is SymbolKind.CONSTANT
    assert by_qname["App.Http.Controllers.ReportController.field"].kind is SymbolKind.FIELD
    assert by_qname["App.Http.Controllers.bare"].kind is SymbolKind.FUNCTION
    # no backslashes survive anywhere
    assert all("\\" not in s.qualified_name for s in x.symbols)


def test_namespaceless_module_path_falls_back_to_directory():
    x = extract("<?php\nfunction go() {}\n", path="public/index.php")
    assert x.module_path == "public.index"
    assert not any("\\" in s.qualified_name for s in x.symbols)


def test_use_aliases_normalized():
    x = extract("<?php\nnamespace App;\nuse App\\Services\\Runner;\nuse App\\Foo as Bar;\n")
    imports = {(i.module, i.imported_name, i.alias) for i in x.imports}
    assert ("App.Services.Runner", "Runner", "Runner") in imports
    assert ("App.Foo", "Foo", "Bar") in imports
    assert ("import", "App.Services.Runner") in x.framework_signals


def test_inheritance_and_interfaces():
    x = extract("<?php\nnamespace App;\nclass C extends Base implements Handler {}\n")
    cls = next(s for s in x.symbols if s.name == "C")
    assert cls.bases == ["Base", "Handler"]
    inherits = [r for r in x.references if r.kind == "inherit"]
    implements = [r for r in x.references if r.kind == "implement"]
    assert inherits[0].callee_text == "Base"
    assert implements[0].callee_text == "Handler"


# ---------------- unit: calls & receivers ----------------


def test_scoped_vs_member_receivers():
    x = extract(
        "<?php\n"
        "namespace App;\n"
        "class C {\n"
        "    public function m($obj) {\n"
        "        Runner::run($x);\n"
        "        $obj->handle(1);\n"
        "        $this->helper();\n"
        "        foo($y);\n"
        "    }\n"
        "}\n"
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}
    # Foo::bar() -> receiver Foo, callee bar
    assert calls["Runner.run"].receiver_text == "Runner"
    assert calls["Runner.run"].callee_name == "run"
    # $obj->m() -> receiver $obj
    assert calls["$obj.handle"].receiver_text == "$obj"
    assert calls["$obj.handle"].callee_name == "handle"
    # $this receiver preserved for the resolver
    assert calls["$this.helper"].receiver_text == "$this"
    # bare function call
    assert calls["foo"].receiver_text is None
    assert calls["foo"].caller_qualified_name == "App.C.m"


def test_php8_attributes_are_decorators():
    x = extract(
        "<?php\nnamespace App;\nclass C {\n    #[Route('/x')]\n    public function h() {}\n}\n"
    )
    handler = next(s for s in x.symbols if s.name == "h")
    assert handler.decorators == ["#[Route('/x')]"]
    decorator_refs = [r for r in x.references if r.kind == "decorator"]
    assert decorator_refs and decorator_refs[0].callee_name == "Route"


def test_include_variable_is_a_call_ref():
    x = extract("<?php\nnamespace App;\nfunction load($f) {\n    include $f;\n}\n")
    includes = [r for r in x.references if r.callee_name == "include"]
    assert len(includes) == 1
    assert includes[0].kind == "call"
    assert includes[0].receiver_text is None
    assert "$f" in (includes[0].arg_preview or "")
    assert includes[0].caller_qualified_name == "App.load"


# ---------------- unit: mixed HTML & robustness ----------------


def test_mixed_html_php_extracts_symbols():
    x = extract(
        "<html>\n"
        "<body><h1>Report</h1>\n"
        "<?php\n"
        "namespace App;\n"
        "function render_it($data) {\n"
        "    echo $data;\n"
        "}\n"
        "?>\n"
        "<footer>done</footer>\n"
        "</html>\n",
        path="app/view.php",
    )
    assert x.parse_ok
    assert any(s.name == "render_it" for s in x.symbols)


def test_broken_syntax_does_not_crash_and_reports_parse_error():
    x = extract("<?php\nfunction good() {}\nfunction broken( {\n")
    assert not x.parse_ok
    # partial extraction still recovers the good function
    assert any(s.name == "good" for s in x.symbols)


# ---------------- e2e: laravel fixture ----------------


def test_laravel_fixture_route_to_sink(tmp_path):
    db = tmp_path / "graph.db"
    graph = CodeGraph.index(FIXTURE, db=db)
    try:
        detected = {f.name for f in graph.detect().frameworks}
        assert "laravel" in detected

        routes = graph.entrypoints(kind="http_route")
        assert any(e.route == "/reports" for e in routes)

        store_qname = "App.Http.Controllers.ReportController.store"
        assert any(s.qname == store_qname for s in graph.symbols())

        paths = graph.paths(source=store_qname, sink="php:shell_exec", include_unresolved=True)
        assert paths, "expected a store -> shell_exec path"
        assert paths[0].symbols[-1].qname == "php:shell_exec"
    finally:
        graph.close()
