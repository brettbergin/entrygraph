from __future__ import annotations

from entrygraph.detect.taint import (
    SanitizerPattern,
    SinkPattern,
    SinkRegistry,
    SourcePattern,
    _load_toml,
    builtin_registry,
)


def test_load_toml_parses_sinks_sources_sanitizers():
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

[[sanitizer]]
id = "x.san"
category = "command_exec"
callee = "py:shlex.quote"
effect = "neutralizes"
"""
    sinks, sources, sanitizers, disable = _load_toml(text)
    assert sinks[0].library == "stdlib"
    assert sources[0].id == "x.src"
    assert sanitizers[0].effect == "neutralizes"
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


def test_sanitizer_matching_and_category_lookup():
    reg = SinkRegistry(
        sinks=[SinkPattern(id="s", category="command_exec", callee="py:os.system")],
        sources=[],
        sanitizers=[
            SanitizerPattern(
                id="san",
                category="command_exec",
                callee="py:shlex.{quote,split}",
                effect="neutralizes",
            )
        ],
    )
    assert reg.match("py:os.system") == "s"
    matched = reg.match_sanitizers("py:shlex.quote")
    assert len(matched) == 1 and matched[0].effect == "neutralizes"
    assert reg.match_sanitizers("py:os.system") == []
    assert reg.sanitizers_for_category("command_exec")[0].id == "san"


def test_builtin_sanitizers_cover_all_languages():
    # every supported language now ships sanitizers, matched against the exact
    # canonical callee form its resolver produces.
    reg = builtin_registry()
    cases = [
        ("go:path/filepath.Clean", "go.sanitize.filepath-clean"),
        ("go:strconv.Atoi", "go.sanitize.strconv-sql"),
        ("java:*.forHtml", "java.sanitize.html-encode"),
        ("java:*.getFileName", "java.sanitize.path-normalize"),
        ("rb:*.shellescape", "rb.sanitize.shellwords"),
        ("rb:File.basename", "rb.sanitize.basename"),
        ("cs:System.IO.Path.GetFileName", "cs.sanitize.path-getfilename"),
        ("cs:*.AddWithValue", "cs.sanitize.sql-parameter"),
        ("php:escapeshellarg", "php.sanitize.escapeshell"),
        ("php:htmlspecialchars", "php.sanitize.htmlspecialchars"),
        ("rs:shell_escape.escape", "rs.sanitize.shell-escape"),
        ("rs:*.file_name", "rs.sanitize.path-filename"),
    ]
    for callee, sid in cases:
        assert sid in {s.id for s in reg.match_sanitizers(callee)}, callee
    # all eight languages represented
    langs = {sid.split(".")[0] for sid in reg.sanitizers}
    assert langs == {"py", "js", "go", "java", "rb", "cs", "php", "rs"}


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


def test_builtin_registry_loads_library_summaries_and_sanitizers():
    reg = builtin_registry()
    # library summary: paramiko exec_command mapped to command_exec
    assert reg.match("py:client.exec_command") == "lib.py.paramiko.exec"
    assert reg.sinks["lib.py.paramiko.exec"].library == "paramiko"
    # execa (bare call) isn't caught by any generic pattern -> library summary wins
    assert reg.match("js:execa") == "lib.js.execa"
    assert reg.sinks["lib.js.execa"].library == "execa"
    # shipped sanitizers are discoverable
    assert reg.match_sanitizers("py:shlex.quote")
    assert any(s.effect == "neutralizes" for s in reg.sanitizers_for_category("command_exec"))


def test_merged_with_preserves_sanitizers_and_honors_disable():
    base = builtin_registry()
    extra_san = SanitizerPattern(id="extra", category="sql", callee="py:mydb.escape")
    merged = base.merged_with([], [], disable=["py.sanitize.shlex"], sanitizers=[extra_san])
    assert "extra" in merged.sanitizers
    assert "py.sanitize.shlex" not in merged.sanitizers
