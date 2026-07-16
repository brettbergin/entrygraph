"""Unified server /api/v1 read surface (phase 1: dev-mode auth)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from entrygraph.api import CodeGraph
from entrygraph.server.app import create_app
from entrygraph.server.config import ServerConfig

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    tmp = tmp_path_factory.mktemp("server")
    db = tmp / "graph.db"
    g = CodeGraph.index(FLASK_APP, db=db)
    g.close()
    cfg = ServerConfig.from_env({"EG_DB": str(db), "EG_APP_DB": str(tmp / "app.db")})
    return TestClient(create_app(cfg, serve_ui=False))


@pytest.fixture(scope="module")
def repo_id(client) -> int:
    return client.get("/api/v1/repos").json()["repos"][0]["id"]


# ---------------- meta ----------------


def test_healthz_and_version(client):
    assert client.get("/api/v1/healthz").json() == {"status": "ok"}
    assert client.get("/api/v1/version").json()["version"]


def test_me_reports_dev_mode(client):
    body = client.get("/api/v1/me").json()
    assert body["auth_disabled"] is True
    assert body["user"]["role"] == "admin"
    assert body["user"]["via"] == "dev"


# ---------------- repos ----------------


def test_list_repos(client):
    repos = client.get("/api/v1/repos").json()["repos"]
    assert len(repos) == 1
    assert repos[0]["name"] == "flask_app"
    assert repos[0]["symbols"] > 0
    assert repos[0]["source"] is None  # registered outside the UI: no RepoSource yet


def test_repo_detail_and_404(client, repo_id):
    assert client.get(f"/api/v1/repos/{repo_id}").json()["repo"]["id"] == repo_id
    assert client.get("/api/v1/repos/99999").status_code == 404
    assert client.get("/api/v1/repos/99999/stats").status_code == 404


# ---------------- reads: stats / detect / files ----------------


def test_stats(client, repo_id):
    body = client.get(f"/api/v1/repos/{repo_id}/stats").json()
    assert body["stats"]["symbols"] > 0
    assert body["stats"]["entrypoints"] > 0


def test_detect_endpoint(client, repo_id):
    body = client.get(f"/api/v1/repos/{repo_id}/detect").json()
    assert any(lang["name"] == "python" for lang in body["languages"])
    assert any(fw["name"] == "flask" for fw in body["frameworks"])
    assert all("confidence" in fw for fw in body["frameworks"])


def test_files_endpoint(client, repo_id):
    body = client.get(f"/api/v1/repos/{repo_id}/files").json()
    assert body["files"] and all(f["path"] for f in body["files"])
    py = client.get(f"/api/v1/repos/{repo_id}/files", params={"language": "python"}).json()
    assert py["files"] and all(f["language"] == "python" for f in py["files"])


# ---------------- reads: symbols / entrypoints ----------------


def test_symbols_search_and_qname_filter(client, repo_id):
    hits = client.get(f"/api/v1/repos/{repo_id}/symbols", params={"q": "report"}).json()["symbols"]
    assert hits and all("report" in s["name"].lower() for s in hits)
    qname = hits[0]["qname"]
    exact = client.get(f"/api/v1/repos/{repo_id}/symbols", params={"qname": qname}).json()[
        "symbols"
    ]
    assert exact and exact[0]["qname"] == qname


def test_entrypoints_route_filter(client, repo_id):
    eps = client.get(f"/api/v1/repos/{repo_id}/entrypoints").json()["entrypoints"]
    assert eps
    routed = [e for e in eps if e["kind"] == "http_route" and e["route"]]
    assert routed
    some_route = routed[0]["route"]
    hits = client.get(f"/api/v1/repos/{repo_id}/entrypoints", params={"route": some_route}).json()[
        "entrypoints"
    ]
    assert hits and all(e["route"] == some_route for e in hits)


# ---------------- reads: traversal ----------------


def _create_report_qname(client, repo_id) -> str:
    syms = client.get(f"/api/v1/repos/{repo_id}/symbols", params={"q": "create_report"}).json()[
        "symbols"
    ]
    assert syms
    return syms[0]["qname"]


def test_symbol_detail(client, repo_id):
    qname = _create_report_qname(client, repo_id)
    detail = client.get(f"/api/v1/repos/{repo_id}/symbol", params={"qname": qname}).json()
    assert detail["symbol"]["qname"] == qname
    assert len(detail["callees"]) >= 1


def test_callers_callees_with_depth(client, repo_id):
    qname = _create_report_qname(client, repo_id)
    d1 = client.get(f"/api/v1/repos/{repo_id}/callees", params={"qname": qname, "depth": 1}).json()[
        "symbols"
    ]
    d3 = client.get(f"/api/v1/repos/{repo_id}/callees", params={"qname": qname, "depth": 3}).json()[
        "symbols"
    ]
    assert d1
    assert len(d3) >= len(d1)  # deeper traversal is a superset
    missing = client.get(f"/api/v1/repos/{repo_id}/callers", params={"qname": "does.not.exist"})
    assert missing.status_code == 404


def test_graph_neighborhood(client, repo_id):
    qname = _create_report_qname(client, repo_id)
    g = client.get(f"/api/v1/repos/{repo_id}/graph", params={"qname": qname}).json()
    center = [n for n in g["nodes"] if n["role"] == "center"]
    assert len(center) == 1 and center[0]["qname"] == qname
    assert any(e["from"] == qname for e in g["edges"])


# ---------------- reads: paths (full flag surface) ----------------


def test_paths_default(client, repo_id):
    body = client.get(
        f"/api/v1/repos/{repo_id}/paths",
        params={"source_category": "http_input", "sink_category": "command_exec"},
    ).json()
    assert body["paths"]
    assert body["mode"] in ("precise", "widened", "strict", "explicit")
    assert isinstance(body["truncated"], bool)
    p = body["paths"][0]
    assert p["hops"][0]["qname"] and p["hops"][-1]["qname"].startswith("py:")
    assert p["severity"] in ("critical", "high", "medium", "low")
    assert len(p["edges"]) == len(p["hops"]) - 1
    assert all({"kind", "line", "confidence"} <= set(e) for e in p["edges"])
    # snippets read from the fixture checkout
    assert p["source_snippet"] or p["sink_snippet"]


def test_paths_flag_passthrough(client, repo_id):
    strict = client.get(
        f"/api/v1/repos/{repo_id}/paths",
        params={
            "source_category": "http_input",
            "sink_category": "command_exec",
            "strict": "true",
        },
    ).json()
    assert strict["mode"] == "strict"
    explicit = client.get(
        f"/api/v1/repos/{repo_id}/paths",
        params={
            "source_category": "http_input",
            "sink_category": "command_exec",
            "min_confidence": "exact",
        },
    ).json()
    assert explicit["mode"] == "explicit"
    bad = client.get(f"/api/v1/repos/{repo_id}/paths", params={"min_confidence": "very-high"})
    assert bad.status_code == 422


def test_paths_confirmed_only_is_subset(client, repo_id):
    base = {"source_category": "http_input", "sink_category": "command_exec"}
    all_paths = client.get(f"/api/v1/repos/{repo_id}/paths", params=base).json()["paths"]
    confirmed = client.get(
        f"/api/v1/repos/{repo_id}/paths", params={**base, "confirmed_only": "true"}
    ).json()["paths"]
    assert len(confirmed) <= len(all_paths)
    assert all(p["verified"] is True for p in confirmed)
