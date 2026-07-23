"""/entrypoints/{id}/flows: parameters + per-parameter data-flow paths."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from entrygraph.api import CodeGraph
from entrygraph.server.app import create_app
from entrygraph.server.config import ServerConfig

RAILS_APP = Path(__file__).parent / "fixtures" / "ruby" / "rails_app"


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    tmp = tmp_path_factory.mktemp("server-flows")
    db = tmp / "graph.db"
    g = CodeGraph.index(RAILS_APP, db=db)
    g.close()
    cfg = ServerConfig.from_env({"EG_DB": str(db), "EG_APP_DB": str(tmp / "app.db")})
    return TestClient(create_app(cfg, serve_ui=False))


@pytest.fixture(scope="module")
def repo_id(client) -> int:
    return client.get("/api/v1/repos").json()["repos"][0]["id"]


@pytest.fixture(scope="module")
def entrypoints(client, repo_id) -> dict[tuple[str, str], dict]:
    eps = client.get(f"/api/v1/repos/{repo_id}/entrypoints").json()["entrypoints"]
    return {(e["http_method"], e["route"]): e for e in eps}


def test_entrypoints_carry_parameters_and_extra(entrypoints):
    show = entrypoints[("GET", "/posts/:id")]
    assert show["parameters"] == [
        {
            "name": "id",
            "location": "path",
            "required": True,
            "type": None,
            "provenance": "route",
            "line": show["parameters"][0]["line"],
        }
    ]
    assert show["extra"]["controller"] == "posts"
    assert show["extra"]["action"] == "show"
    assert show["handler"]["qname"].endswith("PostsController.show")

    create = entrypoints[("POST", "/posts")]
    assert {(p["name"], p["provenance"]) for p in create["parameters"]} == {
        ("title", "strong_params"),
        ("body", "strong_params"),
    }


def test_flows_groups_paths_by_parameter(client, repo_id, entrypoints):
    ep_id = entrypoints[("GET", "/posts/:id")]["id"]
    body = client.get(f"/api/v1/repos/{repo_id}/entrypoints/{ep_id}/flows").json()

    assert body["entrypoint"]["id"] == ep_id
    by_param = {p["parameter"]["name"]: p for p in body["parameters"]}
    id_paths = by_param["id"]["paths"]
    assert id_paths, "params[:id] -> system flow should attribute to the id parameter"
    assert any(p["verified"] is True for p in id_paths)
    assert all(p["source_key"] == "id" for p in id_paths)
    assert id_paths[0]["hops"][0]["qname"].endswith("PostsController.show")


def test_flows_entrypoint_without_sinks_is_empty_not_error(client, repo_id, entrypoints):
    ep_id = entrypoints[("GET", "/")]["id"]  # pages#home reaches no sink
    body = client.get(f"/api/v1/repos/{repo_id}/entrypoints/{ep_id}/flows").json()
    assert body["unmatched_paths"] == []
    assert all(p["paths"] == [] for p in body["parameters"])


def test_flows_unknown_entrypoint_404(client, repo_id):
    assert client.get(f"/api/v1/repos/{repo_id}/entrypoints/999999/flows").status_code == 404
