"""Reaching-defs language breadth: Ruby, Go, Java, PHP (#96 Phase 2 breadth).

End-to-end verification through the graph, plus grammar node-name pins so a
tree-sitter-language-pack bump that renames a node fails loudly rather than
silently returning None everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entrygraph import CodeGraph
from entrygraph.analysis.facts import extract_function_facts, language_supported

FIX = Path(__file__).parent / "fixtures"

_CASES = {
    "ruby": ("ruby/reaching", "app.UsersController"),
    "go": ("go/reaching", "_root"),
    "java": ("java/reaching", "App"),
    "php": ("php/reaching", "app"),
}


@pytest.mark.parametrize("lang", list(_CASES))
def test_confirmed_and_refuted_per_language(tmp_path, lang):
    fixture_dir, prefix = _CASES[lang]
    g = CodeGraph.index(FIX / fixture_dir, db=tmp_path / f"{lang}.db")
    try:
        paths = g.paths(
            source_category="http_input", sink_category="command_exec", include_unresolved=True
        )
        by_name = {p.symbols[0].qname.rsplit(".", 1)[-1].lower(): p for p in paths}
        assert by_name["confirmed"].taint_verified is True
        assert by_name["refuted"].taint_verified is False
    finally:
        g.close()


def test_supported_language_set():
    for lang in ("python", "javascript", "typescript", "tsx", "ruby", "go", "java", "php"):
        assert language_supported(lang)
    # C# and Rust are deliberately deferred -> None, no behavior change
    assert not language_supported("csharp")
    assert not language_supported("rust")


# --- grammar node-name pins: a renamed node in a grammar bump fails here ---


def test_ruby_grammar_nodes():
    f = extract_function_facts("ruby", b"def h\n  x = params[:id]\n  system(x)\nend\n", 1, 1)
    assert f is not None
    assert any(getattr(fact, "callee_name", "") == "system" for fact in f.facts)


def test_go_grammar_nodes():
    src = b"package m\nfunc H(r int) {\n\tq := form(r)\n\texec(q)\n}\n"
    f = extract_function_facts("go", src, 2, 5)
    assert f is not None
    assert any(getattr(fact, "targets", None) == ("q",) for fact in f.facts)


def test_java_grammar_nodes():
    src = b"class A {\n  void h(String id) {\n    String q = get(id);\n    run(q);\n  }\n}\n"
    f = extract_function_facts("java", src, 2, 5)
    assert f is not None
    assert f.params == ("id",)


def test_php_grammar_nodes():
    src = b"<?php\nfunction h() {\n  $q = $_GET['x'];\n  system($q);\n}\n"
    f = extract_function_facts("php", src, 2, 5)
    assert f is not None
    assert any(getattr(fact, "callee_name", "") == "system" for fact in f.facts)
