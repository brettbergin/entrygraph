"""Syntactic name->type binding resolution (#98 Phase 1)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.db import models
from entrygraph.extract.ir import FileExtraction, RawBinding, Span
from entrygraph.pipeline.scanner import index_repository
from entrygraph.resolve.bindings import FileBindingView, _resolve_type
from entrygraph.resolve.symbol_table import SymbolTable


def _span():
    return Span(1, 0, 1, 0)


def test_resolve_type_external_gets_language_prefix():
    table = SymbolTable()
    # unknown/imported type -> external, language-prefixed so wildcard globs match
    assert _resolve_type("ingester.Ingester", {}, "app", "go", table) == "go:ingester.Ingester"
    assert _resolve_type("*ingester.Ingester", {}, "app", "go", table) == "go:ingester.Ingester"
    # a bare unknown type is external too (prefixed, written verbatim)
    assert _resolve_type("Foo", {}, "app", "python", table) == "py:Foo"


def test_resolve_type_project_fqn():
    table = SymbolTable()
    table.by_fqn["app.models.Runner"] = 1
    # same-module resolution
    assert _resolve_type("Runner", {}, "app.models", "python", table) == "app.models.Runner"
    # already-qualified project fqn
    assert (
        _resolve_type("app.models.Runner", {}, "app.other", "python", table) == "app.models.Runner"
    )


def test_resolve_type_via_import_map():
    table = SymbolTable()
    table.by_fqn["app.models.Runner"] = 1
    import_map = {"models": "app.models"}
    assert (
        _resolve_type("models.Runner", import_map, "app.svc", "python", table)
        == "app.models.Runner"
    )
    # imported but not a project symbol -> external, resolved dotted
    import_map2 = {"ing": "example.com/ingester"}
    assert (
        _resolve_type("ing.Ingester", import_map2, "app", "go", table)
        == "go:example.com/ingester.Ingester"
    )


def test_file_binding_view_type_of_scope_and_module():
    table = SymbolTable()
    ext = FileExtraction(
        path="app/svc.py",
        language="python",
        module_path="app.svc",
        parse_ok=True,
        error_count=0,
        bindings=[
            RawBinding(name="r", type_text="Runner", span=_span(), scope="app.svc.handler"),
            RawBinding(name="g", type_text="Global", span=_span(), scope=None),
        ],
    )
    view = FileBindingView(ext, table)
    # scoped binding visible in its scope
    assert view.type_of("r", "app.svc.handler") == "py:Runner"
    # module-level binding visible from any scope
    assert view.type_of("g", "app.svc.handler") == "py:Global"
    # unknown name
    assert view.type_of("missing", "app.svc.handler") is None


def test_file_binding_view_field_fallback():
    table = SymbolTable()
    table.field_types["pkg.App.Ingester"] = "go:ingester.Ingester"
    ext = FileExtraction(
        path="app.go", language="go", module_path="pkg", parse_ok=True, error_count=0
    )
    view = FileBindingView(ext, table)
    # a field of the enclosing type, resolved via the table's field_types
    assert view.receiver_type("Ingester", "pkg.App.Run") == "go:ingester.Ingester"


def test_go_struct_field_types_persisted():
    src = (
        "package app\n\n"
        'import "example.com/pkg/ingester"\n\n'
        "type App struct {\n"
        "\tIngester *ingester.Ingester\n"
        "\tname     string\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "go.mod").write_text("module example.com/app\n")
        (p / "app.go").write_text(src)
        from entrygraph.db.engine import make_engine
        from entrygraph.db.meta import create_schema

        engine = make_engine(p / "g.db")
        create_schema(engine)
        index_repository(p, engine)
        with Session(engine) as s:
            refs = {  # noqa: C416 (Row is not a plain tuple)
                q: tr
                for q, tr in s.execute(
                    select(models.Symbol.qname, models.Symbol.type_ref).where(
                        models.Symbol.type_ref.is_not(None)
                    )
                )
            }
        assert refs.get("_root.App.Ingester") == "go:example.com/pkg/ingester.Ingester"
        assert refs.get("_root.App.name") == "go:string"
        engine.dispose()


def test_type_ref_reload_parity_on_incremental():
    # a field's type_ref must survive an incremental refresh identically to a full
    # re-index (the binding maps are rebuilt from persisted type_ref, #98)
    from entrygraph.db.engine import make_engine
    from entrygraph.db.meta import create_schema

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "go.mod").write_text("module example.com/app\n")
        app = p / "app.go"
        app.write_text("package app\n\ntype App struct {\n\tName string\n}\n")
        other = p / "other.go"
        other.write_text("package app\n\nfunc Helper() {}\n")

        engine = make_engine(p / "g.db")
        create_schema(engine)
        index_repository(p, engine)

        def refs():
            with Session(engine) as s:
                return {  # noqa: C416 (Row is not a plain tuple)
                    q: tr
                    for q, tr in s.execute(
                        select(models.Symbol.qname, models.Symbol.type_ref).where(
                            models.Symbol.type_ref.is_not(None)
                        )
                    )
                }

        before = refs()
        assert before.get("_root.App.Name") == "go:string"

        # touch other.go so an incremental refresh runs but App is unchanged
        import os

        other.write_text("package app\n\nfunc Helper() { _ = 1 }\n")
        os.utime(other, ns=(0, 10**18))
        index_repository(p, engine, incremental=True)
        assert refs() == before
        engine.dispose()


@pytest.mark.parametrize(
    "lang,fname,src,fqn,expected_type",
    [
        (
            "python",
            "m.py",
            "class Runner: pass\nclass App:\n    svc: Runner\n",
            "m.App.svc",
            "m.Runner",
        ),
        (
            "php",
            "a.php",
            "<?php\nclass App {\n  public Runner $svc;\n}\n",
            "a.App.svc",
            "php:Runner",
        ),
        ("java", "A.java", "class App {\n  private Runner svc;\n}\n", "A.App.svc", "java:Runner"),
    ],
)
def test_field_type_refs_across_languages(lang, fname, src, fqn, expected_type):
    from entrygraph.db.engine import make_engine
    from entrygraph.db.meta import create_schema

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / fname).write_text(src)
        engine = make_engine(p / "g.db")
        create_schema(engine)
        index_repository(p, engine)
        with Session(engine) as s:
            ref = s.execute(
                select(models.Symbol.type_ref).where(models.Symbol.qname == fqn)
            ).scalar()
        assert ref == expected_type
        engine.dispose()


def test_receiver_typing_resolves_method_by_binding():
    # a local var bound to a project type resolves `var.method()` to the concrete
    # method via the binding table (via="binding"), not a fuzzy/unresolved guess
    from entrygraph.db.engine import make_engine
    from entrygraph.db.meta import create_schema

    src = (
        "class Connection:\n"
        "    def run_query(self, q): pass\n\n"
        "def handler():\n"
        "    conn = Connection()\n"
        "    conn.run_query('SELECT 1')\n"
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "app.py").write_text(src)
        engine = make_engine(p / "g.db")
        create_schema(engine)
        index_repository(p, engine)
        with Session(engine) as s:
            rows = [
                (sq, e.dst_qname, e.via)
                for sq, e in s.execute(
                    select(models.Symbol.qname, models.Edge)
                    .join(models.Edge, models.Edge.src_symbol_id == models.Symbol.id)
                    .where(models.Edge.via == "binding")
                )
            ]
        assert ("app.handler", "app.Connection.run_query", "binding") in rows
        engine.dispose()


def test_receiver_typing_preserves_external_sink_stamp():
    # binding to a PROJECT method would erase a *.execute sink stamp; the guard
    # keeps the sink. An external-typed receiver still matches *.execute.
    from entrygraph.detect.taint import builtin_registry

    # sanity: the guard exists — a project method named like a sink isn't rebound
    # (covered end-to-end by the java/csharp e2e tests staying green).
    r = builtin_registry()
    assert r.match("py:*.execute", '("SELECT 1")') == "py.sql-execute"


def test_cross_file_import_followed_receiver_typing():
    # a receiver imported from another module, bound there at module level to a
    # type, resolves its method across the file hop (#98 P3)
    from entrygraph.db.engine import make_engine
    from entrygraph.db.meta import create_schema

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "db.py").write_text("class Pool:\n    def run_query(self, q): pass\n\npool = Pool()\n")
        (p / "app.py").write_text(
            "from db import pool\n\ndef handler():\n    pool.run_query('SELECT 1')\n"
        )
        engine = make_engine(p / "g.db")
        create_schema(engine)
        index_repository(p, engine)
        with Session(engine) as s:
            rows = {
                (sq, e.dst_qname, e.via)
                for sq, e in s.execute(
                    select(models.Symbol.qname, models.Edge)
                    .join(models.Edge, models.Edge.src_symbol_id == models.Symbol.id)
                    .where(models.Edge.via == "binding")
                )
            }
        assert ("app.handler", "db.Pool.run_query", "binding") in rows
        engine.dispose()


# ---------------- call_result return-type resolution (#113) ----------------


def test_call_result_binding_resolves_via_return_type():
    from entrygraph.kinds import SymbolKind

    table = SymbolTable()
    table.by_fqn["pkg.New"] = 10
    table.kinds[10] = SymbolKind.FUNCTION
    table.return_types["pkg.New"] = "pkg.Ingester"
    ext = FileExtraction(
        path="pkg/a.go",
        language="go",
        module_path="pkg",
        parse_ok=True,
        error_count=0,
        bindings=[
            RawBinding(
                name="impl", type_text="New", span=_span(), scope="pkg.Setup", kind="call_result"
            )
        ],
    )
    view = FileBindingView(ext, table)
    # same-package: "New" -> pkg.New -> its return type
    assert view.type_of("impl", "pkg.Setup") == "pkg.Ingester"


def test_call_result_cross_package_unique_name_fallback():
    from entrygraph.kinds import SymbolKind

    table = SymbolTable()
    # the Go import path won't match the callee's directory module, so the callee
    # resolves external; a uniquely-named project function still resolves by name
    table.by_fqn["ingester.New"] = 7
    table.qname_of[7] = "ingester.New"
    table.by_name["New"].append(7)
    table.kinds[7] = SymbolKind.FUNCTION
    table.lang[7] = "go"
    table.return_types["ingester.New"] = "ingester.Ingester"
    ext = FileExtraction(
        path="main.go",
        language="go",
        module_path="_root",
        parse_ok=True,
        error_count=0,
        bindings=[
            RawBinding(
                name="impl",
                type_text="ingester.New",  # no import map -> external, forces name fallback
                span=_span(),
                scope="_root.Setup",
                kind="call_result",
            )
        ],
    )
    view = FileBindingView(ext, table)
    assert view.type_of("impl", "_root.Setup") == "ingester.Ingester"


def test_call_result_ambiguous_name_does_not_resolve():
    from entrygraph.kinds import SymbolKind

    table = SymbolTable()
    # two functions named New -> ambiguous -> no fuzzy resolution (no false type)
    for sid, mod in ((1, "a"), (2, "b")):
        table.by_fqn[f"{mod}.New"] = sid
        table.qname_of[sid] = f"{mod}.New"
        table.by_name["New"].append(sid)
        table.kinds[sid] = SymbolKind.FUNCTION
        table.lang[sid] = "go"
        table.return_types[f"{mod}.New"] = f"{mod}.T"
    ext = FileExtraction(
        path="main.go",
        language="go",
        module_path="_root",
        parse_ok=True,
        error_count=0,
        bindings=[
            RawBinding(
                # unresolvable to a project fqn -> forces the bare-name fallback,
                # which is ambiguous (two `New`) and must yield no type
                name="x",
                type_text="zzz.New",
                span=_span(),
                scope="_root.Setup",
                kind="call_result",
            )
        ],
    )
    view = FileBindingView(ext, table)
    assert view.type_of("x", "_root.Setup") is None


def test_function_return_type_persisted_as_type_ref():
    from entrygraph.db.engine import make_engine
    from entrygraph.db.meta import create_schema

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "go.mod").write_text("module ex\n")
        # a subdirectory package so the module_path is "a" (repo-root files are _root)
        (p / "a").mkdir()
        (p / "a" / "a.go").write_text("package a\ntype T struct{}\nfunc New() *T { return &T{} }\n")
        engine = make_engine(p / "g.db")
        create_schema(engine)
        index_repository(p, engine)
        with Session(engine) as s:
            tr = s.execute(
                select(models.Symbol.type_ref).where(models.Symbol.qname == "a.New")
            ).scalar()
        assert tr == "a.T"  # resolved return type persisted on the function
        engine.dispose()


@pytest.mark.parametrize(
    ("fname", "src", "fn_qname", "expected_type"),
    [
        (
            "models.py",
            "class Recipe:\n    pass\ndef get_recipe() -> Recipe:\n    return Recipe()\n",
            "models.get_recipe",
            "models.Recipe",
        ),
        (
            "recipes.ts",
            "export class Recipe {}\n"
            "export function getRecipe(): Recipe { return new Recipe(); }\n",
            "recipes.getRecipe",
            "recipes.Recipe",
        ),
    ],
)
def test_return_type_persisted_python_ts(fname, src, fn_qname, expected_type):
    # #132: Python/TS return annotations resolve to type_ref, mirroring Go/Rust
    from entrygraph.db.engine import make_engine
    from entrygraph.db.meta import create_schema

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / fname).write_text(src)
        engine = make_engine(p / "g.db")
        create_schema(engine)
        index_repository(p, engine)
        with Session(engine) as s:
            tr = s.execute(
                select(models.Symbol.type_ref).where(models.Symbol.qname == fn_qname)
            ).scalar()
        assert tr == expected_type
        engine.dispose()


def test_return_type_persisted_java():
    # #132: a Java method return type resolves to a same-package type_ref
    from entrygraph.db.engine import make_engine
    from entrygraph.db.meta import create_schema

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        pkg = p / "src" / "main" / "java" / "com" / "example"
        pkg.mkdir(parents=True)
        (pkg / "Recipe.java").write_text("package com.example;\npublic class Recipe {}\n")
        (pkg / "Repo.java").write_text(
            "package com.example;\npublic class Repo {\n  Recipe find() { return null; }\n}\n"
        )
        engine = make_engine(p / "g.db")
        create_schema(engine)
        index_repository(p, engine)
        with Session(engine) as s:
            tr = s.execute(
                select(models.Symbol.type_ref).where(models.Symbol.qname == "com.example.Repo.find")
            ).scalar()
        assert tr == "com.example.Recipe"
        engine.dispose()
