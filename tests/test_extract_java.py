from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.api import CodeGraph
from entrygraph.db.models import Detection, Edge, Entrypoint, Symbol
from entrygraph.extract.base import FileContext
from entrygraph.extract.java import JavaExtractor
from entrygraph.kinds import EdgeKind, EntrypointKind, SymbolKind
from entrygraph.parsing.parsers import parse
from entrygraph.pipeline.scanner import index_repository

EXTRACTOR = JavaExtractor()

SPRING_APP = Path(__file__).parent / "fixtures" / "java" / "spring_app"


def extract(source: str, path: str = "src/main/java/com/example/Mod.java"):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(path=path, language="java", module_path=module_path,
                      source=src, is_package=is_package)
    return EXTRACTOR.extract(parse("java", src), ctx)


# ---------------- unit: module path ----------------


def test_module_path_for():
    assert EXTRACTOR.module_path_for(
        "src/main/java/com/example/UserController.java"
    ) == ("com.example.UserController", False)
    assert EXTRACTOR.module_path_for(
        "src/test/java/com/example/FooTest.java"
    ) == ("com.example.FooTest", False)
    assert EXTRACTOR.module_path_for("com/example/Bar.java") == ("com.example.Bar", False)
    assert EXTRACTOR.module_path_for("Main.java") == ("Main", False)


# ---------------- unit: classes / methods / fields ----------------


def test_classes_methods_fields():
    x = extract(
        """
package com.example;

public class Runner extends Base implements Runnable, AutoCloseable {

    private static final int LIMIT = 10;
    public String name;

    public String execute(String arg) {
        return arg;
    }

    protected void helper() {}
}
""",
        path="src/main/java/com/example/Runner.java",
    )
    by_qname = {s.qualified_name: s for s in x.symbols}

    runner = by_qname["com.example.Runner"]
    assert runner.kind is SymbolKind.CLASS
    assert runner.bases == ["Base", "Runnable", "AutoCloseable"]
    assert runner.is_exported  # public

    execute = by_qname["com.example.Runner.execute"]
    assert execute.kind is SymbolKind.METHOD
    assert execute.parent_qualified_name == "com.example.Runner"
    assert "public" in execute.modifiers

    assert by_qname["com.example.Runner.LIMIT"].kind is SymbolKind.CONSTANT
    assert by_qname["com.example.Runner.name"].kind is SymbolKind.FIELD

    # extends -> inherit refs; implements -> implement refs (distinct edge kinds)
    inherits = {r.callee_text for r in x.references if r.kind == "inherit"}
    assert inherits == {"Base"}
    implements = {r.callee_text for r in x.references if r.kind == "implement"}
    assert implements == {"Runnable", "AutoCloseable"}


def test_interface_extracted():
    x = extract(
        """
package com.example;
public interface Repo {
    String find(String id);
}
""",
        path="src/main/java/com/example/Repo.java",
    )
    repo = next(s for s in x.symbols if s.qualified_name == "com.example.Repo")
    assert repo.kind is SymbolKind.INTERFACE


# ---------------- unit: annotations captured as decorators ----------------


def test_annotations_captured_as_decorators():
    x = extract(
        """
package com.example;

import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class Ctrl {

    @GetMapping("/users")
    public String list() { return "ok"; }
}
""",
        path="src/main/java/com/example/Ctrl.java",
    )
    ctrl = next(s for s in x.symbols if s.qualified_name == "com.example.Ctrl")
    assert ctrl.decorators == ["@RestController", '@RequestMapping("/api")']

    method = next(s for s in x.symbols if s.qualified_name == "com.example.Ctrl.list")
    assert method.decorators == ['@GetMapping("/users")']

    decorator_refs = {(r.callee_text, r.caller_qualified_name)
                      for r in x.references if r.kind == "decorator"}
    assert ("RestController", "com.example.Ctrl") in decorator_refs
    assert ("GetMapping", "com.example.Ctrl.list") in decorator_refs


# ---------------- unit: imports ----------------


def test_imports_and_wildcards():
    x = extract(
        """
package com.example;

import org.springframework.web.bind.annotation.RestController;
import java.util.List;
import java.util.*;
""",
        path="src/main/java/com/example/Mod.java",
    )
    imports = {(i.module, i.imported_name, i.alias) for i in x.imports}
    assert (
        "org.springframework.web.bind.annotation.RestController",
        "RestController",
        "RestController",
    ) in imports
    assert ("java.util.List", "List", "List") in imports
    assert ("java.util", "*", "*") in imports

    # framework signals carry the full dotted import so prefix globs fire
    assert ("import", "org.springframework.web.bind.annotation.RestController") in x.framework_signals


