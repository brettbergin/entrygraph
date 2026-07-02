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
    # reachable -> exit 0 and prints a chain
    rc = main(["paths", "--db", db, "--source", "app.routes.create_report",
               "--sink", "py:subprocess.run"])
    assert rc == 0
    assert "->" in capsys.readouterr().out

    # unreachable -> exit 1
    rc = main(["paths", "--db", db, "--source", "app.routes.health",
               "--sink", "py:subprocess.run"])
    assert rc == 1


def test_paths_by_category(db, capsys):
    rc = main(["paths", "--db", db, "--source", "app.routes.*",
               "--sink-category", "command_exec", "--json"])
    assert rc == 0
    paths = json.loads(capsys.readouterr().out)
    assert paths and paths[0]["symbols"][-1] == "py:subprocess.run"


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
