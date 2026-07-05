from __future__ import annotations

from entrygraph.extract.ir import FileExtraction, RawImport, RawReference, Span
from entrygraph.kinds import Confidence, EdgeKind, SymbolKind
from entrygraph.resolve.externals import ExternalRegistry
from entrygraph.resolve.resolver import FileResolver
from entrygraph.resolve.symbol_table import SymbolTable

SPAN = Span(1, 0, 1, 10)


def make_table() -> SymbolTable:
    table = SymbolTable()
    table.add_module("app.services", 1)
    table.add_module("app.routes", 2)
    table.add_symbol(10, "app.services.run_report", "run_report", SymbolKind.FUNCTION)
    table.add_symbol(11, "app.services.Runner", "Runner", SymbolKind.CLASS)
    table.add_symbol(12, "app.services.Runner.execute", "execute", SymbolKind.METHOD)
    table.add_symbol(13, "app.services.Runner.render", "render", SymbolKind.METHOD)
    return table


def make_resolver(table, refs=(), imports=(), module="app.routes", module_id=2, sink_registry=None):
    x = FileExtraction(
        path="app/routes.py",
        language="python",
        module_path=module,
        parse_ok=True,
        error_count=0,
        imports=list(imports),
        references=list(refs),
    )
    externals = ExternalRegistry(iter(range(100, 200)).__next__, repo_id=1)
    return FileResolver(x, module_id, table, externals, sink_registry=sink_registry), externals


def ref(callee_text, callee_name=None, receiver=None, caller=None, kind="call"):
    return RawReference(
        kind=kind,
        callee_text=callee_text,
        callee_name=callee_name or callee_text.rsplit(".", 1)[-1],
        receiver_text=receiver,
        span=SPAN,
        caller_qualified_name=caller,
    )


