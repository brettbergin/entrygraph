from __future__ import annotations

from pathlib import Path

from entrygraph.api import CodeGraph
from entrygraph.extract.base import FileContext
from entrygraph.extract.golang import GoExtractor
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.parsers import parse

EXTRACTOR = GoExtractor()

GIN_APP = Path(__file__).parent / "fixtures" / "go" / "gin_app"


def extract(source: str, path: str = "cmd/server/main.go"):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(
        path=path, language="go", module_path=module_path, source=src, is_package=is_package
    )
    return EXTRACTOR.extract(parse("go", src), ctx)


# ---------------- unit tests ----------------


def test_module_path_for():
    # Go module paths are package-directory based; the file stem is dropped.
    assert EXTRACTOR.module_path_for("cmd/server/main.go") == ("cmd.server", False)
    assert EXTRACTOR.module_path_for("main.go") == ("_root", False)
    assert EXTRACTOR.module_path_for("src/pkg/util.go") == ("pkg", False)
    assert EXTRACTOR.module_path_for("internal/svc/run.go") == ("internal.svc", False)


def test_functions_and_exported_detection():
    x = extract(
        """
package server

func Run() error {
	return nil
}

func helper() {}

const MaxRetries = 3
var buffer = 10
"""
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    assert by_qname["cmd.server.Run"].kind is SymbolKind.FUNCTION
    assert by_qname["cmd.server.Run"].is_exported is True
    assert by_qname["cmd.server.helper"].kind is SymbolKind.FUNCTION
    assert by_qname["cmd.server.helper"].is_exported is False
    assert by_qname["cmd.server.MaxRetries"].kind is SymbolKind.CONSTANT
    assert by_qname["cmd.server.MaxRetries"].is_exported is True
    assert by_qname["cmd.server.buffer"].kind is SymbolKind.VARIABLE
    assert by_qname["cmd.server.buffer"].is_exported is False


def test_structs_interfaces_and_fields():
    x = extract(
        """
package server

type Server struct {
	Name string
	port int
}

type Handler interface {
	Handle()
}
"""
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    assert by_qname["cmd.server.Server"].kind is SymbolKind.STRUCT
    assert by_qname["cmd.server.Handler"].kind is SymbolKind.INTERFACE
    name_field = by_qname["cmd.server.Server.Name"]
    assert name_field.kind is SymbolKind.FIELD
    assert name_field.parent_qualified_name == "cmd.server.Server"
    assert name_field.is_exported is True
    assert by_qname["cmd.server.Server.port"].is_exported is False


def test_methods_with_receivers():
    x = extract(
        """
package server

type Server struct{}

func (s *Server) Run() error {
	return s.doRun()
}

func (s Server) doRun() error {
	return nil
}
"""
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    run = by_qname["cmd.server.Server.Run"]
    assert run.kind is SymbolKind.METHOD
    assert run.parent_qualified_name == "cmd.server.Server"
    assert run.is_exported is True
    # value receiver too, unexported method
    dorun = by_qname["cmd.server.Server.doRun"]
    assert dorun.kind is SymbolKind.METHOD
    assert dorun.parent_qualified_name == "cmd.server.Server"
    assert dorun.is_exported is False
    # the self-receiver call carries the enclosing method qname
    call = next(r for r in x.references if r.callee_text == "s.doRun")
    assert call.caller_qualified_name == "cmd.server.Server.Run"
    assert call.receiver_text == "s"
    assert call.callee_name == "doRun"


def test_imports_with_aliases():
    x = extract(
        """
package server

import (
	"fmt"
	"os/exec"
	f "strings"
)

import "net/http"
"""
    )
    imports = {(i.module, i.alias) for i in x.imports}
    assert ("fmt", "fmt") in imports
    assert ("os/exec", "exec") in imports  # alias = last path segment
    assert ("strings", "f") in imports  # explicit alias
    assert ("net/http", "http") in imports  # single-line form, alias = last segment
    # full import paths become framework signals
    assert ("import", "os/exec") in x.framework_signals
    assert ("import", "net/http") in x.framework_signals


def test_calls_with_selectors_and_receivers():
    x = extract(
        """
package server

import "os/exec"

func run(cmd string) {
	helper()
	exec.Command("ls", "-la")
}

func helper() {}
"""
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}
    assert calls["helper"].receiver_text is None
    assert calls["helper"].caller_qualified_name == "cmd.server.run"
    exec_call = calls["exec.Command"]
    assert exec_call.callee_name == "Command"
    assert exec_call.receiver_text == "exec"
    assert exec_call.caller_qualified_name == "cmd.server.run"
    assert exec_call.arg_count == 2
    assert "ls" in exec_call.arg_preview


def test_function_value_argument_is_a_callback():
    # http.HandleFunc("/", handler) passes `handler` as a value; it must be
    # emitted as a callback so the handler is reachable from the registration site.
    x = extract(
        """
package server

import "net/http"

func register() {
	http.HandleFunc("/run", handler)
}

func handler(w http.ResponseWriter, r *http.Request) {}
"""
    )
    callbacks = {r.callee_name: r for r in x.references if r.kind == "callback"}
    assert "handler" in callbacks
    assert callbacks["handler"].caller_qualified_name == "cmd.server.register"
    # string/other literal args are not callbacks
    assert "/run" not in callbacks


def test_partial_tree_still_extracts():
    x = extract("package p\n\nfunc good() {}\n\nfunc broken( {\n")
    assert not x.parse_ok
    assert any(s.name == "good" for s in x.symbols)


# ---------------- end-to-end index test ----------------


def test_index_gin_app_reachability(tmp_engine):
    graph = CodeGraph(tmp_engine)
    from entrygraph.pipeline.scanner import index_repository

    index_repository(GIN_APP, tmp_engine)

    # gin framework detected from the manifest + import
    report = graph.detect()
    fw_names = {f.name for f in report.frameworks}
    assert "gin" in fw_names
    assert "net/http" in fw_names

    # HTTP routes discovered
    routes = graph.entrypoints(kind="http_route")
    assert routes, "expected at least one http_route entrypoint"
    route_values = {e.route for e in routes}
    assert "/reports/:name" in route_values
    gin_routes = [e for e in routes if e.framework == "gin"]
    assert gin_routes

    # the go main entrypoint exists
    mains = graph.entrypoints(kind="main")
    assert any(e.symbol and e.symbol.qname == "_root.main" for e in mains)

    # the external command_exec sink is present with the observed qname
    exec_sink = graph.symbols(qname="go:os/exec.Command", include_external=True)
    assert exec_sink, "expected external symbol go:os/exec.Command"

    # source -> sink reachability: route handler reaches os/exec.Command
    # through RunReport -> execReport (a 3-hop chain).
    by_qname = graph.paths(source="_root.reportHandler", sink="go:os/exec.Command")
    assert by_qname, "expected a path from reportHandler to go:os/exec.Command"
    chain = [s.qname for s in by_qname[0].symbols]
    assert chain[0] == "_root.reportHandler"
    assert chain[-1] == "go:os/exec.Command"
    assert "_root.RunReport" in chain
    assert "_root.execReport" in chain

    by_category = graph.paths(source="_root.reportHandler", sink_category="command_exec")
    assert by_category, "expected a command_exec-category path from reportHandler"
