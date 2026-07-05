"""Python entrypoint-rule tests (kept per-language so PRs touching different
languages don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.extract.ir import FileExtraction, RawReference, Span
from entrygraph.pipeline.scanner import _collect_route_wrappers


def _call(callee, arg, receiver=None, caller=None):
    return RawReference(
        kind="call",
        callee_text=f"{receiver}.{callee}" if receiver else callee,
        callee_name=callee,
        receiver_text=receiver,
        span=Span(1, 0, 1, 40),
        caller_qualified_name=caller,
        arg_preview=arg,
    )


def _py_ext(references=(), path="zproject/urls.py", wrappers=frozenset()):
    return FileExtraction(
        path=path,
        language="python",
        module_path=path.replace("/", ".").removesuffix(".py"),
        parse_ok=True,
        error_count=0,
        references=list(references),
        route_wrappers=set(wrappers),
    )


def _django_rule():
    return {r.id: r for r in rules_for("python", {"django"})}["python.django.urls"]


def test_django_native_path_still_detected():
    ext = _py_ext([_call("path", "('api/foo', view)")])
    assert [h.route for h in _django_rule().match(ext)] == ["api/foo"]


def test_django_wrapper_calls_detected_via_route_wrappers():
    # Zulip's rest_path forwards to path(); a call to it in urls.py is a route once
    # the wrapper name is known (#50).
    refs = [_call("rest_path", "('messages/render', view)"), _call("path", "('foo', v)")]
    ext = _py_ext(refs, wrappers={"rest_path"})
    routes = {h.route for h in _django_rule().match(ext)}
    assert routes == {"messages/render", "foo"}
    # without the wrapper set, rest_path is invisible
    assert {h.route for h in _django_rule().match(_py_ext(refs))} == {"foo"}


def test_collect_route_wrappers_finds_forwarders_only():
    # a function that calls path()/re_path() is a wrapper; the module-level
    # urlpatterns path() calls (no enclosing function) are not.
    rest = _py_ext(
        [_call("path", "(route, rest_dispatch)", caller="zerver.lib.rest.rest_path")],
        path="zerver/lib/rest.py",
    )
    urls = _py_ext([_call("path", "('foo', v)", caller=None)])  # module-level urlpattern
    wrappers = _collect_route_wrappers([("p1", rest, False), ("p2", urls, False)])
    assert wrappers == {"rest_path"}


def test_dedup_prefers_fastapi_over_flask_on_confidence_tie():
    # both flask and fastapi rules fire for `@app.get('/x')`; on a detection tie the
    # route must read as fastapi (its primary syntax), not flask (#116 QA regression)
    from entrygraph.extract.ir import EntrypointHint
    from entrygraph.kinds import EntrypointKind
    from entrygraph.pipeline.scanner import _dedup_entrypoint_hints

    def hint(framework, rule):
        return EntrypointHint(
            rule_id=rule,
            kind=EntrypointKind.HTTP_ROUTE,
            handler_qualified_name="app.read_item",
            route="/items",
            http_methods=["GET"],
            framework=framework,
        )

    flask = hint("flask", "python.flask.route")
    fastapi = hint("fastapi", "python.fastapi.route")
    tie = {"flask": 0.7, "fastapi": 0.7}
    # deterministic regardless of hint order
    assert _dedup_entrypoint_hints([flask, fastapi], tie)[0].framework == "fastapi"
    assert _dedup_entrypoint_hints([fastapi, flask], tie)[0].framework == "fastapi"
    assert len(_dedup_entrypoint_hints([flask, fastapi], tie)) == 1
    # confidence still dominates the tiebreak: a clearly-more-confident flask wins
    strong_flask = {"flask": 0.95, "fastapi": 0.7}
    assert _dedup_entrypoint_hints([fastapi, flask], strong_flask)[0].framework == "flask"
