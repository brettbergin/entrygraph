"""Negative-precision harness: constructs that must NOT be tagged (#97).

The rest of the suite asserts recall ("does it find X?"); this file asserts the
other half — a catalog pattern silently broadening now trips CI. Each fixture
under ``tests/fixtures/precision/<lang>/`` pairs must-not-tag constructs with a
tagged positive control per category, so a pattern can neither over-match nor
silently die.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.db.models import Edge
from entrygraph.pipeline.scanner import index_repository

PRECISION = Path(__file__).parent / "fixtures" / "precision"


def _tagged(engine) -> list[tuple[str, str, str]]:
    """(dst_qname, sink_id, arg_preview) for every sink-tagged edge."""
    with Session(engine) as s:
        return [
            (r.dst_qname, r.sink_id, r.arg_preview or "")
            for r in s.execute(
                select(Edge.dst_qname, Edge.sink_id, Edge.arg_preview).where(
                    Edge.sink_id.is_not(None)
                )
            )
        ]


def _index(engine, lang: str):
    stats = index_repository(PRECISION / lang, engine)
    assert stats.files_indexed >= 1
    return _tagged(engine)


def test_ruby_precision(tmp_engine):
    tagged = _index(tmp_engine, "ruby")
    ids = {sink_id for _, sink_id, _ in tagged}
    previews = {p for _, sink_id, p in tagged if sink_id == "rb.sql-execute"}
    # service-object .execute and parameterized .where are not sql
    assert not any("declared_params" in p for p in previews)
    assert not any("params[:id]" in p and "SELECT" not in p for p in previews)
    # SHA256 is not weak crypto (no ruby weak-crypto pattern should fire)
    assert not any("weak" in (sink_id or "") for _, sink_id, _ in tagged)
    # constant-argv / constant-string commands are not command_exec
    cmd_previews = {p for _, sink_id, p in tagged if sink_id.startswith("rb.command-exec")}
    assert '(["ls", "-l"])' not in cmd_previews
    assert '("ls -l")' not in cmd_previews
    # positive controls stay alive
    assert "rb.sql-execute" in ids
    assert any(sink_id.startswith("rb.command-exec") for _, sink_id, _ in tagged)
    assert any("#{params[:dir]}" in p for _, sink_id, p in tagged if "command-exec" in sink_id)
    assert any("user_cmd" in p for _, sink_id, p in tagged if "command-exec" in sink_id)


def test_python_precision(tmp_engine):
    tagged = _index(tmp_engine, "python")
    by_sink: dict[str, list[str]] = {}
    for dst, sink_id, _p in tagged:
        by_sink.setdefault(sink_id, []).append(dst)
    # sha256 is not weak crypto; md5 control stays tagged
    assert "py:hashlib.sha256" not in by_sink.get("py.weak-crypto", [])
    assert "py:hashlib.md5" in by_sink.get("py.weak-crypto", [])


def test_javascript_precision(tmp_engine):
    tagged = _index(tmp_engine, "javascript")
    weak = [(d, p) for d, sink_id, p in tagged if sink_id == "js.weak-crypto"]
    # sha256 not tagged; md5 control tagged
    assert not any("sha256" in p for _, p in weak)
    assert any("md5" in p for _, p in weak)
    sql = [p for _, sink_id, p in tagged if sink_id == "js.sql-query"]
    # parameterized query untagged; concatenated control tagged
    assert not any("?" in p for p in sql)
    assert any("+ id" in p for p in sql)
    # RegExp.exec is not command_exec
    assert not any(sink_id.startswith("js.command-exec") for _, sink_id, _ in tagged)


def test_java_precision(tmp_engine):
    tagged = _index(tmp_engine, "java")
    by_sink: dict[str, list[str]] = {}
    for _, sink_id, p in tagged:
        by_sink.setdefault(sink_id, []).append(p)
    # Executor.execute(runnable) is not sql; concatenated stmt.execute is
    assert not any("task" in p for p in by_sink.get("java.sql-execute", []))
    assert any("DELETE FROM" in p for p in by_sink.get("java.sql-execute", []))
    # no-arg lookup() is not jndi; ctx.lookup(id) is
    assert all(p.strip("()") for p in by_sink.get("java.jndi", []))
    assert by_sink.get("java.jndi")
    # no-arg evaluate() is not template_injection; parseExpression control is
    assert not any(p in ("()", "") for p in by_sink.get("java.expression-injection.evaluate", []))
    assert by_sink.get("java.expression-injection")


def test_php_precision(tmp_engine):
    tagged = _index(tmp_engine, "php")
    sql = [p for _, sink_id, p in tagged if sink_id.startswith("php.sql")]
    # literal prepared statement and constant query untagged
    assert not any(":id" in p and "$" not in p for p in sql)
    assert not any("COUNT(*)" in p for p in sql)
    # interpolated query and variable prepare stay tagged
    assert any("$name" in p for p in sql)
    assert any("$sql" in p for p in sql)


def test_registry_negative_matrix():
    """Fast registry-level truth table for the guarded patterns."""
    from entrygraph.detect.taint import builtin_registry

    r = builtin_registry()
    negative = [
        ("rb:*.execute", "(params)"),
        ("rb:*.execute", "(declared_params(include_missing: false))"),
        ("rb:*.capture3", '(["ls", "-l"])'),
        ("rb:system", '("ls -l")'),
        ("java:*.execute", "(runnable)"),
        ("java:*.lookup", "()"),
        ("java:*.lookup", None),
        ("java:*.evaluate", "()"),
        ("php:*.query", "('SELECT 1')"),
        ("php:*.prepare", "('SELECT * FROM t WHERE id = :id')"),
        ("js:*.query", "('SELECT * FROM t WHERE id = ?', [id])"),
        ("js:*.exec", "(input)"),
        ("go:*.Query", '("offset")'),
    ]
    for callee, preview in negative:
        assert r.match(callee, preview) is None, f"{callee} {preview!r} must not tag"

    positive = [
        ("rb:*.execute", '("SELECT * FROM t WHERE id = #{id}")', "rb.sql-execute"),
        ("rb:*.capture3", "(user_cmd)", "rb.command-exec.open3"),
        ("rb:system", '("ls #{dir}")', "rb.command-exec.kernel"),
        ("rb:system", "(*args)", "rb.command-exec.kernel"),  # splat forwarding
        ("rb:system", "yarn, *ARGV", "rb.command-exec.kernel"),  # paren-less call
        ("java:*.execute", '("DELETE FROM t WHERE id = " + id)', "java.sql-execute"),
        ("java:*.lookup", "(name)", "java.jndi"),
        ("java:*.evaluate", "(expr)", "java.expression-injection.evaluate"),
        ("php:*.query", "(\"SELECT * FROM t WHERE n = '$n'\")", "php.sql.pdo-query"),
        ("js:*.query", "('SELECT * FROM t WHERE id = ' + id)", "js.sql-query"),
    ]
    for callee, preview, expected in positive:
        assert r.match(callee, preview) == expected, f"{callee} {preview!r} -> {expected}"


@pytest.mark.parametrize("lang", ["ruby", "python", "javascript", "java", "php"])
def test_precision_fixture_extracts(tmp_engine, lang):
    # guard: every precision fixture actually parses and produces edges, so the
    # negative assertions above can't pass vacuously.
    stats = index_repository(PRECISION / lang, tmp_engine)
    assert stats.symbols > 0
    assert stats.edges > 0
