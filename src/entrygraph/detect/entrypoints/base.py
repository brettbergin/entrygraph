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
from dataclasses import dataclass
from typing import Callable

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


def rules_for(language: str, detected_frameworks: set[str]) -> list[EntrypointRule]:
    return [
        r
        for r in _RULES
        if r.language == language
        and (r.framework is None or r.framework in detected_frameworks)
    ]


def all_rules() -> list[EntrypointRule]:
    return list(_RULES)


# ---------------- shared decorator-parsing helpers ----------------

_STRING_ARG = re.compile(r"""\(\s*[rbf]*["']([^"']+)["']""")
_METHODS_KWARG = re.compile(r"""methods\s*=\s*[\[(]([^\])]*)[\])]""")
_QUOTED = re.compile(r"""["']([^"']+)["']""")


def first_string_arg(decorator_text: str) -> str | None:
    match = _STRING_ARG.search(decorator_text)
    return match.group(1) if match else None


def methods_kwarg(decorator_text: str) -> list[str]:
    match = _METHODS_KWARG.search(decorator_text)
    if not match:
        return []
    return [m.upper() for m in _QUOTED.findall(match.group(1))]
