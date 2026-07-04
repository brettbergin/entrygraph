"""Central test-file classifier.

Test suites are recognized here — once — instead of per-detection-rule, and
excluded from extraction at walk time (``skip_reason="test"``). Test symbols and
edges otherwise pollute the graph: on real corpora the highest-confidence taint
findings were dominated by ``*_test.go`` handlers (#94).

Conventions are deliberately curated and high-confidence: unambiguous filename
suffixes (``_test.go``, ``_spec.rb``, ``FooTest.java``) and directory segments
that are vanishingly rare in production code (``tests/``, ``__mocks__/``,
``src/test/``). All checks run on the **repo-relative** path so the absolute
path to the repository can never false-match.

Rust inline ``#[cfg(test)] mod tests`` (and bare ``#[test]`` functions) live
inside production ``.rs`` files, so the file classifier can't see them; they are
excluded at extractor level instead — see ``extract/rust.py`` ``_drop_test_code``
(#100), which honors the same ``--include-tests`` override.
"""

from __future__ import annotations

# Directory segments that mark a subtree as test code regardless of language.
_TEST_DIR_SEGMENTS = frozenset(
    {
        "test",
        "tests",
        "spec",
        "specs",
        "testdata",
        "__tests__",
        "__mocks__",
        "__fixtures__",
        "fixtures",
        "mocks",
        "cypress",
        "e2e",
    }
)

_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
_JS_TEST_MARKERS = (".test.", ".spec.", ".cy.", ".e2e.")


def is_test_path(rel_path: str, language: str | None = None) -> bool:
    """True if the repo-relative path is test code by curated convention.

    ``rel_path`` must be repo-relative (posix separators as produced by the
    walker); passing an absolute path would let the repository's own location
    (e.g. ``/home/ci/tests/myrepo``) misclassify every file.
    """
    path = rel_path.replace("\\", "/").strip("/")
    parts = path.split("/")
    name = parts[-1]

    for seg in parts[:-1]:
        low = seg.lower()
        if low in _TEST_DIR_SEGMENTS:
            return True
        if low.endswith(".tests"):  # C# `Foo.Tests/` project directories
            return True
    if language == "ruby" and any(seg.lower() == "features" for seg in parts[:-1]):
        return True  # cucumber

    if name.endswith("_test.go"):
        return True
    if name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
    ):
        return True
    if name.endswith(_JS_EXTS) and any(marker in name for marker in _JS_TEST_MARKERS):
        return True
    if name.endswith(("_spec.rb", "_test.rb")):
        return True
    if name.endswith((".java", ".kt")):
        stem = name.rsplit(".", 1)[0]
        if stem.endswith(("Test", "Tests", "IT")):
            return True
    if name.endswith(".cs") and name[:-3].endswith(("Test", "Tests")):
        return True
    if name.endswith("Test.php"):
        return True
    return name.endswith("_test.rs")