# ---------------- unit: calls / receivers ----------------


def test_calls_and_receivers():
    x = extract(
        """
package com.example;

public class C {
    public void m() {
        helper();
        Runtime.getRuntime().exec("ls");
        service.run("x");
        new ProcessBuilder("ls").start();
    }
}
""",
        path="src/main/java/com/example/C.java",
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}
    assert calls["helper"].receiver_text is None
    assert calls["helper"].caller_qualified_name == "com.example.C.m"

    exec_call = calls["Runtime.getRuntime().exec"]
    assert exec_call.callee_name == "exec"
    assert exec_call.receiver_text == "Runtime.getRuntime()"

    assert calls["service.run"].callee_name == "run"
    assert calls["service.run"].receiver_text == "service"

    # object creation is captured as a call keyed on the constructed type
    assert "ProcessBuilder" in calls


# ---------------- unit: main detection ----------------


def test_main_detected():
    x = extract(
        """
package com.example;
public class App {
    public static void main(String[] args) {
        System.out.println("hi");
    }
}
""",
        path="src/main/java/com/example/App.java",
    )
    main = next(s for s in x.symbols if s.name == "main")
    assert "static" in main.modifiers and "public" in main.modifiers
    assert "String[]" in main.signature


def test_partial_tree_still_extracts():
    x = extract(
        "package com.example;\npublic class Good { void ok() {} \nvoid broken( {",
        path="src/main/java/com/example/Good.java",
    )
    assert not x.parse_ok
    assert any(s.name == "ok" for s in x.symbols)


# ---------------- end-to-end: index the fixture app ----------------


@pytest.fixture
def indexed(tmp_engine):
    stats = index_repository(SPRING_APP, tmp_engine)
    return tmp_engine, stats


def test_index_symbols(indexed):
    engine, _ = indexed
    with Session(engine) as s:
        qnames = set(s.execute(select(Symbol.qname)).scalars())
        assert "com.example.UserController" in qnames
        assert "com.example.UserController.createReport" in qnames
        assert "com.example.ReportService.buildReport" in qnames
        assert "com.example.ReportRunner.executeShell" in qnames
        assert "com.example.Application.main" in qnames
        assert "java:*.exec" in qnames  # external sink placeholder


def test_spring_boot_detected(indexed):
    engine, _ = indexed
    with Session(engine) as s:
        frameworks = {
            row.name for row in s.execute(
                select(Detection).where(Detection.category == "framework")
            ).scalars()
        }
        assert "spring-boot" in frameworks


def test_spring_routes_are_http_entrypoints(indexed):
    engine, _ = indexed
    with Session(engine) as s:
        routes = s.execute(
            select(Entrypoint.route, Entrypoint.http_method, Entrypoint.framework).where(
                Entrypoint.kind == EntrypointKind.HTTP_ROUTE
            )
        ).all()
        by_route = {r.route: r for r in routes}
        assert "/users/{id}" in by_route
        assert by_route["/users/{id}"].http_method == "GET"
        assert by_route["/users/{id}"].framework == "spring-boot"
        assert "/reports" in by_route
        assert by_route["/reports"].http_method == "POST"

        # the language-core main entrypoint is also detected
        mains = s.execute(
            select(Entrypoint).where(Entrypoint.kind == EntrypointKind.MAIN)
        ).scalars().all()
        assert mains


def test_exec_call_is_tagged_as_sink(indexed):
    engine, _ = indexed
    with Session(engine) as s:
        exec_edges = s.execute(
            select(Edge).where(Edge.dst_qname == "java:*.exec", Edge.kind == EdgeKind.CALLS)
        ).scalars().all()
        assert exec_edges
        assert all(e.sink_id == "java.command-exec" for e in exec_edges)


def test_route_reaches_command_exec_sink(indexed):
    engine, _ = indexed
    graph = CodeGraph(engine)
    # java:*.exec is a receiver-agnostic UNRESOLVED sink placeholder, so opt in
    # to unresolved-edge traversal.
    paths = graph.paths(
        source="com.example.UserController.createReport",
        sink_category="command_exec",
        include_unresolved=True,
    )
    assert paths, "expected a route -> exec reachability path"
    path = paths[0]
    qnames = [sym.qname for sym in path.symbols]
    assert qnames[0] == "com.example.UserController.createReport"
    assert "com.example.ReportService.buildReport" in qnames
    assert "com.example.ReportRunner.executeShell" in qnames
    assert qnames[-1] == "java:*.exec"
