from __future__ import annotations

from pathlib import Path

import pytest

# Importing the ruby entrypoint module registers its rules in the shared
# EntrypointRule registry that the scanner consults. (The detect package's
# __init__ only eagerly imports python; language rule modules self-register on
# import, mirroring how third-party rules are added.)
import entrygraph.detect.entrypoints.ruby  # noqa: F401
from entrygraph import CodeGraph
from entrygraph.extract.base import FileContext
from entrygraph.extract.ruby import RubyExtractor
from entrygraph.kinds import Confidence, SymbolKind
from entrygraph.parsing.parsers import parse

EXTRACTOR = RubyExtractor()
SINATRA_APP = Path(__file__).parent / "fixtures" / "ruby" / "sinatra_app"


def extract(source: str, path: str = "app/mod.rb"):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(
        path=path, language="ruby", module_path=module_path, source=src, is_package=is_package
    )
    return EXTRACTOR.extract(parse("ruby", src), ctx)


# ---------------- unit tests ----------------


def test_module_path_for():
    assert EXTRACTOR.module_path_for("app/services/runner.rb") == ("services.runner", False)
    assert EXTRACTOR.module_path_for("lib/foo/bar.rb") == ("foo.bar", False)
    assert EXTRACTOR.module_path_for("src/util.rb") == ("util", False)
    assert EXTRACTOR.module_path_for("app.rb") == ("app", False)
    assert EXTRACTOR.module_path_for("config/routes.rb") == ("config.routes", False)


