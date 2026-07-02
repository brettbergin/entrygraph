"""Rich-powered output rendering for the CLI, plus plain JSON.

Human-readable output goes through Rich (colored tables, trees, panels); the
``--json`` path stays plain so piped/consumed output is untouched. Consoles are
created per call so they bind to the current ``sys.stdout`` (important under
pytest's capture) and, when output is not a TTY, use a very wide virtual width
so long qualified names are never truncated or wrapped mid-token.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any, Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

# Width used when stdout is redirected (pipe/file/capture): large enough that
# Rich never truncates a cell with an ellipsis or folds a qname.
_PIPE_WIDTH = 10_000


def console(*, stderr: bool = False) -> Console:
    """A fresh Console bound to the current stream."""
    stream = sys.stderr if stderr else sys.stdout
    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    return Console(
        file=stream,
        stderr=stderr,
        highlight=False,
        soft_wrap=not is_tty,
        width=None if is_tty else _PIPE_WIDTH,
    )


# ---------------- JSON (unchanged, dependency-free) ----------------

def to_json(obj: Any) -> str:
    def default(o):
        if is_dataclass(o):
            return asdict(o)
        raise TypeError(f"not serializable: {type(o)}")

    return json.dumps(obj, default=default, indent=2)


# ---------------- styling helpers ----------------

_KIND_STYLE = {
    "class": "bold cyan", "interface": "cyan", "struct": "cyan",
    "function": "green", "method": "green",
    "module": "blue", "variable": "yellow", "constant": "yellow",
    "field": "yellow", "property": "yellow", "external": "dim red",
}

_METHOD_STYLE = {
    "GET": "green", "POST": "yellow", "PUT": "blue", "PATCH": "blue",
    "DELETE": "red", "OPTIONS": "dim", "HEAD": "dim", "*": "magenta",
}

_ENTRYPOINT_STYLE = {
    "http_route": "green", "cli_command": "cyan", "main": "blue",
    "task": "magenta", "lambda_handler": "yellow", "event_handler": "yellow",
    "middleware": "dim yellow",
}

_CONFIDENCE_NAME = {0: "unresolved", 1: "fuzzy", 2: "import", 3: "exact"}
_CONFIDENCE_STYLE = {0: "dim red", 1: "yellow", 2: "green", 3: "bold green"}


def kind_text(kind: str | None) -> Text:
    return Text(kind or "", style=_KIND_STYLE.get(kind or "", ""))


def method_text(method: str | None) -> Text:
    if not method:
        return Text("")
    parts = method.split(",")
    text = Text()
    for i, m in enumerate(parts):
        if i:
            text.append(",", style="dim")
        text.append(m, style=_METHOD_STYLE.get(m.strip().upper(), ""))
    return text


def entrypoint_kind_text(kind: str | None) -> Text:
    return Text(kind or "", style=_ENTRYPOINT_STYLE.get(kind or "", ""))


def confidence_text(value: int) -> Text:
    return Text(_CONFIDENCE_NAME.get(value, str(value)),
                style=_CONFIDENCE_STYLE.get(value, ""))


def risk_style(risk: float | None) -> str:
    """Color a risk score: red (high) -> yellow -> green (low)."""
    if risk is None:
        return "dim"
    if risk >= 0.66:
        return "bold red"
    if risk >= 0.33:
        return "yellow"
    return "green"


def risk_text(risk: float | None) -> Text:
    if risk is None:
        return Text("—", style="dim")
    return Text(f"{risk:.2f}", style=risk_style(risk))


# ---------------- table helper ----------------

def table(title: str | None = None, *, caption: str | None = None) -> Table:
    """A consistently-styled Rich table. Header cells are upper-cased."""
    return Table(
        title=title,
        caption=caption,
        title_style="bold",
        header_style="bold white",
        border_style="dim",
        expand=False,
        pad_edge=False,
    )


def cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
