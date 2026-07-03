"""C# entrypoint-rule tests (kept per-language so PRs touching different languages
don't serially conflict on one shared test module)."""

from __future__ import annotations

from entrygraph.detect.entrypoints import rules_for
from entrygraph.extract.base import FileContext
from entrygraph.extract.csharp import CSharpExtractor
from entrygraph.parsing.parsers import parse


def _controller_rule():
    return {r.id: r for r in rules_for("csharp", {"aspnetcore"})}["csharp.aspnet.controller-route"]


def _extract(src: bytes, path="Controllers/C.cs", module="App.Controllers"):
    ctx = FileContext(
        path=path, language="csharp", module_path=module, source=src, is_package=False
    )
    return CSharpExtractor().extract(parse("csharp", src), ctx)


def test_conventional_mvc_actions_detected():
    # Non-attribute controller: public actions route to /{controller}/{action} via
    # GET; constructors, [NonAction], override, and [HttpPost] methods are excluded
    # (the POST one is emitted by the attribute branch instead) (#37).
    src = (
        b"namespace App.Controllers;\n"
        b"public class HomeController : Controller\n{\n"
        b"    public HomeController() {}\n"
        b"    public IActionResult Index() { return View(); }\n"
        b"    public async Task<IActionResult> About() { return View(); }\n"
        b"    [NonAction] public void Helper() {}\n"
        b"    public override void OnActionExecuting() {}\n"
        b"}\n"
    )
    got = {(tuple(h.http_methods), h.route) for h in _controller_rule().match(_extract(src))}
    assert got == {(("GET",), "/Home/Index"), (("GET",), "/Home/About")}


def test_conventional_route_includes_area_prefix():
    src = (
        b"namespace App.Areas.Admin.Controllers;\n"
        b'[Area("Admin")]\n'
        b"public class DashboardController : Controller\n{\n"
        b"    public IActionResult Stats() { return View(); }\n"
        b"}\n"
    )
    got = {h.route for h in _controller_rule().match(_extract(src))}
    assert got == {"/Admin/Dashboard/Stats"}


def test_api_controllers_are_not_convention_routed():
    # [ApiController] and a class-level [Route] both mandate attribute routing, so a
    # bare public method must NOT get a convention route.
    src = (
        b"namespace App.Controllers;\n"
        b"[ApiController]\n"
        b'[Route("api/[controller]")]\n'
        b"public class ThingsController : ControllerBase\n{\n"
        b"    public IActionResult NoAttr() { return Ok(); }\n"
        b"}\n"
    )
    assert _controller_rule().match(_extract(src)) == []


def test_attribute_routes_still_win_over_convention():
    src = (
        b"namespace App.Controllers;\n"
        b"public class HomeController : Controller\n{\n"
        b'    [HttpGet("ping")] public IActionResult Ping() { return Ok(); }\n'
        b"}\n"
    )
    got = {(tuple(h.http_methods), h.route) for h in _controller_rule().match(_extract(src))}
    assert got == {(("GET",), "/ping")}  # attribute route, not /Home/Ping
