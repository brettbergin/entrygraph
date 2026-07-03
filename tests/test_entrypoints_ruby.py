"""Ruby entrypoint-rule tests (kept per-language so PRs touching different
languages don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.extract.ir import FileExtraction, RawReference, Span


def _ruby_ext(references, path):
    return FileExtraction(
        path=path,
        language="ruby",
        module_path=path.replace("/", ".").removesuffix(".rb"),
        parse_ok=True,
        error_count=0,
        symbols=[],
        references=list(references),
    )


def _verb(verb, route):
    return RawReference(
        kind="call",
        callee_text=verb,
        callee_name=verb,
        receiver_text=None,
        span=Span(1, 0, 1, 40),
        caller_qualified_name=None,
        arg_preview=f"('{route}')",
    )


def _sinatra_rule():
    return {r.id: r for r in rules_for("ruby", {"sinatra"})}["ruby.sinatra.route"]


def _grape_rule():
    return {r.id: r for r in rules_for("ruby", {"grape"})}["ruby.grape.route"]


def test_sinatra_route_in_app_file_detected():
    hints = _sinatra_rule().match(_ruby_ext([_verb("get", "/health")], "app.rb"))
    assert [h.route for h in hints] == ["/health"]


def test_sinatra_ignores_rack_test_calls_in_spec_and_test_files():
    # Rack::Test uses the same bare `get '/x'` DSL inside specs; those files are not
    # the route surface (sinatra corpus: 256/274 routes came from test/spec) (#33).
    for path in (
        "test/routing_test.rb",
        "spec/app_spec.rb",
        "rack-protection/spec/lib/rack/protection/ip_spoofing_spec.rb",
        "some/nested/test/helper_test.rb",
    ):
        assert _sinatra_rule().match(_ruby_ext([_verb("get", "/")], path)) == []


def test_grape_ignores_test_files():
    assert _grape_rule().match(_ruby_ext([_verb("post", "/x")], "spec/api_spec.rb")) == []
    assert [h.route for h in _grape_rule().match(_ruby_ext([_verb("post", "/x")], "api.rb"))] == [
        "/x"
    ]
