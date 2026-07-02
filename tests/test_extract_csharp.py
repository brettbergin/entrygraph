from __future__ import annotations

from pathlib import Path

from entrygraph import CodeGraph
from entrygraph.extract.base import FileContext
from entrygraph.extract.csharp import CSharpExtractor
from entrygraph.kinds import SymbolKind
from entrygraph.parsing.parsers import parse

EXTRACTOR = CSharpExtractor()

ASPNET_APP = Path(__file__).parent / "fixtures" / "csharp" / "aspnet_app"


def extract(source: str, path: str = "src/Mod.cs"):
    module_path, is_package = EXTRACTOR.module_path_for(path)
    src = source.encode()
    ctx = FileContext(
        path=path, language="csharp", module_path=module_path,
        source=src, is_package=is_package,
    )
    return EXTRACTOR.extract(parse("csharp", src), ctx)


# ---------------- unit: module path ----------------


def test_module_path_for():
    assert EXTRACTOR.module_path_for("src/Controllers/Users.cs") == (
        "src.Controllers.Users",
        False,
    )
    assert EXTRACTOR.module_path_for("Program.cs") == ("Program", False)


# ---------------- unit: namespace-scoped qnames ----------------


def test_block_namespace_qnames():
    x = extract(
        """
namespace MyApp.Controllers
{
    public class UsersController
    {
        public string Get(int id) { return "ok"; }
    }
}
""",
        path="anywhere/Whatever.cs",
    )
    by_qname = {s.qualified_name: s for s in x.symbols}
    ctrl = by_qname["MyApp.Controllers.UsersController"]
    assert ctrl.kind is SymbolKind.CLASS
    get = by_qname["MyApp.Controllers.UsersController.Get"]
    assert get.kind is SymbolKind.METHOD
    assert get.parent_qualified_name == "MyApp.Controllers.UsersController"


def test_file_scoped_namespace_qnames():
    # C# 10 file-scoped namespace: members are SIBLINGS of the namespace node,
    # not nested — the extractor must still qualify under it.
    x = extract(
        """
namespace MyApp.Api;

public class Pinger
{
    public string Ping() { return "pong"; }
}
""",
    )
    qnames = {s.qualified_name for s in x.symbols}
    assert "MyApp.Api.Pinger" in qnames
    assert "MyApp.Api.Pinger.Ping" in qnames


def test_top_level_statements_fall_back_to_dir_module():
    # No namespace, no type: qnames fall back to the directory-derived module.
    x = extract(
        'System.Console.WriteLine("hi");\n',
        path="src/Program.cs",
    )
    # No named symbols, but extraction must not raise and calls are captured.
    assert x.parse_ok
    assert any(r.kind == "call" for r in x.references)


# ---------------- unit: attributes captured as decorators ----------------


def test_attributes_captured_as_decorators():
    x = extract(
        """
namespace App;

[ApiController]
[Route("/api")]
public class Ctrl
{
    [HttpGet("/users")]
    public string List() { return "ok"; }
}
""",
    )
    ctrl = next(s for s in x.symbols if s.qualified_name == "App.Ctrl")
    assert ctrl.decorators == ["[ApiController]", '[Route("/api")]']
    assert ctrl.bases == []
    assert ctrl.is_exported

    method = next(s for s in x.symbols if s.qualified_name == "App.Ctrl.List")
    assert method.decorators == ['[HttpGet("/users")]']

    decorator_refs = {
        (r.callee_text, r.caller_qualified_name)
        for r in x.references
        if r.kind == "decorator"
    }
    assert ("ApiController", "App.Ctrl") in decorator_refs
    assert ("HttpGet", "App.Ctrl.List") in decorator_refs


# ---------------- unit: base types -> inherit refs ----------------


def test_base_types_emit_inherit_refs():
    x = extract(
        """
namespace App;
public class Foo : BaseController, IThing { }
""",
    )
    foo = next(s for s in x.symbols if s.qualified_name == "App.Foo")
    assert foo.bases == ["BaseController", "IThing"]
    inherits = {r.callee_text for r in x.references if r.kind == "inherit"}
    assert inherits == {"BaseController", "IThing"}


# ---------------- unit: using / alias import map ----------------


