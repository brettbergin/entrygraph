from __future__ import annotations

from entrygraph.fs.lang import EXTRACTABLE, RepoLanguageProfile, detect_language


def test_extension_detection():
    assert detect_language("src/app.py") == "python"
    assert detect_language("web/index.TSX") == "tsx"
    assert detect_language("cmd/main.go") == "go"
    assert detect_language("lib/task.rake") == "ruby"
    assert detect_language("README.md") == "markdown"
    assert detect_language("data.bin") is None


def test_new_language_extensions():
    assert detect_language("src/App.cs") == "csharp"
    assert detect_language("src/index.php") == "php"
    assert detect_language("templates/view.phtml") == "php"
    assert detect_language("src/main.rs") == "rust"
    assert {"csharp", "php", "rust"} <= EXTRACTABLE


def test_not_extracted_group_is_not_in_extractable():
    # The "recognized but not extracted" extensions must genuinely lack extractors;
    # cs/rs/php were mis-grouped under that comment despite having extractors (#48).
    from entrygraph.fs.lang import _EXTENSION_MAP

    not_extracted = {"c", "cpp", "kotlin", "swift", "scala"}
    assert not (not_extracted & EXTRACTABLE)
    for ext in (".cs", ".rs", ".php", ".phtml"):
        assert _EXTENSION_MAP[ext] in EXTRACTABLE


def test_common_unsupported_languages_are_recognized():
    # A repo's dominant language must be recognized (not None) so its files stay in
    # stats and the files table; pandoc's .hs files used to vanish, making it look
    # 82% markdown (#49). These have no extractor, so they're recognized-not-extracted.
    cases = {
        "src/Text/Pandoc.hs": "haskell",
        "lib/app.ex": "elixir",
        "src/core.clj": "clojure",
        "main.lua": "lua",
        "analysis.R": "r",  # extension matched case-insensitively
        "cmd/tool.dart": "dart",
        "contracts/Token.sol": "solidity",
        "src/App.vue": "vue",
    }
    for path, lang in cases.items():
        assert detect_language(path) == lang
        assert lang not in EXTRACTABLE  # recognized for honest stats, no extractor


def test_filename_detection():
    assert detect_language("Gemfile") == "ruby"
    assert detect_language("subdir/Rakefile") == "ruby"
    assert detect_language("Dockerfile") == "dockerfile"


def test_shebang_detection():
    assert detect_language("bin/tool", b"#!/usr/bin/env python3\n") == "python"
    assert detect_language("bin/tool", b"#!/usr/bin/env node\n") == "javascript"
    assert detect_language("bin/tool", b"#!/bin/bash\n") is None
    # extension always wins over shebang
    assert detect_language("bin/tool.rb", b"#!/usr/bin/env python\n") == "ruby"


def test_language_profile_percentages():
    profile = RepoLanguageProfile()
    profile.add("python", 750)
    profile.add("python", 150)
    profile.add("javascript", 100)
    profile.add(None, 999)  # ignored

    stats = {s.name: s for s in profile.stats()}
    assert stats["python"].file_count == 2
    assert stats["python"].percent == 90.0
    assert stats["javascript"].percent == 10.0
    assert profile.extractable_languages() == {"python", "javascript"}
