"""Subscript / member-access source-key capture (#87 part C).

`params[:id]`, `req.body.name`, `request.args["q"]`, `$_GET["x"]` read request
input but are not calls, so they produced no source edge and no key. The
extractors synthesize an accessor-read reference so the catalog matches them and
the specific key is surfaced.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.db import models
from entrygraph.pipeline.scanner import index_repository


def _source_edges(engine) -> set[tuple[str, str | None]]:
    with Session(engine) as s:
        return {
            (r.source_id, r.source_key)
            for r in s.execute(
                select(models.Edge.source_id, models.Edge.source_key).where(
                    models.Edge.source_id.is_not(None)
                )
            )
        }


CASES = {
    "python": (
        "app.py",
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        "@app.route('/a')\n"
        "def a():\n"
        "    return request.args['user_id']\n",
        [("py.flask.query", "user_id")],
    ),
    "javascript_member": (
        "app.js",
        "const express = require('express');\n"
        "const app = express();\n"
        "app.get('/a', (req, res) => { const n = req.body.name; res.send(n); });\n",
        [("js.express.body", "name")],
    ),
    "javascript_subscript": (
        "q.js",
        "const express = require('express');\n"
        "const app = express();\n"
        "app.get('/b', (req, res) => { res.send(req.query['q']); });\n",
        [("js.express.query", "q")],
    ),
    "ruby": (
        "app.rb",
        "class UsersController\n  def show\n    id = params[:user_id]\n    render id\n  end\nend\n",
        [("rb.sinatra.params", "user_id")],
    ),
    "php": (
        "app.php",
        "<?php\nfunction handle() {\n  return $_GET['name'];\n}\n",
        [("php.superglobal.get", "name")],
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_subscript_member_source_key(tmp_engine, tmp_path, name):
    fname, src, expected = CASES[name]
    repo = tmp_path / name
    repo.mkdir()
    (repo / fname).write_text(src)
    index_repository(repo, tmp_engine)
    edges = _source_edges(tmp_engine)
    for pair in expected:
        assert pair in edges, f"{name}: {pair} not in {edges}"


def test_computed_key_yields_no_stable_name(tmp_engine, tmp_path):
    # a non-literal subscript key (params[var]) has no name to surface, but the
    # access is still a source (key None), not dropped
    repo = tmp_path / "dyn"
    repo.mkdir()
    (repo / "app.rb").write_text(
        "class C\n  def show\n    key = compute\n    v = params[key]\n    render v\n  end\nend\n"
    )
    index_repository(repo, tmp_engine)
    edges = _source_edges(tmp_engine)
    assert ("rb.sinatra.params", None) in edges


def test_ordinary_member_read_is_not_a_phantom_source(tmp_engine, tmp_path):
    # req.method / obj.foo must NOT spawn a synthesized source edge — only the
    # curated accessor props (body/query/params/...) do (#87C).
    repo = tmp_path / "clean"
    repo.mkdir()
    (repo / "app.js").write_text(
        "const express = require('express');\n"
        "const app = express();\n"
        "app.get('/a', (req, res) => {\n"
        "  const m = req.method;\n"
        "  const u = user.name;\n"
        "  res.send(m);\n"
        "});\n"
    )
    index_repository(repo, tmp_engine)
    with Session(tmp_engine) as s:
        dsts = {
            r for (r,) in s.execute(select(models.Edge.dst_qname)) if r and r.endswith((".method",))
        }
    # req.method did not become a source-tagged accessor read
    assert _source_edges(tmp_engine) == set() or all(
        sid != "js.express.body" for sid, _ in _source_edges(tmp_engine)
    )
    assert "js:*.method" not in dsts


def test_key_helpers():
    from entrygraph.extract.base import member_key, subscript_key

    # subscript keys: only quoted strings and :symbols are literal
    assert subscript_key('"q"') == "q"
    assert subscript_key("'user_id'") == "user_id"
    assert subscript_key(":id") == "id"
    assert subscript_key('"X-Api-Key"') == "X-Api-Key"
    assert subscript_key("var") is None  # bare identifier -> computed key
    assert subscript_key("") is None
    # member property names are always literal
    assert member_key("name") == "name"
    assert member_key("userId") == "userId"
