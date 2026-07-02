from __future__ import annotations

from pathlib import Path

from entrygraph.detect.entrypoints import rules_for
from entrygraph.detect.entrypoints.base import identifier_args, tainted_params
from entrygraph.detect.entrypoints.configs import (
    bind_handler,
    scan_config_entrypoints,
)
from entrygraph.extract.ir import FileExtraction, RawReference, RawSymbol, Span
from entrygraph.kinds import EntrypointKind, SymbolKind

SPAN = Span(1, 0, 1, 20)


def _sym(name, qname, kind=SymbolKind.FUNCTION, signature=None):
    return RawSymbol(
        kind=kind, name=name, qualified_name=qname, span=SPAN, signature=signature, decorators=[]
    )


def _extraction(symbols=(), references=(), path="app/routes.py", module="app.routes"):
    return FileExtraction(
        path=path,
        language="python",
        module_path=module,
        parse_ok=True,
        error_count=0,
        symbols=list(symbols),
        references=list(references),
    )


# ---------------- base helpers ----------------


def test_identifier_args():
    assert identifier_args("('/x', view_func=handler)") == ["handler"]
    assert identifier_args("(handler, name='x')") == ["handler"]
    assert identifier_args("('/x', 'literal')") == []


def test_tainted_params():
    assert tainted_params("def create(name, request):", "http_route") == ["name", "request"]
    assert tainted_params("def h(self, req):", "http_route") == ["req"]
    assert tainted_params("def handler(event, context):", "lambda_handler") == ["event"]
    assert tainted_params("def job(payload, count):", "task") == ["payload"]
    assert tainted_params(None, "http_route") == []


# ---------------- flask rules ----------------


def _run(rule_id, x, frameworks=frozenset({"flask", "fastapi"})):
    rules = {r.id: r for r in rules_for("python", frameworks)}
    return rules[rule_id].match(x)


def test_flask_route_carries_tainted_params():
    sym = _sym(
        "create_report", "app.routes.create_report", signature="def create_report(name, request):"
    )
    sym.decorators = ["@app.route('/reports', methods=['POST'])"]
    hints = _run("python.flask.route", _extraction([sym]))
    assert hints and hints[0].metadata["tainted_params"] == ["name", "request"]


def test_flask_add_url_rule():
    handler = _sym("show", "app.routes.show")
    ref = RawReference(
        kind="call",
        callee_text="app.add_url_rule",
        callee_name="add_url_rule",
        receiver_text="app",
        span=SPAN,
        caller_qualified_name=None,
        arg_preview="('/show', view_func=show)",
    )
    hints = _run("python.flask.add_url_rule", _extraction([handler], [ref]))
    assert hints and hints[0].route == "/show"
    assert hints[0].handler_qualified_name == "app.routes.show"


def test_flask_middleware_kind():
    sym = _sym("require_auth", "app.routes.require_auth")
    sym.decorators = ["@app.before_request"]
    hints = _run("python.flask.middleware", _extraction([sym]))
    assert hints and hints[0].kind is EntrypointKind.MIDDLEWARE


# ---------------- config-file entrypoints ----------------


def test_scan_config_entrypoints(tmp_path: Path):
    (tmp_path / "serverless.yml").write_text("functions:\n  api:\n    handler: src/app.handler\n")
    (tmp_path / "Procfile").write_text("web: gunicorn app.wsgi:application\n")
    (tmp_path / "Dockerfile").write_text('FROM python\nCMD ["python", "-m", "app.main"]\n')
    hints = scan_config_entrypoints(tmp_path)
    frameworks = {h.framework for h in hints}
    assert {"serverless", "procfile", "docker"} <= frameworks
    lam = next(h for h in hints if h.framework == "serverless")
    assert lam.kind is EntrypointKind.LAMBDA_HANDLER
    assert lam.handler_ref == "src/app.handler"


def test_bind_handler_forms():
    symbols = {"app.handler": 5, "app.main": 6}
    modules = {"app.wsgi": 7}
    assert bind_handler("src/app.handler", symbols, modules) == 5
    assert bind_handler("python -m app.main", symbols, modules) == 6
    assert bind_handler("gunicorn app.wsgi:application", symbols, modules) == 7
    assert bind_handler("uvicorn nonexistent:app", symbols, modules) is None


