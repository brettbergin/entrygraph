"""Graph explorer read API + CLI (#explorer)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from entrygraph.api import CodeGraph
from entrygraph.cli.main import main
from entrygraph.explore.api import create_app

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture(scope="module")
def index_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("idx") / "graph.db"
    g = CodeGraph.index(FLASK_APP, db=db)
    g.close()
    return db


@pytest.fixture(scope="module")
def client(index_db) -> TestClient:
    return TestClient(create_app(index_db, serve_ui=False))


@pytest.fixture(scope="module")
def repo_id(client) -> int:
    return client.get("/api/repos").json()["repos"][0]["id"]


# ---------------- repos + stats ----------------


def test_list_repos(client):
    repos = client.get("/api/repos").json()["repos"]
    assert len(repos) == 1
    assert repos[0]["name"] == "flask_app"
    assert repos[0]["symbols"] > 0


def test_stats_and_detection(client, repo_id):
    body = client.get(f"/api/repos/{repo_id}/stats").json()
    assert body["stats"]["symbols"] > 0
    assert body["stats"]["entrypoints"] > 0
    # flask_app is detected as Python + flask
    assert any(lang["name"] == "python" for lang in body["languages"])
    assert any(fw["name"] == "flask" for fw in body["frameworks"])


def test_unknown_repo_is_404(client):
    assert client.get("/api/repos/99999/stats").status_code == 404


# ---------------- symbols ----------------


def test_symbols_search(client, repo_id):
    all_syms = client.get(f"/api/repos/{repo_id}/symbols").json()["symbols"]
    assert all_syms and all(set(s) >= {"qname", "kind", "name", "file"} for s in all_syms)
    # search narrows by name glob
    hits = client.get(f"/api/repos/{repo_id}/symbols", params={"q": "report"}).json()["symbols"]
    assert hits and all("report" in s["name"].lower() for s in hits)


def test_symbols_kind_filter(client, repo_id):
    funcs = client.get(f"/api/repos/{repo_id}/symbols", params={"kind": "function"}).json()
    assert funcs["symbols"] and all(s["kind"] == "function" for s in funcs["symbols"])


# ---------------- entrypoints ----------------


def test_entrypoints(client, repo_id):
    eps = client.get(f"/api/repos/{repo_id}/entrypoints").json()["entrypoints"]
    assert eps
    routes = [e for e in eps if e["kind"] == "http_route"]
    assert routes and any(e["route"] for e in routes)
    assert any(e["framework"] == "flask" for e in eps)


# ---------------- symbol detail (callers/callees) ----------------


def test_symbol_detail_has_callers_and_callees(client, repo_id):
    # create_report -> runReport-style chain in the flask fixture
    syms = client.get(f"/api/repos/{repo_id}/symbols", params={"q": "create_report"}).json()[
        "symbols"
    ]
    assert syms
    qname = syms[0]["qname"]
    detail = client.get(f"/api/repos/{repo_id}/symbol", params={"qname": qname}).json()
    assert detail["symbol"]["qname"] == qname
    assert "callees" in detail and "callers" in detail
    assert len(detail["callees"]) >= 1  # create_report calls into the service layer


def test_symbol_detail_unknown_is_404(client, repo_id):
    r = client.get(f"/api/repos/{repo_id}/symbol", params={"qname": "does.not.exist"})
    assert r.status_code == 404


# ---------------- paths ----------------


def test_source_to_sink_paths(client, repo_id):
    body = client.get(
        f"/api/repos/{repo_id}/paths",
        params={"source_category": "http_input", "sink_category": "command_exec"},
    ).json()
    assert body["paths"]
    p = body["paths"][0]
    assert p["hops"][0]["qname"] and p["hops"][-1]["qname"].startswith("py:")
    assert p["risk"] is not None


# ---------------- call-graph neighborhood ----------------


def test_graph_neighborhood(client, repo_id):
    syms = client.get(f"/api/repos/{repo_id}/symbols", params={"q": "create_report"}).json()[
        "symbols"
    ]
    qname = syms[0]["qname"]
    g = client.get(f"/api/repos/{repo_id}/graph", params={"qname": qname}).json()
    center = [n for n in g["nodes"] if n["role"] == "center"]
    assert len(center) == 1 and center[0]["qname"] == qname
    # edges connect center to its callees
    assert any(e["from"] == qname for e in g["edges"])


# ---------------- CLI ----------------


def test_cli_repos(index_db, capsys):
    assert main(["explore", "repos", "--db", str(index_db), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and rows[0]["symbols"] > 0


def test_cli_serve_bad_db_exits_2(capsys):
    # missing index (or missing uvicorn extra) both exit 2 before serving
    assert main(["explore", "serve", "--db", "/nonexistent/x.db"]) == 2
