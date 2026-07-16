from __future__ import annotations

import io
import json

from rich.console import Console

from entrygraph.cli import render


def test_to_json_roundtrips_dataclasses():
    from entrygraph.results import Symbol

    s = Symbol(id=1, kind="class", name="A", qname="pkg.A", file="a.py", start_line=1, end_line=3)
    payload = json.loads(render.to_json([s]))
    assert payload[0]["qname"] == "pkg.A"


def test_console_wide_when_not_tty(monkeypatch):
    # a redirected (non-tty) stream must not truncate long qnames
    con = render.console()
    assert con.width >= 1000


def test_kind_and_method_and_confidence_text():
    assert render.kind_text("class").style == "bold cyan"
    assert render.confidence_text(3).plain == "exact"
    assert render.confidence_text(0).plain == "unresolved"
    # multi-method http verbs render each part
    assert render.method_text("GET,POST").plain == "GET,POST"


def test_table_renders_full_qname_without_truncation():
    tbl = render.table("Symbols")
    tbl.add_column("QNAME", no_wrap=True)
    long_qname = "tests.fixtures.java.spring_app.com.example.UserController.getUser"
    tbl.add_row(long_qname)
    buf = io.StringIO()
    Console(file=buf, width=render._PIPE_WIDTH).print(tbl)
    assert long_qname in buf.getvalue()
