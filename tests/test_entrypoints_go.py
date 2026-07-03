"""Go entrypoint-rule tests (kept per-language so PRs touching different languages
don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.extract.ir import FileExtraction, RawReference, Span


def _go_ext(references, path="main.go"):
    return FileExtraction(
        path=path,
        language="go",
        module_path="app",
        parse_ok=True,
        error_count=0,
        symbols=[],
        references=list(references),
    )


def _call(callee, receiver, arg, start=1, end=None):
    return RawReference(
        kind="call",
        callee_text=f"{receiver}.{callee}" if receiver else callee,
        callee_name=callee,
        receiver_text=receiver,
        span=Span(start, 0, end if end is not None else start, 40),
        caller_qualified_name="app.routes",
        arg_preview=arg,
    )


def _chi_rule():
    return {r.id: r for r in rules_for("go", {"chi"})}["go.chi.route"]


def test_chi_route_prefix_is_composed():
    # r.Route("/admin", func(r){ r.Get("/users", h); r.Post("/users", h) }) — the
    # inner verb routes fall inside the Route call's span and get the prefix (#37).
    refs = [
        _call("Get", "r", '("/health", health)', start=2),
        _call("Route", "r", '("/admin", func(r chi.Router) { ... })', start=3, end=6),
        _call("Get", "r", '("/users", listUsers)', start=4),
        _call("Post", "r", '("/users", createUser)', start=5),
    ]
    got = {(h.http_methods[0], h.route) for h in _chi_rule().match(_go_ext(refs))}
    assert got == {
        ("GET", "/health"),  # top-level, no prefix
        ("GET", "/admin/users"),  # composed with the enclosing Route prefix
        ("POST", "/admin/users"),
    }


def test_chi_nested_route_prefixes_stack():
    refs = [
        _call("Route", "r", '("/api", func(r){ ... })', start=1, end=6),
        _call("Route", "r", '("/v1", func(r){ ... })', start=2, end=5),
        _call("Get", "r", '("/users", h)', start=3),
    ]
    got = {(h.http_methods[0], h.route) for h in _chi_rule().match(_go_ext(refs))}
    assert got == {("GET", "/api/v1/users")}  # both enclosing prefixes stack


def test_chi_mount_prefix_not_composed_across_functions():
    # r.Mount("/api", apiRouter()) points at a router built elsewhere (single-line,
    # no inline body) — its prefix is out of static reach and must not be applied.
    refs = [
        _call("Mount", "r", '("/api", apiRouter())', start=1),
        _call("Get", "r", '("/health", h)', start=2),
    ]
    got = {(h.http_methods[0], h.route) for h in _chi_rule().match(_go_ext(refs))}
    assert got == {("GET", "/health")}  # no phantom /api prefix
