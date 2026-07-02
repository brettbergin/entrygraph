"""Entrypoint rule schema and registry.

Rules are IR-driven: they match against a FileExtraction (symbols, decorators,
references, framework signals) rather than re-walking the tree, which keeps
them cheap and language-uniform. A rule gated on a framework only runs when
that framework was detected above threshold — `app.get("/x")` in random code
is not an Express route.

Adding a framework = registering one rule (and usually one FrameworkSpec).
Third parties: entrygraph.detect.entrypoints.register(rule).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from entrygraph.extract.ir import EntrypointHint, FileExtraction
from entrygraph.kinds import EntrypointKind

Matcher = Callable[[FileExtraction], list[EntrypointHint]]


@dataclass(frozen=True, slots=True)
class EntrypointRule:
    id: str  # "python.flask.route"
    language: str
    framework: str | None  # None => language-core rule, always runs
    kind: EntrypointKind
    match: Matcher


_RULES: list[EntrypointRule] = []


def register(rule: EntrypointRule) -> None:
    _RULES.append(rule)


# TypeScript/TSX reuse the JavaScript rule set (one extractor drives all three).
_LANG_ALIASES = {"typescript": "javascript", "tsx": "javascript"}


def rules_for(language: str, detected_frameworks: set[str]) -> list[EntrypointRule]:
    lang = _LANG_ALIASES.get(language, language)
    return [
        r
        for r in _RULES
        if r.language == lang and (r.framework is None or r.framework in detected_frameworks)
    ]


def all_rules() -> list[EntrypointRule]:
    return list(_RULES)


# ---------------- shared decorator-parsing helpers ----------------

_STRING_ARG = re.compile(r"""\(\s*[rbf]*["']([^"']+)["']""")
_METHODS_KWARG = re.compile(r"""methods\s*=\s*[\[(]([^\])]*)[\])]""")
_QUOTED = re.compile(r"""["']([^"']+)["']""")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_PARAMS = re.compile(r"\(([^)]*)\)")

# names conventionally carrying user-controlled input across web frameworks
_TAINTED_NAMES = frozenset(
    {"request", "req", "params", "event", "body", "payload", "query", "form", "data"}
)


def first_string_arg(decorator_text: str) -> str | None:
    match = _STRING_ARG.search(decorator_text)
    return match.group(1) if match else None


def methods_kwarg(decorator_text: str) -> list[str]:
    match = _METHODS_KWARG.search(decorator_text)
    if not match:
        return []
    return [m.upper() for m in _QUOTED.findall(match.group(1))]


def identifier_args(arg_preview: str | None) -> list[str]:
    """Bare identifiers appearing as call arguments (best-effort over the preview).

    Skips string/number literals and keyword-argument names before '='.
    """
    if not arg_preview:
        return []
    names: list[str] = []
    for part in arg_preview.strip("()").split(","):
        token = part.strip()
        if "=" in token:  # keyword arg: take the value side only
            token = token.split("=", 1)[1].strip()
        if token and _IDENTIFIER.fullmatch(token):
            names.append(token)
    return names


def tainted_params(signature: str | None, kind: str) -> list[str]:
    """Parameters of an entrypoint handler that are likely user-controlled.

    HTTP handlers: every parameter except self/cls (path/query params flow in).
    Lambda handlers: the `event` parameter. Otherwise: parameters whose names
    match the conventional tainted-name set.
    """
    if not signature:
        return []
    match = _PARAMS.search(signature)
    if not match:
        return []
    raw = [p.strip() for p in match.group(1).split(",") if p.strip()]
    params = []
    for p in raw:
        name = p.split(":", 1)[0].split("=", 1)[0].strip().lstrip("*")
        if name and name not in ("self", "cls"):
            params.append(name)
    if kind == "http_route":
        return params
    if kind == "lambda_handler":
        return [p for p in params if p == "event"] or params[:1]
    return [p for p in params if p in _TAINTED_NAMES]
