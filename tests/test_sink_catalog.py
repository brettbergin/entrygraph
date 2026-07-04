"""Structural guards over the shipped sink/source/sanitizer catalogs."""

from __future__ import annotations

import re

import pytest

from entrygraph.detect.taint import builtin_registry, expand_braces


@pytest.fixture(scope="module")
def registry():
    return builtin_registry()


def test_no_duplicate_sink_ids(registry):
    # SinkRegistry stores by id; a collision would silently drop an entry.
    ids = list(registry.sinks) + list(registry.sources) + list(registry.sanitizers)
    assert len(ids) == len(set(ids))


def test_every_pattern_compiles(registry):
    import fnmatch

    for sink in registry.sinks.values():
        for glob in expand_braces(sink.callee):
            re.compile(fnmatch.translate(glob))  # must not raise
    for san in registry.sanitizers.values():
        for glob in expand_braces(san.callee):
            re.compile(fnmatch.translate(glob))


def test_arg_hints_are_valid_regex(registry):
    for sink in registry.sinks.values():
        if sink.require_arg_hint:
            re.compile(sink.require_arg_hint)


def test_spot_check_new_sinks(registry):
    # a representative new entry per language resolves to the right category
    assert registry.match("py:hashlib.md5") == "py.weak-crypto"
    assert registry.match("js:vm.runInNewContext") == "js.code-eval.vm"
    assert registry.match("go:net/http.NewRequest") == "go.ssrf"
    assert registry.match("java:ctx.lookup", "(name)") == "java.jndi"
    assert registry.match("rb:Open3.capture3", "(user_cmd)") == "rb.command-exec.open3"


def test_categories_are_queryable(registry):
    for category in ("ssrf", "xxe", "weak_crypto", "path_traversal", "jndi"):
        assert registry.ids_for_category(category), f"no sinks for {category}"


def test_regexp_exec_is_not_a_command_sink(registry):
    # `js:*.exec` collided with RegExp.prototype.exec. Real child_process exec
    # resolves to js:child_process.exec (imported/aliased) and stays tagged; the
    # bare unknown-receiver `.exec` no longer matches command_exec.
    assert registry.match("js:child_process.exec", "('ls ' + x)") == "js.command-exec.child_process"
    assert registry.match("js:*.exec", "(input)") is None
    # sibling child-process methods with no built-in collision still match
    assert registry.match("js:*.spawn", "(cmd)") == "js.command-exec.member"
    assert registry.match("js:*.execSync", "(cmd)") == "js.command-exec.member"


def test_receiver_agnostic_sql_requires_dynamic_arg(registry):
    # `*.Query`/`*.query` collided with url.Query()/gin c.Query()/DOM .query.
    # Only a concatenated or interpolated argument (the injection signal) tags.
    assert registry.match("go:*.Exec", '("ALTER DATABASE COLLATE " + c)') == "go.sql-query"
    assert registry.match("go:*.Query", "()") is None  # url.Query()
    assert registry.match("go:*.Query", '("offset")') is None  # gin c.Query("offset")
    assert (
        registry.match("go:*.Exec", '(ctx, "UPDATE t SET k = ? WHERE id = ?")') is None
    )  # param'd
    assert registry.match("js:*.query", "('SELECT * FROM t WHERE id = ' + id)") == "js.sql-query"
    assert registry.match("js:*.query", "({ where: { id } })") is None  # ORM/tRPC object arg


def test_ruby_bare_execute_requires_sql_shaped_arg(registry):
    # `rb:*.execute` collided with the Rails service-object convention
    # (Service.new(params).execute) — 13,821 GitLab edges, ~99% not SQL (#91).
    # Only a SQL-keyword or interpolated argument tags now.
    assert registry.match("rb:*.execute", "(params)") is None
    assert registry.match("rb:*.execute", "(declared_params(include_missing: false))") is None
    assert registry.match("rb:*.execute", "()") is None
    assert registry.match("rb:*.execute", None) is None
    assert (
        registry.match("rb:*.execute", '("SELECT * FROM t WHERE id = #{id}")') == "rb.sql-execute"
    )
    assert registry.match("rb:*.execute", '("select 1")') == "rb.sql-execute"  # (?i)
    assert registry.match("rb:*.execute", '("TRUNCATE audit_events")') == "rb.sql-execute"
    assert registry.match("rb:*.execute", "(sql_with_#{interp})") == "rb.sql-execute"
    # unambiguous ActiveRecord methods stay unguarded
    assert registry.match("rb:*.find_by_sql", "(anything)") == "rb.sql-query"
    assert registry.match("rb:*.exec_query", "()") == "rb.sql-query"
    assert registry.match("rb:*.select_all", None) == "rb.sql-query"