def test_import_based_project_resolution():
    table = make_table()
    imports = [
        RawImport(module="app.services", imported_name="run_report", alias="run_report", span=SPAN)
    ]
    resolver, _ = make_resolver(table, [ref("run_report")], imports)
    edges = resolver.resolve()
    call = next(e for e in edges if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 10
    assert call.confidence is Confidence.IMPORT


def test_decorator_refs_resolve_to_decorates_not_calls():
    # An annotation/attribute/derive application (kind="decorator") must not inflate
    # the call graph; it resolves to a DECORATES edge, never CALLS (#43).
    table = make_table()
    resolver, _ = make_resolver(
        table, [ref("run_report", caller="app.routes.handler", kind="decorator")]
    )
    edges = resolver.resolve()
    assert all(e.kind is not EdgeKind.CALLS for e in edges)
    decorates = [e for e in edges if e.kind is EdgeKind.DECORATES]
    assert decorates and decorates[0].dst_symbol_id == 10


def test_relative_import_from_package_init_resolves_submodule():
    # `from .sub import thing` in the package's own __init__.py (module_path
    # "app", is_package=True) must resolve to the project module "app.sub".
    # Regression: is_package was hardcoded False, dropping "app" and yielding a
    # bogus ".sub" external placeholder.
    table = make_table()
    table.add_module("app.sub", 20)
    x = FileExtraction(
        path="app/__init__.py",
        language="python",
        module_path="app",
        parse_ok=True,
        error_count=0,
        imports=[
            RawImport(
                module="sub",
                imported_name="thing",
                alias="thing",
                span=SPAN,
                is_relative=True,
                relative_level=1,
            )
        ],
    )
    externals = ExternalRegistry(iter(range(100, 200)).__next__, repo_id=1)
    resolver = FileResolver(x, 1, table, externals, is_package=True)
    edges = resolver.resolve()
    imp = next(e for e in edges if e.kind is EdgeKind.IMPORTS)
    assert imp.dst_qname == "app.sub"
    assert imp.dst_symbol_id == 20


def test_external_import_creates_placeholder():
    table = make_table()
    imports = [RawImport(module="subprocess", imported_name=None, alias="sub", span=SPAN)]
    resolver, externals = make_resolver(table, [ref("sub.run", receiver="sub")], imports)
    edges = resolver.resolve()
    call = next(e for e in edges if e.kind is EdgeKind.CALLS)
    assert call.dst_qname == "py:subprocess.run"
    assert call.confidence is Confidence.IMPORT
    assert externals.by_qname["py:subprocess.run"] == call.dst_symbol_id
    # import edge to external module node too
    imp = next(e for e in edges if e.kind is EdgeKind.IMPORTS)
    assert imp.dst_qname == "py:subprocess"


def test_module_local_exact():
    table = make_table()
    table.add_symbol(20, "app.routes.helper", "helper", SymbolKind.FUNCTION)
    resolver, _ = make_resolver(table, [ref("helper", caller="app.routes.get_user")])
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 20
    assert call.confidence is Confidence.EXACT


def test_self_method_resolution():
    table = make_table()
    resolver, _ = make_resolver(
        table,
        [ref("self.render", receiver="self", caller="app.services.Runner.execute")],
        module="app.services",
        module_id=1,
    )
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 13
    assert call.confidence is Confidence.EXACT


def test_self_method_via_base_class():
    table = make_table()
    table.add_symbol(30, "app.services.Special", "Special", SymbolKind.CLASS)
    # class_parents holds resolved parent FQNs (populated by resolve_hierarchy)
    table.class_parents["app.services.Special"] = ["app.services.Runner"]
    resolver, _ = make_resolver(
        table,
        [ref("self.execute", receiver="self", caller="app.services.Special.go")],
        module="app.services",
        module_id=1,
    )
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 12  # Runner.execute via the ancestor walk
    assert call.confidence is Confidence.EXACT


def test_self_method_via_transitive_base_chain():
    table = make_table()
    table.add_symbol(30, "app.services.Special", "Special", SymbolKind.CLASS)
    table.add_symbol(31, "app.services.Mid", "Mid", SymbolKind.CLASS)
    # Special -> Mid -> Runner; execute lives on Runner (grandparent)
    table.class_parents["app.services.Special"] = ["app.services.Mid"]
    table.class_parents["app.services.Mid"] = ["app.services.Runner"]
    resolver, _ = make_resolver(
        table,
        [ref("self.execute", receiver="self", caller="app.services.Special.go")],
        module="app.services",
        module_id=1,
    )
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 12  # found two levels up
    assert call.confidence is Confidence.EXACT


def test_fuzzy_unique_name():
    table = make_table()
    resolver, _ = make_resolver(table, [ref("run_report")])  # no import evidence
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 10
    assert call.confidence is Confidence.FUZZY


def test_unresolved_gets_prefixed_placeholder():
    table = make_table()
    resolver, externals = make_resolver(
        table, [ref("cursor.execute", receiver="cursor"), ref("eval")]
    )
    edges = [e for e in resolver.resolve() if e.kind is EdgeKind.CALLS]
    # attribute call with unknown receiver -> receiver-agnostic guess
    # (Runner.execute exists but attribute fuzzy requires METHOD uniqueness — execute is unique)
    by_qname = {e.dst_qname: e for e in edges}
    assert "py:eval" in by_qname
    assert by_qname["py:eval"].confidence is Confidence.UNRESOLVED


def test_sink_named_attribute_call_is_not_fuzzy_bound():
    # `cursor.execute(...)` guesses to the sink `py:*.execute`. Even though a
    # unique project method `Runner.execute` (id 12) exists, we must NOT fuzzy-bind
    # to it — that would rewrite dst_qname and erase the SQL sink. Keep the guess.
    from entrygraph.detect.taint import builtin_registry

    table = make_table()
    resolver, _ = make_resolver(
        table, [ref("cursor.execute", receiver="cursor")], sink_registry=builtin_registry()
    )
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS and e.via != "cha")
    assert call.dst_qname == "py:*.execute"
    assert call.confidence is Confidence.UNRESOLVED
    assert call.dst_symbol_id != 12


def test_non_sink_attribute_call_still_fuzzy_binds_with_registry():
    # `obj.render` is not a sink, so unique-name fuzzy binding still applies even
    # with a sink registry present — the fix is targeted to sink names only.
    from entrygraph.detect.taint import builtin_registry

    table = make_table()
    resolver, _ = make_resolver(
        table, [ref("obj.render", receiver="obj")], sink_registry=builtin_registry()
    )
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 13  # Runner.render
    assert call.confidence is Confidence.FUZZY


def test_relative_import_expansion():
    table = make_table()
    table.add_symbol(40, "app.utils", 40 and "utils", SymbolKind.MODULE)
    table.project_modules.add("app.utils")
    table.module_symbol_ids["app.utils"] = 40
    imports = [
        RawImport(
            module="",
            imported_name="utils",
            alias="utils",
            span=SPAN,
            is_relative=True,
            relative_level=1,
        )
    ]
    resolver, _ = make_resolver(table, [], imports)
    assert resolver.import_map["utils"] == "app.utils"