def test_modules_classes_methods_scope_chain():
    x = extract(
        """
module App
  class Base
  end

  class Runner < Base
    LIMIT = 10

    def execute(arg)
      arg
    end

    def self.boot
      42
    end
  end
end

def helper
end

TOKEN = "abc"
"""
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    assert by_qname["mod.App"].kind is SymbolKind.MODULE
    assert by_qname["mod.App.Base"].kind is SymbolKind.CLASS
    assert by_qname["mod.App.Base"].parent_qualified_name == "mod.App"

    runner = by_qname["mod.App.Runner"]
    assert runner.kind is SymbolKind.CLASS
    assert runner.bases == ["Base"]
    assert runner.parent_qualified_name == "mod.App"

    execute = by_qname["mod.App.Runner.execute"]
    assert execute.kind is SymbolKind.METHOD
    assert execute.parent_qualified_name == "mod.App.Runner"

    boot = by_qname["mod.App.Runner.boot"]
    assert boot.kind is SymbolKind.METHOD
    assert "self" in boot.modifiers  # singleton method

    assert by_qname["mod.App.Runner.LIMIT"].kind is SymbolKind.CONSTANT
    assert by_qname["mod.helper"].kind is SymbolKind.METHOD
    assert by_qname["mod.TOKEN"].kind is SymbolKind.CONSTANT

    # inherit reference emitted for the superclass
    inherits = [r for r in x.references if r.kind == "inherit"]
    assert len(inherits) == 1 and inherits[0].callee_text == "Base"
    assert inherits[0].caller_qualified_name == "mod.App.Runner"


def test_require_imports():
    x = extract(
        "require 'sinatra'\nrequire 'json'\nrequire_relative './services/runner'\n",
        path="app.rb",
    )
    imports = {(i.module, i.alias) for i in x.imports}
    assert ("sinatra", "sinatra") in imports
    assert ("json", "json") in imports
    # require_relative pre-expands to a project dotted module
    assert ("services.runner", "services.runner") in imports
    # framework signals surface the top segment so the sinatra spec fires
    assert ("import", "sinatra") in x.framework_signals
    assert ("import", "json") in x.framework_signals


def test_calls_bare_and_receiver():
    x = extract(
        """
class C
  def m(x)
    helper(x)
    Runner.new.run(x)
    system("ls")
  end
end

top_level_call(1)
"""
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}

    # bare call inside a method is modeled as an implicit-self send
    assert calls["helper"].receiver_text == "self"
    assert calls["helper"].callee_name == "helper"
    assert calls["helper"].caller_qualified_name == "mod.C.m"

    # receiver call: rightmost segment is the callee name, receiver is the chain
    assert calls["Runner.new.run"].callee_name == "run"
    assert calls["Runner.new.run"].receiver_text == "Runner.new"

    # system() inside a method: implicit-self send
    assert calls["system"].receiver_text == "self"
    assert '"ls"' in calls["system"].arg_preview

    # bare call at top level has no receiver (so route DSL calls stay bare)
    assert calls["top_level_call"].receiver_text is None
    assert calls["top_level_call"].caller_qualified_name is None


def test_partial_tree_still_extracts():
    x = extract("def good\nend\n\ndef broken(\n")
    assert not x.parse_ok
    assert any(s.name == "good" for s in x.symbols)


# ---------------- end-to-end ----------------


@pytest.fixture(scope="module")
def graph() -> CodeGraph:
    g = CodeGraph.index(SINATRA_APP)
    yield g
    g.close()
    # remove the on-disk index the facade drops next to the fixture
    db = SINATRA_APP / ".entrygraph.db"
    for p in (db, db.with_suffix(db.suffix + "-wal"), db.with_suffix(db.suffix + "-shm")):
        p.unlink(missing_ok=True)


def test_sinatra_framework_detected(graph):
    report = graph.detect()
    names = {f.name for f in report.frameworks}
    assert "sinatra" in names
    sinatra = next(f for f in report.frameworks if f.name == "sinatra")
    assert sinatra.language == "ruby"
    assert sinatra.confidence > 0.9  # Gemfile dep + require import


def test_sinatra_routes_detected(graph):
    routes = graph.entrypoints(kind="http_route", framework="sinatra")
    by_route = {e.route: e for e in routes}
    assert set(by_route) == {"/reports", "/health"}
    assert by_route["/reports"].http_method == "GET"
    assert by_route["/health"].http_method == "POST"


def test_system_sink_edge_tagged(graph):
    # Ruby cross-object calls resolve fuzzily, so the command_exec sink lands on
    # the receiver-agnostic canonical form `rb:*.system` (implicit-self send).
    refs = graph.references("rb:*.system")
    assert any(r.sink_id == "rb.command-exec.attr" for r in refs)
    assert any(r.sink_id and r.sink_id.startswith("rb.command-exec") for r in refs)


def test_source_reaches_command_exec_sink(graph):
    # The Sinatra route handler is an inline block; its handler symbol is the
    # module (`app`). From there:
    #   app -> Runner#run_report (FUZZY) -> render_and_execute (EXACT)
    #       -> system (rb:*.system, command_exec sink)
    # Fuzzy resolution makes this best-effort; assert the full path is found.
    # rb:*.system is a receiver-agnostic UNRESOLVED sink placeholder -> opt in.
    reached = graph.reachable(source="app", sink_category="command_exec", include_unresolved=True)
    assert reached, "expected the sinatra route module to reach a command_exec sink"

    paths = graph.paths(source="app", sink_category="command_exec", include_unresolved=True)
    assert paths, "expected at least one source->sink call path"
    qnames = [s.qname for s in paths[0].symbols]
    assert "services.runner.Services.Runner.run_report" in qnames
    assert "services.runner.Services.Runner.render_and_execute" in qnames


def test_project_call_edges_resolved(graph):
    # intra-class implicit-self hop resolves EXACT; cross-object hop is FUZZY.
    with graph.session() as s:
        from sqlalchemy import select

        from entrygraph.db.models import Edge, Symbol
        from entrygraph.kinds import EdgeKind

        def sym(qname):
            return s.execute(select(Symbol.id).where(Symbol.qname == qname)).scalar_one()

        def edge(src, dst):
            return (
                s.execute(
                    select(Edge).where(
                        Edge.src_symbol_id == sym(src),
                        Edge.dst_symbol_id == sym(dst),
                        Edge.kind == EdgeKind.CALLS,
                    )
                )
                .scalars()
                .all()
            )

        run = "services.runner.Services.Runner.run_report"
        rae = "services.runner.Services.Runner.render_and_execute"
        exact = edge(run, rae)
        assert exact and Confidence(exact[0].confidence) is Confidence.EXACT
