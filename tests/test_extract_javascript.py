from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph
from entrygraph.extract.base import FileContext
from entrygraph.extract.javascript import JavaScriptExtractor
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.parsers import parse

EXPRESS_APP = Path(__file__).parent / "fixtures" / "javascript" / "express_app"
EXTRACTOR = JavaScriptExtractor()


def extract(source: str, path: str = "src/sample.js", lang: str = "javascript"):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(path=path, language=lang, module_path=module_path,
                      source=src, is_package=is_package)
    return EXTRACTOR.extract(parse(lang, src), ctx)


def test_module_path():
    assert EXTRACTOR.module_path_for("src/routes/users.ts") == ("routes.users", False)
    assert EXTRACTOR.module_path_for("src/index.js") == ("_root", True)
    assert EXTRACTOR.module_path_for("lib/api/sample.ts") == ("api.sample", False)
    assert EXTRACTOR.module_path_for("lib/api/index.ts") == ("api", True)


def test_functions_classes_methods():
    x = extract(
        """
export function handler(req) { return 1; }
class Service extends Base {
  run() { return this.helper(); }
  helper() {}
}
const inline = (a) => a + 1;
export const CONST = 42;
"""
    )
    by = {s.qualified_name: s for s in x.symbols}
    assert by["sample.handler"].kind is SymbolKind.FUNCTION
    assert by["sample.handler"].is_exported
    assert by["sample.Service"].kind is SymbolKind.CLASS
    assert by["sample.Service"].bases == ["Base"]
    assert by["sample.Service.run"].kind is SymbolKind.METHOD
    assert by["sample.inline"].kind is SymbolKind.FUNCTION  # arrow fn bound to const
    assert "sample.CONST" in by


def test_imports_default_named_namespace():
    x = extract(
        "import express from 'express';\n"
        "import { Router, json as parseJson } from 'express';\n"
        "import * as fs from 'fs';\n"
        "import { helper } from './util';\n"
    )
    imports = {(i.module, i.imported_name, i.alias) for i in x.imports}
    assert ("express", None, "express") in imports  # default -> module
    assert ("express", "Router", "Router") in imports
    assert ("express", "json", "parseJson") in imports
    assert ("fs", "*", "fs") in imports
    assert ("util", "helper", "helper") in imports  # relative resolved to dotted


def test_relative_import_resolution():
    x = extract("import { a } from '../db/conn';\n", path="src/routes/users.js")
    assert any(i.module == "db.conn" for i in x.imports)


def test_calls_with_receivers():
    x = extract(
        "import cp from 'child_process';\n"
        "function run() { cp.execSync('ls'); helper(); }\n"
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}
    assert calls["cp.execSync"].receiver_text == "cp"
    assert calls["cp.execSync"].callee_name == "execSync"
    assert calls["helper"].receiver_text is None
    assert calls["cp.execSync"].caller_qualified_name == "sample.run"


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "js.db"
    g = CodeGraph.index(EXPRESS_APP, db=db)
    yield g
    g.close()


def test_express_end_to_end(graph):
    frameworks = {f.name for f in graph.detect().frameworks}
    assert "express" in frameworks

    routes = {e.route: e for e in graph.entrypoints(kind="http_route")}
    assert set(routes) == {"/users/:id", "/reports", "/health"}
    assert routes["/reports"].symbol.qname == "routes.createReport"

    paths = graph.paths(source="routes.createReport", sink_category="command_exec")
    assert paths
    assert paths[0].symbols[-1].qname == "js:child_process.execSync"
    assert graph.reachable(source=routes["/reports"], sink_category="command_exec")
    assert not graph.reachable(source=routes["/health"], sink_category="command_exec")


def test_reexport_and_callback_and_computed_call():
    x = extract(
        'export { A, B as C } from "./widgets";\n'
        'export * from "./other";\n'
        'function boot() {\n'
        '  setTimeout(onTick, 10);\n'
        '  registry[name]();\n'
        '}\n'
    )
    named = {(r.exported_name, r.alias) for r in x.reexports if not r.is_star}
    assert ("A", None) in named and ("B", "C") in named
    assert any(r.is_star for r in x.reexports)
    assert any(r.kind == "callback" and r.callee_name == "onTick" for r in x.references)
    assert any(r.kind == "dynamic_call" and r.callee_name == "<computed>" for r in x.references)
