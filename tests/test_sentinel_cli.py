"""`entrygraph sentinel` operational CLI (#126)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from entrygraph.cli.main import main
from entrygraph.gate.store import GateFinding, record_scan
from entrygraph.sentinel import store

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"


def _seed(db_url: str) -> None:
    sf = store.init_store(store.make_store_engine(db_url))
    with sf() as s:
        store.upsert_installation(s, 42, "acme", now=_NOW)
        store.upsert_installation(s, 77, "globex", now=_NOW)
        store.set_suspended(s, 77, True)
        repo_id = store.resolve_repo(s, 42, "acme/app", now=_NOW)
        for i in range(4):
            record_scan(
                s,
                repo_id,
                status="passed",
                findings=[
                    (
                        GateFinding(
                            strict=f"fp{i}",
                            endpoint="e",
                            source_category=None,
                            sink_id=None,
                            sink_category=None,
                            risk=0.5,
                        ),
                        "known",
                    )
                ],
                head_sha=f"sha{i}",
                now=_NOW,
            )


# ---------------- config ----------------


def test_config_prints_redacted(capsys, monkeypatch):
    monkeypatch.setenv("SENTINEL_GITHUB_APP_ID", "1")
    monkeypatch.setenv("SENTINEL_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("SENTINEL_GITHUB_PRIVATE_KEY", "-----BEGIN KEY-----\nx\n-----END KEY-----")
    assert main(["sentinel", "config"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["app_id"] == "1"
    assert out["webhook_secret"] == "<set>"
    assert "s3cret" not in json.dumps(out)


def test_config_missing_env_errors(capsys, monkeypatch):
    monkeypatch.delenv("SENTINEL_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("SENTINEL_WEBHOOK_SECRET", raising=False)
    assert main(["sentinel", "config"]) == 2
    assert "config error" in capsys.readouterr().out


# ---------------- serve / worker (extra absent in CI) ----------------


def test_serve_without_extra_reports_missing(capsys):
    # uvicorn is runtime-only (not in the dev extra), so serve fails cleanly
    assert main(["sentinel", "serve"]) == 2
    assert "sentinel" in capsys.readouterr().out.lower()


def test_worker_without_extra_reports_missing(capsys):
    # arq is runtime-only
    assert main(["sentinel", "worker"]) == 2
    assert "sentinel" in capsys.readouterr().out.lower()


# ---------------- installations ----------------


def test_installations_json(tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 's.db'}"
    _seed(url)
    assert main(["sentinel", "installations", "--database-url", url, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    by_id = {r["id"]: r for r in rows}
    assert by_id[42]["account_login"] == "acme" and by_id[42]["repos"] == 1
    assert by_id[77]["suspended"] is True


def test_installations_human(tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 's.db'}"
    _seed(url)
    assert main(["sentinel", "installations", "--database-url", url]) == 0
    out = capsys.readouterr().out
    assert "acme" in out
    assert "[suspended]" in out  # globex


# ---------------- purge ----------------


def test_purge_keeps_newest(tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 's.db'}"
    _seed(url)  # repo has 4 scans
    assert main(["sentinel", "purge", "--database-url", url, "--keep", "1"]) == 0
    assert "purged 3" in capsys.readouterr().out
    sf = store.init_store(store.make_store_engine(url))
    with sf() as s:
        repo_id = store.resolve_repo(s, 42, "acme/app", now=_NOW)
        assert len(store.list_scans(s, repo_id)) == 1


def test_purge_scoped_to_installation(tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 's.db'}"
    _seed(url)
    # installation 77 has a repo with no scans -> nothing to purge there
    assert (
        main(["sentinel", "purge", "--database-url", url, "--keep", "10", "--installation", "77"])
        == 0
    )
    assert "purged 0" in capsys.readouterr().out


# ---------------- baseline ----------------


def test_baseline_from_local_checkout(tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 's.db'}"
    assert (
        main(
            [
                "sentinel",
                "baseline",
                str(FLASK_APP),
                "--database-url",
                url,
                "--installation",
                "5",
                "--repo",
                "acme/app",
                "--branch",
                "main",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "cut baseline for acme/app" in out
    # the baseline is persisted and non-empty (flask_app has reachable paths)
    from entrygraph.gate.store import load_baseline

    sf = store.init_store(store.make_store_engine(url))
    with sf() as s:
        repo_id = store.resolve_repo(s, 5, "acme/app", now=_NOW)
        view = load_baseline(s, repo_id, "main")
        assert view is not None and len(view.strict) > 0
