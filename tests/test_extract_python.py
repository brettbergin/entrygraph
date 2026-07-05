from __future__ import annotations

from entrygraph.extract.base import FileContext
from entrygraph.extract.python import PythonExtractor
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.parsers import parse

EXTRACTOR = PythonExtractor()


def extract(source: str, path: str = "app/mod.py"):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(
        path=path, language="python", module_path=module_path, source=src, is_package=is_package
    )
    return EXTRACTOR.extract(parse("python", src), ctx)


def test_module_path_for():
    assert EXTRACTOR.module_path_for("app/mod.py") == ("app.mod", False)
    assert EXTRACTOR.module_path_for("app/__init__.py") == ("app", True)
    assert EXTRACTOR.module_path_for("src/pkg/util.py") == ("pkg.util", False)
    assert EXTRACTOR.module_path_for("main.py") == ("main", False)


def test_classes_functions_methods():
    x = extract(
        '''
class Base:
    pass

class Runner(Base):
    """Runs things."""

    LIMIT = 10

    def execute(self, arg):
        """Execute."""
        return arg

def helper():
    pass

MAX_SIZE = 100
'''
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    assert by_qname["app.mod.Runner"].kind is SymbolKind.CLASS
    assert by_qname["app.mod.Runner"].bases == ["Base"]
    assert by_qname["app.mod.Runner"].docstring == "Runs things."
    assert by_qname["app.mod.Runner.execute"].kind is SymbolKind.METHOD
    assert by_qname["app.mod.Runner.execute"].parent_qualified_name == "app.mod.Runner"
    assert by_qname["app.mod.Runner.execute"].docstring == "Execute."
    assert by_qname["app.mod.helper"].kind is SymbolKind.FUNCTION
    assert by_qname["app.mod.MAX_SIZE"].kind is SymbolKind.CONSTANT
    assert by_qname["app.mod.Runner.LIMIT"].kind is SymbolKind.FIELD
    # inherit reference emitted
    inherits = [r for r in x.references if r.kind == "inherit"]
    assert len(inherits) == 1 and inherits[0].callee_text == "Base"


def test_imports_and_aliases():
    x = extract(
        "import subprocess as sub\n"
        "import os.path\n"
        "from flask import Flask, request\n"
        "from os.path import join as j\n"
        "from . import utils\n"
        "from ..pkg import thing\n"
    )
    imports = {(i.module, i.imported_name, i.alias, i.relative_level) for i in x.imports}
    assert ("subprocess", None, "sub", 0) in imports
    assert ("os.path", None, "os", 0) in imports
    assert ("flask", "Flask", "Flask", 0) in imports
    assert ("flask", "request", "request", 0) in imports
    assert ("os.path", "join", "j", 0) in imports
    assert ("", "utils", "utils", 1) in imports
    assert ("pkg", "thing", "thing", 2) in imports
    assert ("import", "subprocess") in x.framework_signals
    assert ("import", "flask") in x.framework_signals


def test_calls_receivers_and_previews():
    x = extract(
        """
import subprocess as sub

def run_it(cmd):
    helper()
    return sub.run(cmd, shell=True)

class C:
    def m(self):
        self.other()
"""
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}
    assert calls["helper"].receiver_text is None
    assert calls["helper"].caller_qualified_name == "app.mod.run_it"
    assert calls["sub.run"].callee_name == "run"
    assert calls["sub.run"].receiver_text == "sub"
    assert "shell=True" in calls["sub.run"].arg_preview
    assert calls["sub.run"].arg_count == 2
    assert calls["self.other"].caller_qualified_name == "app.mod.C.m"


def test_decorators_captured():
    x = extract(
        """
import app_framework as fw

@fw.route("/x", methods=["GET"])
def handler():
    pass
"""
    )
    handler = next(s for s in x.symbols if s.name == "handler")
    assert handler.decorators == ['@fw.route("/x", methods=["GET"])']
    decorator_refs = [r for r in x.references if r.kind == "decorator"]
    assert decorator_refs[0].callee_text == "fw.route"


def test_partial_tree_still_extracts():
    x = extract("def good():\n    pass\n\ndef broken(:\n")
    assert not x.parse_ok
    assert any(s.name == "good" for s in x.symbols)


def test_callback_and_dynamic_call_refs():
    x = extract(
        "def handler(name):\n"
        "    schedule(worker)\n"
        "    register(cb=done)\n"
        "    getattr(obj, name)()\n"
        "    handlers[name]()\n"
        "def worker():\n    pass\n"
    )
    callbacks = {r.callee_name for r in x.references if r.kind == "callback"}
    assert {"worker", "done"} <= callbacks
    dynamic = {r.callee_name for r in x.references if r.kind == "dynamic_call"}
    assert dynamic == {"getattr", "<dynamic>"}


def test_return_type_text():
    # `-> T` annotations feed type_ref / call_result typing (#132); value-preserving
    # wrappers unwrap, container generics do not, primitives/None yield nothing
    x = extract(
        "class Recipe: ...\n"
        "def get_recipe() -> Recipe: ...\n"
        "def maybe() -> Optional[Recipe]: ...\n"
        "def union() -> Recipe | None: ...\n"
        "def qualified() -> models.Recipe: ...\n"
        "def container() -> list[Recipe]: ...\n"
        "def nothing(): ...\n"
        "class Repo:\n"
        "    def find(self) -> Recipe: ...\n"
    )
    ret = {
        s.qualified_name: s.return_type_text
        for s in x.symbols
        if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
    }
    assert ret["app.mod.get_recipe"] == "Recipe"
    assert ret["app.mod.maybe"] == "Recipe"  # Optional[T] -> T
    assert ret["app.mod.union"] == "Recipe"  # T | None -> T
    assert ret["app.mod.qualified"] == "models.Recipe"
    assert ret["app.mod.container"] is None  # list[T] is not the returned value's type
    assert ret["app.mod.nothing"] is None
    assert ret["app.mod.Repo.find"] == "Recipe"
