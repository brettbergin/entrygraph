from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.db.models import Edge, Repository, Symbol
from entrygraph.kinds import Confidence, EdgeKind, SymbolKind
from entrygraph.pipeline.scanner import index_repository

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture
def indexed(tmp_engine):
    stats = index_repository(FLASK_APP, tmp_engine)
    return tmp_engine, stats


def test_index_stats(indexed):
    _, stats = indexed
    assert stats.files_indexed >= 4  # routes, services, db, cli, __init__
    assert stats.symbols > 10
    assert stats.edges > 10


def test_symbols_extracted(indexed):
    engine, _ = indexed
    with Session(engine) as s:
        qnames = set(s.execute(select(Symbol.qname)).scalars())
        assert "app.routes.get_user" in qnames
        assert "app.routes.create_report" in qnames
        assert "app.services.ReportRunner" in qnames
        assert "app.services.ReportRunner.start" in qnames
        assert "app.services.run_report" in qnames
        assert "py:subprocess.run" in qnames  # external placeholder (aliased import)

        runner = s.execute(
            select(Symbol).where(Symbol.qname == "app.services.ReportRunner")
        ).scalar_one()
        assert runner.kind is SymbolKind.CLASS
        method = s.execute(
            select(Symbol).where(Symbol.qname == "app.services.ReportRunner.start")
        ).scalar_one()
        assert method.kind is SymbolKind.METHOD
        assert method.parent_id == runner.id


def test_call_edges_resolved(indexed):
    engine, _ = indexed
    with Session(engine) as s:

        def sym(qname):
            return s.execute(select(Symbol.id).where(Symbol.qname == qname)).scalar_one()

        def edge_between(src, dst):
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

        # route handler -> service function (from-import, cross-file)
        assert edge_between("app.routes.create_report", "app.services.run_report")
        # service function -> method
        assert edge_between("app.services.run_report", "app.services.ReportRunner.start")
        # method -> method (self receiver), including the cycle
        assert edge_between(
            "app.services.ReportRunner.start", "app.services.ReportRunner.render_and_execute"
        )
        assert edge_between(
            "app.services.ReportRunner.render_and_execute", "app.services.ReportRunner.start"
        )
        # method -> external sink via aliased import
        sink_edges = edge_between(
            "app.services.ReportRunner.render_and_execute", "py:subprocess.run"
        )
        assert sink_edges and sink_edges[0].confidence == Confidence.IMPORT


def test_import_edges(indexed):
    engine, _ = indexed
    with Session(engine) as s:
        routes_module = s.execute(
            select(Symbol.id).where(Symbol.qname == "app.routes")
        ).scalar_one()
        imports = (
            s.execute(
                select(Edge.dst_qname).where(
                    Edge.src_symbol_id == routes_module, Edge.kind == EdgeKind.IMPORTS
                )
            )
            .scalars()
            .all()
        )
        assert "py:flask" in imports
        assert "app.services" in imports


def test_module_symbols_satisfy_span_invariant(indexed):
    # Module rows used to be written with end_line=0 < start_line=1 (#44). Every
    # module symbol must now satisfy end_line >= start_line and span real content.
    engine, _ = indexed
    with Session(engine) as s:
        modules = s.execute(select(Symbol).where(Symbol.kind == SymbolKind.MODULE)).scalars().all()
        assert modules  # the flask fixture has several modules
        for m in modules:
            assert m.start_line == 1
            assert m.end_line >= m.start_line
        # at least one module spans past line 1 (files have content)
        assert any(m.end_line > 1 for m in modules)


def test_no_duplicate_edge_rows(indexed):
    # Literal duplicate edges (same kind/src/dst/line/sink/via) used to recur and
    # inflate counts / double-count sinks (#45); the flask fixture had 5 such groups.
    engine, _ = indexed
    with Session(engine) as s:
        edges = s.execute(
            select(Edge.kind, Edge.src_symbol_id, Edge.dst_qname, Edge.line, Edge.sink_id, Edge.via)
        ).all()
        assert len(edges) == len(set(edges))  # no identical rows


def test_reindex_is_idempotent(indexed):
    engine, first = indexed
    second = index_repository(FLASK_APP, engine)
    assert second.symbols == first.symbols
    assert second.edges == first.edges
    with Session(engine) as s:
        # each full index run bumps the generation (drives cache invalidation)
        assert s.execute(select(Repository)).scalars().one().index_generation == 2


def test_dedup_entrypoint_hints_collapses_cross_framework_duplicates():
    # Several JS frameworks share a registration shape, so the same route is
    # emitted once per detected framework. Dedup collapses them, keeping the
    # highest-confidence framework's label regardless of rule order.
    from entrygraph.extract.ir import EntrypointHint
    from entrygraph.kinds import EntrypointKind
    from entrygraph.pipeline.scanner import _dedup_entrypoint_hints

    def h(fw, route="/ping", method="GET", handler="app.ping"):
        return EntrypointHint(
            rule_id=f"javascript.{fw}.route",
            kind=EntrypointKind.HTTP_ROUTE,
            handler_qualified_name=handler,
            route=route,
            http_methods=[method],
            framework=fw,
        )

    # hono is the real framework (higher confidence); express is spurious.
    out = _dedup_entrypoint_hints(
        [h("express"), h("hono"), h("express", route="/other")],
        {"hono": 0.94, "express": 0.2},
    )
    routes = sorted((e.route, e.framework) for e in out)
    assert routes == [("/other", "express"), ("/ping", "hono")]


def test_test_files_recorded_but_not_extracted(tmp_engine, fixture_repo):
    from entrygraph.db.models import File

    repo = fixture_repo("python/flask_app")
    test_file = repo / "tests" / "test_routes.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "from app.routes import get_user\n\ndef test_get_user():\n    get_user(1)\n"
    )

    index_repository(repo, tmp_engine)
    with Session(tmp_engine) as s:
        row = s.execute(select(File).where(File.path == "tests/test_routes.py")).scalar_one()
        assert row.skip_reason == "test"  # recorded for honest stats…
        syms = s.execute(select(Symbol.id).where(Symbol.file_id == row.id)).scalars().all()
        assert syms == []  # …but never extracted
        edges = s.execute(select(Edge.id).where(Edge.src_file_id == row.id)).scalars().all()
        assert edges == []


def test_include_tests_reincludes_test_files(tmp_engine, fixture_repo):
    from entrygraph.db.models import File

    repo = fixture_repo("python/flask_app")
    test_file = repo / "tests" / "test_routes.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "from app.routes import get_user\n\ndef test_get_user():\n    get_user(1)\n"
    )

    index_repository(repo, tmp_engine, include_tests=True)
    with Session(tmp_engine) as s:
        row = s.execute(select(File).where(File.path == "tests/test_routes.py")).scalar_one()
        assert row.skip_reason is None
        qnames = set(s.execute(select(Symbol.qname).where(Symbol.file_id == row.id)).scalars())
        assert "tests.test_routes.test_get_user" in qnames
