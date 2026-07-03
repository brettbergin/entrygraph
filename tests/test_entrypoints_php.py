"""PHP entrypoint-rule tests (kept per-language so PRs touching different
languages don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.detect.entrypoints.php import _symfony_methods, _symfony_path
from entrygraph.extract.base import FileContext
from entrygraph.extract.php import PhpExtractor
from entrygraph.parsing.parsers import parse


def _symfony_rule():
    return {r.id: r for r in rules_for("php", {"symfony"})}["php.symfony.route"]


def _extract(src: bytes, path="src/Controller/UserController.php"):
    ctx = FileContext(
        path=path,
        language="php",
        module_path="App.Controller.UserController",
        source=src,
        is_package=False,
    )
    return PhpExtractor().extract(parse("php", src), ctx)


def test_symfony_class_route_prefix_composed():
    # Class-level #[Route('/profile')] must prefix each method route, and
    # methods: [...] must be honored (symfony-demo: 11/12 routes were wrong) (#36).
    src = (
        b"<?php\nnamespace App\\Controller;\n"
        b"#[Route('/profile'), IsGranted(User::ROLE_USER)]\n"
        b"final class UserController extends AbstractController\n{\n"
        b"    #[Route('/edit', name: 'user_edit', methods: ['GET', 'POST'])]\n"
        b"    public function edit() {}\n"
        b"    #[Route(path: '/pw', name: 'pw')]\n"
        b"    public function pw() {}\n"
        b"}\n"
    )
    got = {(tuple(h.http_methods), h.route) for h in _symfony_rule().match(_extract(src))}
    assert got == {
        (("GET", "POST"), "/profile/edit"),
        (("*",), "/profile/pw"),  # path: named arg, no methods -> all verbs
    }


def test_symfony_route_without_class_prefix():
    # A controller with no class-level #[Route] leaves method routes unprefixed.
    src = (
        b"<?php\nnamespace App\\Controller;\n"
        b"final class SecurityController extends AbstractController\n{\n"
        b"    #[Route('/login', name: 'login')]\n"
        b"    public function login() {}\n"
        b"}\n"
    )
    got = {(tuple(h.http_methods), h.route) for h in _symfony_rule().match(_extract(src))}
    assert got == {(("*",), "/login")}


def test_symfony_path_and_methods_parsing():
    assert _symfony_path("#[Route('/edit', name: 'x')]") == "/edit"
    assert _symfony_path("#[Route(path: '/pw', name: 'x')]") == "/pw"
    assert _symfony_methods("#[Route('/x', methods: ['GET', 'POST'])]") == ["GET", "POST"]
    assert _symfony_methods("#[Route('/x', methods: 'DELETE')]") == ["DELETE"]
    assert _symfony_methods("#[Route('/x', name: 'x')]") == []
