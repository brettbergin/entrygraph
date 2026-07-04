from __future__ import annotations

import json
from pathlib import Path

import pytest

from entrygraph.cli.main import main

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "graph.db"
    assert main(["index", str(FLASK_APP), "--db", str(path)]) == 0
    return str(path)


def test_index_json(tmp_path, capsys):
    path = tmp_path / "g.db"
    rc = main(["index", str(FLASK_APP), "--db", str(path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["symbols"] > 0
    assert path.exists()


def test_detect(db, capsys):
    assert main(["detect", "--db", db, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert any(f["name"] == "flask" for f in report["frameworks"])


def test_symbols_table(db, capsys):
    assert main(["symbols", "--db", db, "--kind", "class"]) == 0
    out = capsys.readouterr().out
    assert "app.services.ReportRunner" in out
    assert "QNAME" in out  # header


def test_entrypoints_json(db, capsys):
    assert main(["entrypoints", "--db", db, "--framework", "flask", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    routes = {r["route"] for r in rows}
    assert "/reports" in routes


def test_paths_exit_codes(db, capsys):
    # reachable -> exit 0 and renders a tree ending at the sink, with a risk score
    rc = main(
        ["paths", "--db", db, "--source", "app.routes.create_report", "--sink", "py:subprocess.run"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "py:subprocess.run" in out  # sink node rendered
    assert "risk" in out  # risk indicator present
    assert "app.routes.create_report" in out  # source node rendered

    # unreachable -> exit 1
    rc = main(["paths", "--db", db, "--source", "app.routes.health", "--sink", "py:subprocess.run"])
    assert rc == 1


def test_paths_requires_a_sink(db, capsys):
    # A query with a source but no sink would silently print "no paths found",
    # which reads like a clean result — it must error instead.
    rc = main(["paths", "--db", db, "--source", "*"])
    assert rc == 2
    assert "provide --sink" in capsys.readouterr().err


def test_paths_requires_a_source(db, capsys):
    rc = main(["paths", "--db", db, "--sink-category", "command_exec"])
    assert rc == 2
    assert "provide --source" in capsys.readouterr().err


def test_paths_by_category(db, capsys):
    rc = main(
        [
            "paths",
            "--db",
            db,
            "--source",
            "app.routes.*",
            "--sink-category",
            "command_exec",
            "--json",
        ]
    )
    assert rc == 0
    paths = json.loads(capsys.readouterr().out)
    assert paths and paths[0]["symbols"][-1] == "py:subprocess.run"


NETHTTP_APP = Path(__file__).parent / "fixtures" / "go" / "nethttp_app"


def test_paths_include_callbacks(tmp_path, capsys):
    # a handler passed to http.HandleFunc is severed unless callback edges are
    # traversed; --include-callbacks flips the query from unreachable to reachable.
    db = str(tmp_path / "go.db")
    assert main(["index", str(NETHTTP_APP), "--db", db]) == 0
    args = [
        "paths",
        "--db",
        db,
        "--source",
        "_root.main",
        "--sink-category",
        "command_exec",
        "--include-unresolved",
    ]
    assert main(args) == 1  # handler severed -> no path
    capsys.readouterr()
    assert main([*args, "--include-callbacks"]) == 0  # reachable via the callback edge
    assert "handler" in capsys.readouterr().out  # the callback-bound node is on the path


def test_callers(db, capsys):
    assert main(["callers", "--db", db, "app.services.run_report"]) == 0
    assert "app.routes.create_report" in capsys.readouterr().out


def test_stats(db, capsys):
    assert main(["stats", "--db", db]) == 0
    assert "symbols" in capsys.readouterr().out


def test_error_on_missing_db(tmp_path, capsys):
    rc = main(["stats", "--db", str(tmp_path / "nope.db")])
    assert rc == 2
    assert "error:" in capsys.readouterr().err
