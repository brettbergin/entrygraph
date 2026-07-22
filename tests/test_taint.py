from __future__ import annotations

from entrygraph.detect.taint import (
    SinkPattern,
    SinkRegistry,
    SourcePattern,
    _load_toml,
    builtin_registry,
)


def test_load_toml_parses_sinks_and_sources():
    text = """
[[sink]]
id = "x.cmd"
category = "command_exec"
severity = "high"
callee = "py:os.system"
library = "stdlib"

[[source]]
id = "x.src"
category = "http_input"
callee = "py:flask.request*"
"""
    sinks, sources, disable = _load_toml(text)
    assert sinks[0].library == "stdlib"
    assert sources[0].id == "x.src"
    assert disable == []


def test_prefix_bucketing_preserves_match_semantics():
    reg = SinkRegistry(
        sinks=[
            SinkPattern(id="py.a", category="sql", callee="py:*.execute"),
            SinkPattern(id="py.b", category="sql", callee="py:cursor.execute"),  # shadowed
            SinkPattern(id="js.a", category="cmd", callee="js:*.exec"),
            SinkPattern(id="any", category="eval", callee="*.dangerous"),  # prefix-less
        ],
        sources=[],
    )
    # first-match-wins within a language (py.a before py.b)
    assert reg.match("py:cursor.execute") == "py.a"
    # cross-language isolation: a js callee never returns a py sink and vice versa
    assert reg.match("js:child_process.exec") == "js.a"
    assert reg.match("py:child_process.exec") is None
    # a prefix-less (catch-all) pattern still matches any language
    assert reg.match("py:foo.dangerous") == "any"
    assert reg.match("js:foo.dangerous") == "any"
    # no match -> None
    assert reg.match("go:fmt.Println") is None


def test_match_source_and_source_category():
    reg = SinkRegistry(
        sinks=[],
        sources=[
            SourcePattern(id="env", category="env_input", callee="py:{os.getenv,os.environ.get}"),
            SourcePattern(id="http", category="http_input", callee="py:flask.request*"),
        ],
    )
    assert reg.match_source("py:os.getenv") == "env"
    assert reg.match_source("py:flask.request.args.get") == "http"
    assert reg.match_source("py:os.system") is None
    assert reg.source_ids_for_category("env_input") == {"env"}


def test_builtin_registry_matches_env_source():
    reg = builtin_registry()
    assert reg.match_source("py:os.getenv") == "py.env"


def test_builtin_registry_loads_library_summaries():
    reg = builtin_registry()
    # library summary: paramiko exec_command mapped to command_exec
    assert reg.match("py:client.exec_command") == "lib.py.paramiko.exec"
    assert reg.sinks["lib.py.paramiko.exec"].library == "paramiko"
    # execa (bare call) isn't caught by any generic pattern -> library summary wins
    assert reg.match("js:execa") == "lib.js.execa"
    assert reg.sinks["lib.js.execa"].library == "execa"


def test_merged_with_honors_disable():
    base = builtin_registry()
    extra = SinkPattern(id="extra", category="sql", callee="py:mydb.raw_exec")
    merged = base.merged_with([extra], [], disable=["py.command-exec.subprocess"])
    assert "extra" in merged.sinks
    assert "py.command-exec.subprocess" not in merged.sinks


def test_ruby_service_execute_not_tagged_sql(tmp_engine, fixture_repo):
    # End-to-end #91: Service.new(params).execute must not be a sql sink edge;
    # interpolated connection.execute and find_by_sql must be.
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from entrygraph.db.models import Edge
    from entrygraph.pipeline.scanner import index_repository

    repo = fixture_repo("ruby/rails_sql")
    index_repository(repo, tmp_engine)
    with Session(tmp_engine) as s:
        rows = s.execute(
            select(Edge.dst_qname, Edge.sink_id, Edge.arg_preview).where(Edge.sink_id.is_not(None))
        ).all()
        tagged = {(r.dst_qname, r.sink_id) for r in rows}
        assert ("rb:*.find_by_sql", "rb.sql-query") in tagged
        sql_execute_previews = [r.arg_preview for r in rows if r.sink_id == "rb.sql-execute"]
        # only the interpolated raw-SQL execute is tagged; the service objects are not
        assert len(sql_execute_previews) == 1
        assert "SELECT" in sql_execute_previews[0]


def test_catalog_coverage_counts_and_tiers():
    from entrygraph.detect.taint import builtin_registry, catalog_coverage

    cov = catalog_coverage(builtin_registry())
    # typescript/tsx ride the js: prefix and get their own entries
    assert cov["typescript"] == cov["javascript"]
    assert cov["tsx"] == cov["javascript"]
    # spot counts: every shipped language has sinks and at least one source
    for lang in ("python", "javascript", "go", "java", "ruby", "csharp", "php", "rust"):
        assert cov[lang].sinks > 0, lang
        assert cov[lang].sources > 0, lang
        assert cov[lang].tier in ("full", "partial", "minimal")
        assert "sql" in cov[lang].sink_categories or cov[lang].sink_categories
    # tier anchors (stable expectations; a shift means coverage actually moved)
    assert cov["python"].tier == "full"
    # javascript moved partial -> full when #86 added its cli_arg source pattern
    assert cov["javascript"].tier == "full"
    # go and rust lifted to full coverage in #135
    assert cov["go"].tier == "full"
    assert cov["rust"].tier == "full"


def test_every_extractable_language_has_catalog_entries():
    # a newly-added language can't silently ship with zero taint patterns —
    # unless it is declaration-only (GraphQL SDL) and can't have call sites.
    from entrygraph.detect.taint import (
        DECLARATION_ONLY_LANGUAGES,
        LANGUAGE_PREFIX,
        builtin_registry,
        catalog_coverage,
    )
    from entrygraph.fs.lang import EXTRACTABLE

    cov = catalog_coverage(builtin_registry())
    for language in EXTRACTABLE - DECLARATION_ONLY_LANGUAGES:
        assert language in LANGUAGE_PREFIX, f"{language} has no callee prefix mapping"
        assert cov.get(language) and cov[language].sinks > 0, (
            f"{language} has no sink catalog entries"
        )
