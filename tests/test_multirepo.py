"""Global multi-repo database: two repositories in one DB stay isolated (#116)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from entrygraph.api import CodeGraph
from entrygraph.db import models
from entrygraph.db.engine import make_engine
from entrygraph.db.meta import create_schema
from entrygraph.errors import RepositoryNotIndexedError
from entrygraph.pipeline.scanner import index_repository

FLASK_APP = Path(__file__).parent / "fixtures" / "python" / "flask_app"
CLI_APP = Path(__file__).parent / "fixtures" / "python" / "cli_app"


@pytest.fixture
def two_repos(tmp_path):
    """One shared DB holding two distinct repositories."""
    engine = make_engine(tmp_path / "global.db")
    create_schema(engine)
    index_repository(FLASK_APP, engine)
    index_repository(CLI_APP, engine)
    yield engine
    engine.dispose()


def _graph(engine, root):
    """Bind a CodeGraph to one repo of a multi-repo DB by its root path."""
    from entrygraph.api import _lookup_repo_id

    return CodeGraph(engine, _lookup_repo_id(engine, Path(root).resolve()))


def test_two_repositories_coexist(two_repos):
    with Session(two_repos) as s:
        roots = set(s.execute(select(models.Repository.root_path)).scalars())
        ids = list(s.execute(select(models.Repository.id)).scalars())
    assert roots == {str(FLASK_APP.resolve()), str(CLI_APP.resolve())}
    assert len(ids) == len(set(ids)) == 2  # distinct, non-colliding repo ids


def test_symbols_scoped_per_repo(two_repos):
    flask = _graph(two_repos, FLASK_APP)
    cli = _graph(two_repos, CLI_APP)
    flask_qnames = {sym.qname for sym in flask.symbols(limit=10_000)}
    cli_qnames = {sym.qname for sym in cli.symbols(limit=10_000)}
    # flask's route module is not visible from the cli repo and vice versa
    assert any("app.routes" in q for q in flask_qnames)
    assert not any("app.routes" in q for q in cli_qnames)
    # each repo's symbol set is non-empty and they don't bleed together
    assert flask_qnames and cli_qnames
    assert flask.stats().symbols != 0 and cli.stats().symbols != 0


def test_stats_counts_are_per_repo(two_repos):
    flask = _graph(two_repos, FLASK_APP)
    cli = _graph(two_repos, CLI_APP)
    with Session(two_repos) as s:
        total = s.execute(select(func.count(models.Symbol.id))).scalar()
    # each repo counts only its own symbols; together they make up the global total
    assert flask.stats().symbols + cli.stats().symbols == total
    assert flask.stats().symbols < total  # neither repo sees the whole DB


def test_entrypoints_scoped_per_repo(two_repos):
    flask = _graph(two_repos, FLASK_APP)
    cli = _graph(two_repos, CLI_APP)
    flask_routes = {e.route for e in flask.entrypoints()}
    assert "/reports" in flask_routes
    assert "/reports" not in {e.route for e in cli.entrypoints()}


def test_paths_do_not_cross_repos(two_repos):
    # the flask repo has a route -> subprocess.run path; querying it from the cli
    # repo (which also has a command_exec sink) must not surface flask's path
    flask = _graph(two_repos, FLASK_APP)
    cli = _graph(two_repos, CLI_APP)
    assert flask.paths(source="app.routes.create_report", sink="py:subprocess.run")
    assert not cli.paths(source="app.routes.create_report", sink="py:subprocess.run")


def test_reindex_one_repo_leaves_the_other_intact(two_repos):
    cli = _graph(two_repos, CLI_APP)
    before = cli.stats().symbols
    # full re-index of flask must not touch cli's rows
    index_repository(FLASK_APP, two_repos, incremental=False)
    cli_after = _graph(two_repos, CLI_APP)
    assert cli_after.stats().symbols == before
    assert cli_after.symbols(limit=5)  # still queryable


def test_open_requires_repo_selection_when_ambiguous(two_repos, tmp_path):
    # a multi-repo DB with no root given is ambiguous
    from entrygraph.api import _lookup_repo_id

    with pytest.raises(RepositoryNotIndexedError):
        _lookup_repo_id(two_repos)
    # an unknown root is an error too
    with pytest.raises(RepositoryNotIndexedError):
        _lookup_repo_id(two_repos, tmp_path / "not-indexed")


def test_list_repos_enumerates_the_database(tmp_path):
    db = tmp_path / "global.db"
    engine = make_engine(db)
    create_schema(engine)
    index_repository(FLASK_APP, engine)
    index_repository(CLI_APP, engine)
    engine.dispose()

    repos = CodeGraph.list_repos(db)
    assert {r.name for r in repos} == {"flask_app", "cli_app"}
    assert {r.root for r in repos} == {str(FLASK_APP.resolve()), str(CLI_APP.resolve())}
    flask = next(r for r in repos if r.name == "flask_app")
    assert flask.symbols > 0 and flask.files > 0
    # ordered by root path, stable
    assert [r.root for r in repos] == sorted(r.root for r in repos)
