"""Jobs subsystem: enqueue via the API, runner lifecycle, cancellation,
orphan recovery, and source validation."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from entrygraph.server.app import create_app
from entrygraph.server.config import ServerConfig
from entrygraph.server.models import Job, utcnow

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


@pytest.fixture()
def client(tmp_path) -> TestClient:
    cfg = ServerConfig.from_env(
        {
            "EG_DB": str(tmp_path / "graph.db"),
            "EG_APP_DB": str(tmp_path / "app.db"),
            "EG_CLONE_DIR": str(tmp_path / "clones"),
        }
    )
    # TestClient runs the lifespan, so the JobRunner is live inside the block
    with TestClient(create_app(cfg, serve_ui=False)) as c:
        yield c


def _wait_for_job(client: TestClient, job_id: str, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()["job"]
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish: {job}")


# ---------------- happy path ----------------


def test_register_local_path_indexes_repo(client):
    resp = client.post("/api/v1/repos", json={"source": str(FLASK_APP)})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    job = _wait_for_job(client, job_id)
    assert job["status"] == "succeeded", job["error"]
    assert job["progress"] == 1.0
    assert job["stats"]["symbols"] > 0
    assert job["repo_root"] == str(FLASK_APP.resolve())
    assert job["repo_id"] is not None

    # the repo is now queryable through the read API, with its source recorded
    repos = client.get("/api/v1/repos").json()["repos"]
    assert len(repos) == 1
    assert repos[0]["source"] is not None
    assert repos[0]["source"]["url"] is None  # local path, not a clone

    # jobs list shows it
    listed = client.get("/api/v1/jobs").json()["jobs"]
    assert listed and listed[0]["id"] == job_id


def test_reindex_is_incremental_and_full_works(client):
    job_id = client.post("/api/v1/repos", json={"source": str(FLASK_APP)}).json()["job_id"]
    _wait_for_job(client, job_id)
    repo_id = client.get("/api/v1/repos").json()["repos"][0]["id"]

    incr = client.post(f"/api/v1/repos/{repo_id}/index", json={})
    assert incr.status_code == 202
    job = _wait_for_job(client, incr.json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["params"]["incremental"] is True
    assert job["stats"]["files_indexed"] == 0  # nothing changed

    full = client.post(f"/api/v1/repos/{repo_id}/index", json={"full": True})
    job = _wait_for_job(client, full.json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["params"]["incremental"] is False
    assert job["stats"]["files_indexed"] > 0


def test_delete_repo_removes_graph_and_source(client):
    job_id = client.post("/api/v1/repos", json={"source": str(FLASK_APP)}).json()["job_id"]
    _wait_for_job(client, job_id)
    repo_id = client.get("/api/v1/repos").json()["repos"][0]["id"]

    assert client.delete(f"/api/v1/repos/{repo_id}").status_code == 200
    assert client.get("/api/v1/repos").json()["repos"] == []
    assert client.delete(f"/api/v1/repos/{repo_id}").status_code == 404


# ---------------- validation ----------------


@pytest.mark.parametrize(
    "source",
    [
        "file:///etc/passwd",
        "git://example.com/repo.git",
        "http://example.com/repo.git",  # only https/ssh allowed
        "relative/path",
        "/nonexistent/absolutely/nowhere",
    ],
)
def test_register_rejects_bad_sources(client, source):
    assert client.post("/api/v1/repos", json={"source": source}).status_code == 422


def test_register_rejects_disallowed_hosts(tmp_path):
    cfg = ServerConfig.from_env(
        {
            "EG_DB": str(tmp_path / "g.db"),
            "EG_APP_DB": str(tmp_path / "a.db"),
            "EG_ALLOWED_GIT_HOSTS": "github.com",
        }
    )
    with TestClient(create_app(cfg, serve_ui=False)) as c:
        bad = c.post("/api/v1/repos", json={"source": "https://evil.example/x.git"})
        assert bad.status_code == 422
        scp = c.post("/api/v1/repos", json={"source": "git@evil.example:x/y.git"})
        assert scp.status_code == 422


def test_register_rejects_local_paths_when_disabled(tmp_path):
    cfg = ServerConfig.from_env(
        {
            "EG_DB": str(tmp_path / "g.db"),
            "EG_APP_DB": str(tmp_path / "a.db"),
            "EG_ALLOW_LOCAL_PATHS": "false",
        }
    )
    with TestClient(create_app(cfg, serve_ui=False)) as c:
        resp = c.post("/api/v1/repos", json={"source": str(FLASK_APP)})
        assert resp.status_code == 422


def test_depth_capped(client):
    resp = client.post(
        "/api/v1/repos", json={"source": "https://github.com/org/x.git", "depth": 999}
    )
    assert resp.status_code == 422


# ---------------- cancellation + recovery ----------------


def test_cancel_queued_job(client):
    # enqueue directly (bypassing the runner's nudge) so it stays queued
    factory = client.app.state.app_session_factory
    from entrygraph.server.jobs.runner import enqueue_job

    # stop the runner from picking it up instantly by cancelling before nudge
    job_id = enqueue_job(
        factory, job_type="index", params={"source": "/tmp/never"}, created_by="test"
    )
    resp = client.post(f"/api/v1/jobs/{job_id}/cancel")
    # either we won the race (cancelled) or the runner claimed it first
    assert resp.status_code in (200, 409)
    if resp.status_code == 200:
        assert resp.json()["status"] == "cancelled"


def test_cancel_finished_job_conflicts(client):
    job_id = client.post("/api/v1/repos", json={"source": str(FLASK_APP)}).json()["job_id"]
    _wait_for_job(client, job_id)
    assert client.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 409


def test_orphan_recovery_marks_stale_running_jobs_failed(tmp_path):
    cfg = ServerConfig.from_env(
        {"EG_DB": str(tmp_path / "g.db"), "EG_APP_DB": str(tmp_path / "a.db")}
    )
    from entrygraph.server.appdb import (
        ensure_app_schema,
        make_app_engine,
        make_app_session_factory,
    )
    from entrygraph.server.jobs.runner import JobRunner

    engine = make_app_engine(cfg.app_db_url)
    ensure_app_schema(engine)
    factory = make_app_session_factory(engine)
    # simulate a job left running by a dead process
    with factory() as session:
        session.add(
            Job(
                id="deadbeef",
                type="index",
                status="running",
                params_json="{}",
                worker_token="stale-boot",
                created_at=utcnow(),
            )
        )
        session.commit()

    runner = JobRunner(cfg, factory)
    assert runner.recover_orphans() == 1
    with factory() as session:
        job = session.get(Job, "deadbeef")
        assert job.status == "failed"
        assert "orphaned" in (job.error or "")


def test_unknown_job_404(client):
    assert client.get("/api/v1/jobs/nope").status_code == 404
    assert client.post("/api/v1/jobs/nope/cancel").status_code == 404


# ---------------- stale-repo auto-heal sweep ----------------


def test_heal_sweep_enqueues_stale_repos_and_dedups(tmp_path):
    from sqlalchemy import select, update

    from entrygraph.db.engine import make_engine
    from entrygraph.db.models import Repository
    from entrygraph.pipeline.scanner import index_repository
    from entrygraph.server.appdb import (
        ensure_app_schema,
        make_app_engine,
        make_app_session_factory,
    )
    from entrygraph.server.jobs.heal import sweep_stale_repos

    graph_db = tmp_path / "graph.db"
    graph_engine = make_engine(graph_db)
    index_repository(FLASK_APP, graph_engine)
    graph_engine.dispose()

    app_url = f"sqlite:///{tmp_path / 'app.db'}"
    app_engine = make_app_engine(app_url)
    ensure_app_schema(app_engine)
    app_sf = make_app_session_factory(app_engine)
    cfg = ServerConfig(db_path=str(graph_db), app_db_url=app_url, auth_mode="none")

    # current repo: nothing to heal
    assert sweep_stale_repos(cfg, app_sf) == 0

    # simulate an analyzer bump landing on the existing repo
    eng = make_engine(graph_db)
    with eng.begin() as conn:
        conn.execute(update(Repository).values(analyzer_version=0))
    eng.dispose()

    assert sweep_stale_repos(cfg, app_sf) == 1  # enqueues one index job
    assert sweep_stale_repos(cfg, app_sf) == 0  # dedup: job already queued

    with app_sf() as s:
        jobs = list(s.execute(select(Job)).scalars())
    assert len(jobs) == 1 and jobs[0].type == "index" and jobs[0].status == "queued"
