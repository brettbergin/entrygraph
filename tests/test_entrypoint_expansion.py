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
    return RawSymbol(kind=kind, name=name, qualified_name=qname, span=SPAN,
                     signature=signature, decorators=[])


def _extraction(symbols=(), references=(), path="app/routes.py", module="app.routes"):
    return FileExtraction(path=path, language="python", module_path=module,
                          parse_ok=True, error_count=0, symbols=list(symbols),
                          references=list(references))


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

def _run(rule_id, x, frameworks={"flask", "fastapi"}):
    rules = {r.id: r for r in rules_for("python", frameworks)}
    return rules[rule_id].match(x)


def test_flask_route_carries_tainted_params():
    sym = _sym("create_report", "app.routes.create_report",
               signature="def create_report(name, request):")
    sym.decorators = ["@app.route('/reports', methods=['POST'])"]
    hints = _run("python.flask.route", _extraction([sym]))
    assert hints and hints[0].metadata["tainted_params"] == ["name", "request"]


def test_flask_add_url_rule():
    handler = _sym("show", "app.routes.show")
    ref = RawReference(kind="call", callee_text="app.add_url_rule", callee_name="add_url_rule",
                       receiver_text="app", span=SPAN, caller_qualified_name=None,
                       arg_preview="('/show', view_func=show)")
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
    (tmp_path / "serverless.yml").write_text(
        "functions:\n  api:\n    handler: src/app.handler\n"
    )
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