# ---------------- C6: new-framework entrypoint rules ----------------


def _js_ext(references=(), symbols=(), path="src/app.js"):
    return FileExtraction(
        path=path,
        language="javascript",
        module_path="app",
        parse_ok=True,
        error_count=0,
        symbols=list(symbols),
        references=list(references),
    )


def test_koa_route_rule():
    from entrygraph.detect.entrypoints import rules_for

    ref = RawReference(
        kind="call",
        callee_text="router.get",
        callee_name="get",
        receiver_text="router",
        span=SPAN,
        caller_qualified_name="app.h",
        arg_preview="('/ping', handler)",
    )
    rules = {r.id: r for r in rules_for("javascript", {"koa"})}
    hints = rules["javascript.koa.route"].match(_js_ext([ref]))
    assert hints and hints[0].route == "/ping" and hints[0].framework == "koa"


def test_lambda_js_handler_rule():
    from entrygraph.detect.entrypoints import rules_for

    sym = RawSymbol(
        kind=SymbolKind.FUNCTION,
        name="handler",
        qualified_name="app.handler",
        span=SPAN,
        is_exported=True,
    )
    rules = {r.id: r for r in rules_for("javascript", {"aws-lambda-js"})}
    hints = rules["javascript.aws-lambda.handler"].match(_js_ext(symbols=[sym]))
    assert hints and hints[0].kind is EntrypointKind.LAMBDA_HANDLER


def test_typer_command_reports_typer_not_click():
    # typer reuses click's decorator shape; the hint must carry framework/rule_id
    # for typer, not be mislabeled as click.
    sym = _sym("serve", "cli.serve")
    sym.decorators = ["@app.command()"]
    hints = _run("python.typer.command", _extraction([sym]), frameworks=frozenset({"typer"}))
    assert hints
    assert hints[0].framework == "typer"
    assert hints[0].rule_id == "python.typer.command"


def test_sidekiq_worker_rule():
    from entrygraph.detect.entrypoints import rules_for

    inc = RawReference(
        kind="call",
        callee_text="include",
        callee_name="include",
        receiver_text=None,
        span=SPAN,
        caller_qualified_name="workers.EmailWorker",
        arg_preview="(Sidekiq::Worker)",
    )
    perform = RawSymbol(
        kind=SymbolKind.METHOD,
        name="perform",
        qualified_name="workers.EmailWorker.perform",
        span=SPAN,
        parent_qualified_name="workers.EmailWorker",
    )
    x = FileExtraction(
        path="workers.rb",
        language="ruby",
        module_path="workers",
        parse_ok=True,
        error_count=0,
        symbols=[perform],
        references=[inc],
    )
    rules = {r.id: r for r in rules_for("ruby", {"sidekiq"})}
    hints = rules["ruby.sidekiq.worker"].match(x)
    assert hints and hints[0].kind is EntrypointKind.TASK


def test_nestjs_decorator_routes_with_controller_prefix():
    from entrygraph.detect.entrypoints import rules_for
    from entrygraph.extract.base import FileContext
    from entrygraph.extract.javascript import JavaScriptExtractor
    from entrygraph.parsing.parsers import parse

    src = (
        b"@Controller('users')\nexport class UsersController {\n"
        b"  @Get(':id')\n  findOne(id) { return this.svc.find(id); }\n"
        b"  @Post()\n  create() {}\n}\n"
    )
    ctx = FileContext(
        path="src/users.controller.ts",
        language="typescript",
        module_path="users.controller",
        source=src,
        is_package=False,
    )
    x = JavaScriptExtractor().extract(parse("typescript", src), ctx)
    rules = {r.id: r for r in rules_for("javascript", {"nestjs"})}
    hints = {h.http_methods[0]: h.route for h in rules["javascript.nestjs.route"].match(x)}
    assert hints["GET"] == "/users/:id"  # controller prefix + method path
    assert hints["POST"] == "/users"