# ------------- S2: hierarchy / wildcards / re-exports / callbacks / dynamic / CHA -------------


def test_wildcard_import_expansion():
    table = make_table()
    imports = [RawImport(module="app.services", imported_name="*", alias="*", span=SPAN)]
    resolver, _ = make_resolver(table, [ref("run_report")], imports)
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 10  # bound via the wildcard source module
    assert call.confidence is Confidence.IMPORT


def test_inheritance_cycle_terminates():
    table = make_table()
    table.add_symbol(30, "app.services.A", "A", SymbolKind.CLASS)
    table.add_symbol(31, "app.services.B", "B", SymbolKind.CLASS)
    table.class_parents["app.services.A"] = ["app.services.B"]
    table.class_parents["app.services.B"] = ["app.services.A"]  # cycle
    resolver, _ = make_resolver(
        table,
        [ref("self.missing", receiver="self", caller="app.services.A.go")],
        module="app.services",
        module_id=1,
    )
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    # no such method anywhere; walk must terminate and fall through to a guess
    assert call.confidence is Confidence.UNRESOLVED


def test_reexport_chain_followed():
    table = make_table()
    # barrel module `app.api` re-exports Runner from app.services
    table.add_module("app.api", 3)
    table.reexports["app.api"] = {"Runner": ("app.services", "Runner")}
    imports = [RawImport(module="app.api", imported_name="Runner", alias="Runner", span=SPAN)]
    resolver, _ = make_resolver(table, [ref("Runner")], imports)
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 11  # chased through the barrel to the real symbol
    assert call.via == "reexport"


def test_callback_to_project_function_only():
    table = make_table()
    # `run_report` is a project function -> callback edge; `noise` is not -> dropped
    resolver, _ = make_resolver(
        table,
        [ref("run_report", kind="callback"), ref("noise", kind="callback")],
    )
    cb = [e for e in resolver.resolve() if e.kind is EdgeKind.PASSED_AS_CALLBACK]
    assert len(cb) == 1
    assert cb[0].dst_symbol_id == 10


def test_dynamic_call_placeholder():
    table = make_table()
    resolver, _ = make_resolver(
        table,
        [
            ref("getattr", callee_name="getattr", kind="dynamic_call"),
            ref("registry[x]", callee_name="<dynamic>", kind="dynamic_call"),
        ],
    )
    edges = [e for e in resolver.resolve() if e.via == "dynamic"]
    qnames = {e.dst_qname for e in edges}
    assert qnames == {"py:getattr.*", "py:<dynamic>"}
    assert all(e.confidence is Confidence.UNRESOLVED for e in edges)


def test_cha_candidates_for_unknown_receiver():
    table = make_table()
    # two Handler subclasses each define process(); a call on an unknown receiver
    # should fan out to both as FUZZY via="cha" edges.
    table.add_symbol(40, "app.services.Base", "Base", SymbolKind.CLASS)
    table.add_symbol(41, "app.services.HandlerA", "HandlerA", SymbolKind.CLASS)
    table.add_symbol(42, "app.services.HandlerB", "HandlerB", SymbolKind.CLASS)
    table.add_symbol(43, "app.services.HandlerA.process", "process", SymbolKind.METHOD)
    table.add_symbol(44, "app.services.HandlerB.process", "process", SymbolKind.METHOD)
    table.class_parents["app.services.HandlerA"] = ["app.services.Base"]
    table.class_parents["app.services.HandlerB"] = ["app.services.Base"]
    resolver, _ = make_resolver(table, [ref("h.process", receiver="h")])
    cha = [e for e in resolver.resolve() if e.via == "cha"]
    assert {e.dst_symbol_id for e in cha} == {43, 44}
    assert all(e.confidence is Confidence.FUZZY for e in cha)


def test_cha_survives_many_unrelated_same_name_methods():
    # 2 process() methods share a hierarchy; 8 more standalone classes also define
    # process() (10 total > the fan-out cap). The related pair must still be found
    # — regression: the cap was applied to the raw count before hierarchy filtering,
    # zeroing the legitimate pair.
    table = make_table()
    table.add_symbol(40, "app.services.Base", "Base", SymbolKind.CLASS)
    table.add_symbol(41, "app.services.HandlerA", "HandlerA", SymbolKind.CLASS)
    table.add_symbol(42, "app.services.HandlerB", "HandlerB", SymbolKind.CLASS)
    table.add_symbol(43, "app.services.HandlerA.process", "process", SymbolKind.METHOD)
    table.add_symbol(44, "app.services.HandlerB.process", "process", SymbolKind.METHOD)
    table.class_parents["app.services.HandlerA"] = ["app.services.Base"]
    table.class_parents["app.services.HandlerB"] = ["app.services.Base"]
    for i in range(8):  # unrelated standalone classes, each with its own process()
        table.add_symbol(50 + i, f"app.mod.Loner{i}", f"Loner{i}", SymbolKind.CLASS)
        table.add_symbol(60 + i, f"app.mod.Loner{i}.process", "process", SymbolKind.METHOD)
    resolver, _ = make_resolver(table, [ref("h.process", receiver="h")])
    cha = {e.dst_symbol_id for e in resolver.resolve() if e.via == "cha"}
    assert cha == {43, 44}


