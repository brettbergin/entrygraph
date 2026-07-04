from __future__ import annotations

import pytest

from entrygraph.fs.testfiles import is_test_path

TEST_PATHS = [
    # Go
    ("workhorse/internal/upload/artifacts_upload_test.go", None),
    ("pkg/testdata/gen.go", None),
    # Python
    ("tests/test_app.py", None),
    ("app/test_routes.py", None),
    ("app/routes_test.py", None),
    ("conftest.py", None),
    ("app/conftest.py", None),
    # JS/TS
    ("src/routes.test.ts", None),
    ("src/routes.spec.js", None),
    ("src/login.cy.ts", None),
    ("src/login.e2e.tsx", None),
    ("src/__tests__/routes.js", None),
    ("src/__mocks__/api.ts", None),
    ("cypress/support/index.js", None),
    ("e2e/login.js", None),
    # Ruby
    ("spec/models/user_spec.rb", None),
    ("app/models/user_test.rb", None),
    ("features/step_definitions/login.rb", "ruby"),
    # Java / Kotlin
    ("src/test/java/com/foo/FooTest.java", None),
    ("com/foo/FooTest.java", None),
    ("com/foo/FooTests.java", None),
    ("com/foo/FooIT.java", None),
    ("com/foo/FooTest.kt", None),
    # C#
    ("Foo.Tests/BarTests.cs", None),
    ("src/FooTest.cs", None),
    # PHP
    ("tests/FooTest.php", None),
    ("src/FooTest.php", None),
    # Rust
    ("src/parser_test.rs", None),
    ("tests/integration.rs", None),
    # generic dirs
    ("mocks/server.go", None),
    ("fixtures/sample.py", None),
    ("__fixtures__/sample.js", None),
]

PRODUCTION_PATHS = [
    ("workhorse/internal/upload/artifacts_upload.go", None),
    ("app/routes.py", None),
    ("app/latest.rb", None),  # ends in "test"-adjacent letters but isn't a suffix match
    ("app/contest.py", None),
    ("src/attest.go", None),
    ("src/routes.ts", None),
    ("src/spectrum.js", None),
    ("lib/api/badges.rb", None),
    ("com/foo/Fastest.java", None),  # "…est.java" but not Test/Tests/IT
    ("src/Protest.cs", None),
    ("src/latest.php", None),
    ("src/protest.rs", None),
    ("features/search/index.ts", None),  # JS feature-module dir is production code
    ("features/search.py", "python"),  # features/ only counts for ruby
]


@pytest.mark.parametrize("path,language", TEST_PATHS)
def test_recognized_as_test(path, language):
    assert is_test_path(path, language) is True


@pytest.mark.parametrize("path,language", PRODUCTION_PATHS)
def test_recognized_as_production(path, language):
    assert is_test_path(path, language) is False


def test_relative_paths_only_no_abs_prefix_false_match():
    # The classifier sees repo-relative paths; a repo that lives under a
    # directory named "tests" must not have every file classified as test.
    # Guard the contract: the path below is what the walker would hand us for
    # a production file, and it must stay production even though the repo's
    # absolute location (not visible here) could be /home/ci/tests/repo.
    assert is_test_path("app/routes.py") is False
    # Leading slashes are normalized away rather than creating empty segments.
    assert is_test_path("/app/routes.py") is False
    assert is_test_path("/tests/app.py") is True


def test_windows_separators_normalized():
    assert is_test_path("src\\__tests__\\routes.js") is True
    assert is_test_path("src\\routes.js") is False