def test_using_directives_and_alias():
    x = extract(
        """
using System;
using System.Diagnostics;
using Gen = System.Collections.Generic;

namespace App;
public class C { }
""",
    )
    by_alias = {i.alias: i for i in x.imports}
    # Plain usings are namespace wildcards.
    assert by_alias["*"].module in ("System", "System.Diagnostics")
    wildcards = {i.module for i in x.imports if i.alias == "*"}
    assert {"System", "System.Diagnostics"} <= wildcards
    # Aliased using maps the alias to the target namespace.
    assert "Gen" in by_alias
    assert by_alias["Gen"].module == "System.Collections.Generic"
    # framework signals carry the dotted import so prefix globs fire.
    assert ("import", "System.Diagnostics") in x.framework_signals


# ---------------- unit: invocation + object creation refs ----------------


def test_invocation_and_object_creation_refs():
    x = extract(
        """
namespace App;
public class C
{
    public void M()
    {
        helper();
        Process.Start("ls");
        service.Run("x");
        var cmd = new SqlCommand("select");
    }
}
""",
    )
    calls = {r.callee_text: r for r in x.references if r.kind == "call"}

    assert calls["helper"].receiver_text is None
    assert calls["helper"].caller_qualified_name == "App.C.M"

    # Static-type receiver (`Process`) collapses to a bare call so the resolver
    # keeps the full dotted callee (`cs:Process.Start`).
    assert calls["Process.Start"].callee_name == "Start"
    assert calls["Process.Start"].receiver_text is None

    # Instance receiver (`service`) is kept for `cs:*.Method` sink matching.
    assert calls["service.Run"].callee_name == "Run"
    assert calls["service.Run"].receiver_text == "service"

    # object creation captured as a call keyed on the constructed type.
    assert "SqlCommand" in calls
    assert calls["SqlCommand"].receiver_text is None


# ---------------- unit: partial class (duplicate qnames) ----------------


def test_partial_class_duplicate_qnames():
    x = extract(
        """
namespace App;
public partial class Widget
{
    public void A() { }
}
public partial class Widget
{
    public void B() { }
}
""",
    )
    widgets = [s for s in x.symbols if s.qualified_name == "App.Widget"]
    # Both partial declarations produce the same qname (last-wins downstream).
    assert len(widgets) == 2
    methods = {s.qualified_name for s in x.symbols if s.kind is SymbolKind.METHOD}
    assert "App.Widget.A" in methods
    assert "App.Widget.B" in methods


# ---------------- unit: static Main ----------------


def test_static_main_detected():
    x = extract(
        """
namespace App;
public class Program
{
    public static void Main(string[] args) { }
}
""",
    )
    main = next(s for s in x.symbols if s.name == "Main")
    assert "static" in main.modifiers


def test_partial_tree_still_extracts():
    x = extract(
        "namespace App;\npublic class Good { void Ok() {} void Broken( {",
    )
    assert not x.parse_ok
    assert any(s.name == "Ok" for s in x.symbols)


# ---------------- end-to-end: index the fixture app ----------------


def test_e2e_aspnetcore_detected_and_route_reaches_command_exec():
    graph = CodeGraph.index(ASPNET_APP)

    # aspnetcore framework detected above threshold
    report = graph.detect()
    aspnet = next(
        (f for f in report.frameworks if f.name == "aspnetcore"), None
    )
    assert aspnet is not None
    assert aspnet.confidence > 0.9

    # controller + minimal-api routes are http_route entrypoints
    routes = graph.entrypoints(kind="http_route")
    by_route = {ep.route: ep for ep in routes}
    assert "/reports" in by_route
    assert by_route["/reports"].http_method == "POST"
    assert by_route["/reports"].framework == "aspnetcore"

    # route handler reaches the command-exec sink through the service.
    # cs:Process.Start is an UNRESOLVED external placeholder, so opt in.
    paths = graph.paths(
        source="AspNetApp.Controllers.ReportsController.Create",
        sink="cs:Process.Start",
        include_unresolved=True,
    )
    assert paths, "expected a route -> Process.Start reachability path"
    qnames = [sym.qname for sym in paths[0].symbols]
    assert qnames[0] == "AspNetApp.Controllers.ReportsController.Create"
    assert "AspNetApp.Services.ReportService.Generate" in qnames
    assert qnames[-1] == "cs:Process.Start"
