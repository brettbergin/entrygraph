"""Sentinel store: installation scoping + uninstall hard-delete (#126 M2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from entrygraph.db import models
from entrygraph.gate.store import GateFinding, save_baseline
from entrygraph.sentinel import store
from entrygraph.sentinel.models import Installation, InstallationRepo

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path):
    engine = store.make_store_engine(f"sqlite:///{tmp_path / 'sentinel.db'}")
    return store.init_store(engine)


def test_upsert_and_get_installation(session_factory):
    with session_factory() as s:
        inst = store.upsert_installation(s, 100, "octo", now=_NOW)
        assert inst.id == 100 and inst.account_login == "octo" and inst.suspended is False
    with session_factory() as s:
        # idempotent; refreshes login and clears suspension
        store.set_suspended(s, 100, True)
        again = store.upsert_installation(s, 100, "octo-renamed", now=_NOW)
        assert again.account_login == "octo-renamed"
        assert again.suspended is False
        assert store.get_installation(s, 100).id == 100


def test_suspend_installation(session_factory):
    with session_factory() as s:
        store.upsert_installation(s, 1, "a", now=_NOW)
        store.set_suspended(s, 1, True)
        assert store.get_installation(s, 1).suspended is True


def test_resolve_repo_is_stable_and_scoped(session_factory):
    with session_factory() as s:
        store.upsert_installation(s, 1, "a", now=_NOW)
        store.upsert_installation(s, 2, "b", now=_NOW)
        r1 = store.resolve_repo(s, 1, "a/repo", now=_NOW)
        r1_again = store.resolve_repo(s, 1, "a/repo", now=_NOW)
        r_other_repo = store.resolve_repo(s, 1, "a/other", now=_NOW)
        r_other_inst = store.resolve_repo(s, 2, "a/repo", now=_NOW)
    assert r1 == r1_again  # same (installation, repo) -> stable id
    assert r1 != r_other_repo  # different repo -> different id
    assert r1 != r_other_inst  # same name, different installation -> different id


def test_installation_repo_ids(session_factory):
    with session_factory() as s:
        store.upsert_installation(s, 1, "a", now=_NOW)
        a = store.resolve_repo(s, 1, "a/one", now=_NOW)
        b = store.resolve_repo(s, 1, "a/two", now=_NOW)
        assert set(store.installation_repo_ids(s, 1)) == {a, b}


def test_uninstall_hard_deletes_all_data(session_factory):
    with session_factory() as s:
        store.upsert_installation(s, 7, "acct", now=_NOW)
        repo_id = store.resolve_repo(s, 7, "acct/repo", now=_NOW)
        # give the repo a baseline (findings/baseline hang off repositories.id)
        save_baseline(
            s,
            repo_id,
            [
                GateFinding(
                    strict="fp1",
                    endpoint="ep1",
                    source_category="http_input",
                    sink_id="py.command-exec.os",
                    sink_category="command_exec",
                    risk=0.9,
                )
            ],
            branch="main",
            commit_sha="abc",
            now=_NOW,
        )
    with session_factory() as s:
        assert s.execute(select(func.count()).select_from(models.Baseline)).scalar() == 1

    with session_factory() as s:
        store.delete_installation(s, 7)
    with session_factory() as s:
        # every trace is gone: repository, baseline (cascade), mapping, installation
        assert s.execute(select(func.count()).select_from(models.Repository)).scalar() == 0
        assert s.execute(select(func.count()).select_from(models.Baseline)).scalar() == 0
        assert s.execute(select(func.count()).select_from(models.BaselinePath)).scalar() == 0
        assert s.execute(select(func.count()).select_from(InstallationRepo)).scalar() == 0
        assert s.execute(select(func.count()).select_from(Installation)).scalar() == 0
