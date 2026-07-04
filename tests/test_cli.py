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


def test_paths_show_literal_source_and_sink_lines(db, capsys):
    args = [
        "paths",
        "--db",
        db,
        "--source-category",
        "http_input",
        "--sink-category",
        "command_exec",
    ]
    # JSON carries the literal lines read from the indexed repo on disk
    assert main([*args, "--json"]) == 0
    p = json.loads(capsys.readouterr().out)[0]
    assert p["source_line"] and "def create_report" in p["source_line"]
    assert p["sink_line"] and "run(" in p["sink_line"]  # the actual dangerous call
    # the finding card renders the sink call site too
    assert main(args) == 0
    assert "run(" in capsys.readouterr().out


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


def test_detect_shows_taint_coverage(db, capsys):
    assert main(["detect", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "TAINT CATALOG" in out
    assert "full" in out  # python is full-tier


def test_stats_shows_coverage_line(db, capsys):
    assert main(["stats", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "taint catalog:" in out
    assert "python full" in out


def test_paths_thin_coverage_caveat(tmp_path, capsys):
    # a rust repo (minimal tier) with a low path count gets the coverage note
    repo = tmp_path / "rustapp"
    repo.mkdir()
    (repo / "main.rs").write_text(
        "fn main() { let cmd = std::env::args().nth(1).unwrap(); "
        "std::process::Command::new(cmd); }\n"
    )
    dbp = tmp_path / "rust.db"
    assert main(["index", str(repo), "--db", str(dbp)]) == 0
    main(
        [
            "paths",
            "--db",
            str(dbp),
            "--source-category",
            "env_input",
            "--sink-category",
            "command_exec",
        ]
    )
    err = capsys.readouterr().err
    assert "minimal taint coverage" in err
    assert "may reflect coverage, not safety" in err


def test_paths_no_caveat_on_full_coverage(db, capsys):
    main(
        [
            "paths",
            "--db",
            db,
            "--source",
            "app.routes.create_report",
            "--sink",
            "py:subprocess.run",
        ]
    )
    err = capsys.readouterr().err
    assert "coverage" not in err


def test_paths_cli_arg_category(tmp_path, capsys):
    cli_app = Path(__file__).parent / "fixtures" / "python" / "cli_app"
    dbp = tmp_path / "cli.db"
    assert main(["index", str(cli_app), "--db", str(dbp)]) == 0
    capsys.readouterr()
    rc = main(
        [
            "paths",
            "--db",
            str(dbp),
            "--source-category",
            "cli_arg",
            "--sink-category",
            "command_exec",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "cli_arg" in out
    assert "deploy" in out  # the click handler card
    assert "subprocess.run" in out


def test_paths_render_source_channel_and_key(tmp_path, capsys):
    channels_app = Path(__file__).parent / "fixtures" / "python" / "channels_app"
    dbp = tmp_path / "ch.db"
    assert main(["index", str(channels_app), "--db", str(dbp)]) == 0
    capsys.readouterr()
    rc = main(
        [
            "paths",
            "--db",
            str(dbp),
            "--source-category",
            "http_input",
            "--sink-category",
            "command_exec",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # provenance segment (· explicit / · handler) precedes the channel now (#96)
    assert 'query "q"' in out
    assert 'header "X-Api-Key"' in out
    assert "http_input · explicit" in out


def test_paths_json_source_channel_and_key(tmp_path, capsys):
    channels_app = Path(__file__).parent / "fixtures" / "python" / "channels_app"
    dbp = tmp_path / "chj.db"
    assert main(["index", str(channels_app), "--db", str(dbp)]) == 0
    capsys.readouterr()
    rc = main(
        [
            "paths",
            "--db",
            str(dbp),
            "--json",
            "--source-category",
            "http_input",
            "--sink-category",
            "command_exec",
        ]
    )
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    pairs = {(r["source_channel"], r["source_key"]) for r in rows}
    assert ("query", "q") in pairs
    assert ("header", "X-Api-Key") in pairs


def test_paths_explicit_vs_handler_label_and_flag(tmp_path, capsys):
    app = Path(__file__).parent / "fixtures" / "python" / "source_split"
    dbp = tmp_path / "ss.db"
    assert main(["index", str(app), "--db", str(dbp)]) == 0
    capsys.readouterr()
    # default: both handlers reported, labeled by provenance
    assert (
        main(
            [
                "paths",
                "--db",
                str(dbp),
                "--source-category",
                "http_input",
                "--sink-category",
                "command_exec",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "http_input · explicit" in out
    assert "http_input · handler" in out
    # --explicit-sources drops the handler-as-source finding
    main(
        [
            "paths",
            "--db",
            str(dbp),
            "--explicit-sources",
            "--source-category",
            "http_input",
            "--sink-category",
            "command_exec",
        ]
    )
    out = capsys.readouterr().out
    assert "explicit_handler" in out
    assert "implicit_handler" not in out


def test_paths_json_source_kind(tmp_path, capsys):
    app = Path(__file__).parent / "fixtures" / "python" / "source_split"
    dbp = tmp_path / "ssj.db"
    assert main(["index", str(app), "--db", str(dbp)]) == 0
    capsys.readouterr()
    main(
        [
            "paths",
            "--db",
            str(dbp),
            "--json",
            "--source-category",
            "http_input",
            "--sink-category",
            "command_exec",
        ]
    )
    rows = json.loads(capsys.readouterr().out)
    kinds = {r["source_kind"] for r in rows}
    assert "explicit" in kinds
    assert kinds & {"handler", "handler_params"}


def test_paths_taint_hops_flag_and_multihop_label(tmp_path, capsys):
    src = (
        "import subprocess\n"
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        "@app.route('/x')\n"
        "def h():\n"
        "    q = request.args.get('q')\n"
        "    return run(q)\n"
        "def run(cmd): subprocess.run(cmd)\n"
    )
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "app.py").write_text(src)
    dbp = tmp_path / "ip.db"
    assert main(["index", str(repo), "--db", str(dbp)]) == 0
    capsys.readouterr()
    rc = main(
        [
            "paths",
            "--db",
            str(dbp),
            "--source-category",
            "http_input",
            "--sink-category",
            "command_exec",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "flow: confirmed (1 hop)" in out  # 2-hop path -> 1 interior hop
    # --taint-hops 0 disables the interprocedural check -> no confirmed flow line
    main(
        [
            "paths",
            "--db",
            str(dbp),
            "--taint-hops",
            "0",
            "--source-category",
            "http_input",
            "--sink-category",
            "command_exec",
        ]
    )
    out0 = capsys.readouterr().out
    assert "flow: confirmed" not in out0
