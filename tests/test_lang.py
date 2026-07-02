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
