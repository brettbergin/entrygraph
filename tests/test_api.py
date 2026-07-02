from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph, SymbolNotFoundError
from entrygraph.errors import DatabaseNotFoundError

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture(scope="module")
def graph(tmp_path_factory) -> CodeGraph:
    db = tmp_path_factory.mktemp("db") / "graph.db"
    g = CodeGraph.index(FLASK_APP, db=db)
    yield g
    g.close()


def test_open_missing_db(tmp_path):
    with pytest.raises(DatabaseNotFoundError):
        CodeGraph.open(tmp_path / "nope.db")


def test_open_existing(graph, tmp_path_factory):
    # re-open the same db independently
    db = tmp_path_factory.mktemp("db2") / "graph.db"
    g = CodeGraph.index(FLASK_APP, db=db)
    g.close()
    with CodeGraph.open(db) as reopened:
        assert reopened.stats().symbols > 0


def test_symbols_glob(graph):
    classes = graph.symbols(kind="class")
    assert any(s.qname == "app.services.ReportRunner" for s in classes)

    globbed = graph.symbols(qname="app.services.*")
    assert {"app.services.run_report", "app.services.ReportRunner"} <= {s.qname for s in globbed}

    named = graph.symbols(name="run_*")
    assert any(s.name == "run_report" for s in named)

    by_file = graph.symbols(file="app/routes.py")
    assert all(s.file == "app/routes.py" for s in by_file)
    assert any(s.name == "create_report" for s in by_file)


def test_symbol_exact(graph):
    sym = graph.symbol("app.services.ReportRunner.start")
    assert sym.kind == "method"
    assert sym.file == "app/services.py"
    with pytest.raises(SymbolNotFoundError):
        graph.symbol("does.not.exist")


def test_iter_symbols(graph):
    seen = list(graph.iter_symbols(batch_size=5))
    assert len(seen) == len(graph.symbols())


def test_files_and_detect(graph):
    files = graph.files(language="python")
    assert any(f.path == "app/services.py" for f in files)
    report = graph.detect()
    assert any(lang.name == "python" for lang in report.languages)


def test_callers_callees(graph):
    callers = graph.callers("app.services.run_report")
    assert any(c.qname == "app.routes.create_report" for c in callers)

    callees = graph.callees("app.services.run_report")
    assert any(c.qname == "app.services.ReportRunner.start" for c in callees)

    # depth=2 reaches the subprocess sink from run_report
    deep = graph.callees("app.services.run_report", depth=3)
    assert any(c.qname == "py:subprocess.run" for c in deep)

    with pytest.raises(SymbolNotFoundError):
        graph.callers("no.such.symbol")


def test_references(graph):
    refs = graph.references("app.services.run_report")
    assert {r.src_qname for r in refs} >= {"app.routes.create_report", "cli.report"}
    assert all(r.resolved for r in refs)


def test_paths_source_to_sink(graph):
    paths = graph.paths(source="app.routes.create_report", sink="py:subprocess.run")
    assert paths, "expected a route -> subprocess.run path"
    best = paths[0]
    qnames = [s.qname for s in best.symbols]
    assert qnames[0] == "app.routes.create_report"
    assert qnames[-1] == "py:subprocess.run"
    assert "app.services.run_report" in qnames
    assert len(best.edges) == len(best.symbols) - 1
    assert "->" in best.render()

    # glob sources work; cycle in fixture must not hang path enumeration
    globbed = graph.paths(source="app.routes.*", sink="py:subprocess.run", max_paths=5)
    assert 1 <= len(globbed) <= 5


def test_paths_no_route(graph):
    # health endpoint never reaches subprocess
    assert graph.paths(source="app.routes.health", sink="py:subprocess.run") == []
    assert not graph.reachable(source="app.routes.health", sink="py:subprocess.run")
    assert graph.reachable(source="app.routes.create_report", sink="py:subprocess.run")


def test_max_depth_enforced(graph):
    assert not graph.reachable(
        source="app.routes.create_report", sink="py:subprocess.run", max_depth=1
    )


def test_stats_and_sql_escape_hatch(graph):
    stats = graph.stats()
    assert stats.symbols > 0 and stats.edges > 0
    assert stats.resolved_edges <= stats.edges

    rows = graph.sql("SELECT COUNT(*) AS n FROM symbols")
    assert rows[0]["n"] == stats.symbols

    with graph.session() as s:
        from sqlalchemy import select

        from entrygraph.db.models import Symbol as SymbolModel

        assert s.execute(select(SymbolModel).limit(1)).scalar_one() is not None
