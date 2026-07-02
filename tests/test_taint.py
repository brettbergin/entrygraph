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


def test_sanitizer_matching_and_category_lookup():
    reg = SinkRegistry(
        sinks=[SinkPattern(id="s", category="command_exec", callee="py:os.system")],
        sources=[],
        sanitizers=[
            SanitizerPattern(id="san", category="command_exec",
                             callee="py:shlex.{quote,split}", effect="neutralizes")
        ],
    )
    assert reg.match("py:os.system") == "s"
    matched = reg.match_sanitizers("py:shlex.quote")
    assert len(matched) == 1 and matched[0].effect == "neutralizes"
    assert reg.match_sanitizers("py:os.system") == []
    assert reg.sanitizers_for_category("command_exec")[0].id == "san"


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