def test_chained_call_result_does_not_leak_args_into_dst_qname():
    # `re.search(pattern).groups` is a member access on a call result; the dotted
    # path is not a real qname. It must collapse to `py:*.groups`, not embed the
    # call arguments verbatim (regression: hundreds-of-chars garbage dst_qnames).
    from entrygraph.extract.ir import RawImport

    table = make_table()
    imports = [RawImport(module="re", imported_name="re", alias="re", span=SPAN)]
    r = ref("re.search(pattern).groups", callee_name="groups", receiver="re.search(pattern)")
    resolver, _ = make_resolver(table, [r], imports)
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_qname == "py:*.groups"
    assert "(" not in call.dst_qname


def test_bare_unresolved_name_keeps_its_dotted_text():
    # a non-chained unknown bare call still keeps its readable dotted guess
    table = make_table()
    resolver, _ = make_resolver(table, [ref("mystery_helper")])
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_qname == "py:mystery_helper"


def test_fuzzy_resolution_does_not_cross_languages():
    # A Ruby call to render() must not fuzzy-bind to a uniquely-named JS render();
    # cross-language unique-name matching pollutes callers/callees in polyglot repos.
    table = SymbolTable()
    table.add_module("app.rb_mod", 1, "ruby")
    table.add_symbol(20, "front.widget.render", "render", SymbolKind.FUNCTION, "javascript")
    x = FileExtraction(
        path="app/thing.rb",
        language="ruby",
        module_path="app.rb_mod",
        parse_ok=True,
        error_count=0,
        references=[ref("render")],
    )
    externals = ExternalRegistry(iter(range(100, 200)).__next__, repo_id=1)
    resolver = FileResolver(x, 1, table, externals)
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    # unresolved (no same-language target), not a FUZZY bind to the JS symbol
    assert call.dst_symbol_id != 20
    assert call.confidence is Confidence.UNRESOLVED


def test_fuzzy_resolution_binds_within_same_language():
    # the same call resolves when the unique target shares the caller's language
    table = SymbolTable()
    table.add_module("app.rb_mod", 1, "ruby")
    table.add_symbol(21, "app.helpers.render", "render", SymbolKind.FUNCTION, "ruby")
    x = FileExtraction(
        path="app/thing.rb",
        language="ruby",
        module_path="app.rb_mod",
        parse_ok=True,
        error_count=0,
        references=[ref("render")],
    )
    externals = ExternalRegistry(iter(range(100, 200)).__next__, repo_id=1)
    resolver = FileResolver(x, 1, table, externals)
    call = next(e for e in resolver.resolve() if e.kind is EdgeKind.CALLS)
    assert call.dst_symbol_id == 21 and call.confidence is Confidence.FUZZY


def test_cha_candidates_scoped_to_language():
    # two related classes define handle(); a same-named method in another language
    # must not be pulled into the virtual-dispatch candidate set.
    from entrygraph.resolve.hierarchy import cha_candidates

    table = SymbolTable()
    table.add_symbol(1, "app.Base", "Base", SymbolKind.CLASS, "php")
    table.add_symbol(2, "app.A", "A", SymbolKind.CLASS, "php")
    table.add_symbol(3, "app.B", "B", SymbolKind.CLASS, "php")
    table.add_symbol(4, "app.A.handle", "handle", SymbolKind.METHOD, "php")
    table.add_symbol(5, "app.B.handle", "handle", SymbolKind.METHOD, "php")
    table.add_symbol(6, "front.C.handle", "handle", SymbolKind.METHOD, "javascript")
    table.class_parents["app.A"] = ["app.Base"]
    table.class_parents["app.B"] = ["app.Base"]
    got = set(cha_candidates(table, "handle", language="php"))
    assert got == {4, 5}  # the JS handle() (id 6) is excluded
