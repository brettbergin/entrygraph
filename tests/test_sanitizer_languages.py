"""End-to-end sanitizer detection across the newly-covered languages (Phase 5).

Regression: Go, Java, Ruby, C#, PHP, and Rust shipped zero sanitizers. A sibling
sanitizer call for the sink's category now discounts the path's risk (matched via
out-edges, per the query-time sanitizer machinery).
"""

from __future__ import annotations

import pytest

from entrygraph import CodeGraph

# (language, filename, source code, path source qname, sink category, expected sanitizer id)
CASES = [
    (
        "go",
        "main.go",
        'package main\nimport ("os"; "path/filepath")\n'
        "func h(p string) { filepath.Clean(p); os.ReadFile(p) }\n",
        "_root.h",
        "path_traversal",
        "go.sanitize.filepath-clean",
    ),
    (
        "php",
        "i.php",
        "<?php\nfunction h($c) { escapeshellarg($c); system($c); }\n",
        "i.h",
        "command_exec",
        "php.sanitize.escapeshell",
    ),
    (
        "ruby",
        "a.rb",
        "def h(c)\n  c.shellescape\n  system(c)\nend\n",
        "a.h",
        "command_exec",
        "rb.sanitize.shellwords",
    ),
]


@pytest.mark.parametrize("lang,fname,code,source,category,sanitizer", CASES)
def test_sibling_sanitizer_discounts_risk(tmp_path, lang, fname, code, source, category, sanitizer):
    (tmp_path / fname).write_text(code)
    g = CodeGraph.index(tmp_path, db=tmp_path / "g.db")
    try:
        paths = g.paths(source=source, sink_category=category, include_unresolved=True)
        assert paths, f"{lang}: expected a {category} path from {source}"
        path = paths[0]
        assert sanitizer in {sid for e in path.edges for sid in e.sanitized_by}
        # a detected sanitizer discounts risk without zeroing it
        assert 0.0 < path.risk_score < 0.5
    finally:
        g.close()
