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
    assert registry.match("java:ctx.lookup") == "java.jndi"
    assert registry.match("rb:Open3.capture3") == "rb.command-exec.open3"


def test_categories_are_queryable(registry):
    for category in ("ssrf", "xxe", "weak_crypto", "path_traversal", "jndi"):
        assert registry.ids_for_category(category), f"no sinks for {category}"
