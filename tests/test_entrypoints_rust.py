"""Rust entrypoint-rule tests (kept per-language so PRs touching different
languages don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.extract.base import FileContext
from entrygraph.extract.rust import RustExtractor
from entrygraph.parsing.parsers import parse


def _axum_rule():
    return {r.id: r for r in rules_for("rust", {"axum"})}["rust.axum.route"]


def _extract(src: bytes, path="src/main.rs"):
    ctx = FileContext(path=path, language="rust", module_path="main", source=src, is_package=False)
    return RustExtractor().extract(parse("rust", src), ctx)


def test_axum_nest_prefix_composed_for_function_router():
    # `.nest("/admin", admin_routes())` prefixes the routes declared inside the
    # admin_routes builder function; top-level routes stay unprefixed (#36).
    src = (
        b"fn admin_routes() -> Router {\n"
        b"    Router::new()\n"
        b'        .route("/keys", delete(delete_all_keys))\n'
        b'        .route("/key/{key}", delete(remove_key))\n'
        b"}\n"
        b"async fn main() {\n"
        b"    let app = Router::new()\n"
        b'        .route("/keys", get(list_keys))\n'
        b'        .nest("/admin", admin_routes());\n'
        b"}\n"
    )
    got = {(h.http_methods[0], h.route) for h in _axum_rule().match(_extract(src))}
    assert got == {
        ("GET", "/keys"),  # top-level, no prefix
        ("DELETE", "/admin/keys"),  # composed with the nest prefix
        ("DELETE", "/admin/key/{key}"),
    }


def test_axum_routes_without_nest_are_unprefixed():
    src = (
        b"async fn main() {\n"
        b"    let app = Router::new()\n"
        b'        .route("/users", get(list_users))\n'
        b'        .route("/users/{id}", get(get_user));\n'
        b"}\n"
    )
    got = {(h.http_methods[0], h.route) for h in _axum_rule().match(_extract(src))}
    assert got == {("GET", "/users"), ("GET", "/users/{id}")}
