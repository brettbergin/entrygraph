"""Source/sink pattern registry.

Built-in catalogs ship as TOML package data (entrygraph/data/sinks/*.toml) in
the same schema users write in a repo-root ``entrygraph.toml`` — one loader,
diffable catalogs. Patterns are language-prefixed qualified-name globs with
brace expansion, e.g. ``py:subprocess.{run,call,Popen}``; they are compiled to
one alternation regex per registry and matched against each call edge's
canonical callee (dst_qname) at index time, stamping ``edges.sink_id``.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files as resource_files
from pathlib import Path

_SINK_LANGS = ("python", "javascript", "go", "java", "ruby")


@dataclass(frozen=True, slots=True)
class SinkPattern:
    id: str
    category: str
    callee: str  # brace-glob over canonical qnames
    severity: str = "medium"
    description: str = ""
    require_arg_hint: str | None = None  # regex against arg_preview to cut noise


@dataclass(frozen=True, slots=True)
class SourcePattern:
    id: str
    category: str
    callee: str
    description: str = ""


def expand_braces(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end():]
    expanded: list[str] = []
    for option in match.group(1).split(","):
        expanded.extend(expand_braces(head + option.strip() + tail))
    return expanded


class SinkRegistry:
    def __init__(self, sinks: list[SinkPattern], sources: list[SourcePattern]) -> None:
        self.sinks = {s.id: s for s in sinks}
        self.sources = {s.id: s for s in sources}
        self._compiled: list[tuple[re.Pattern, SinkPattern]] = []
        for sink in sinks:
            alternatives = [fnmatch.translate(g) for g in expand_braces(sink.callee)]
            self._compiled.append((re.compile("|".join(alternatives)), sink))

    def match(self, canonical_callee: str, arg_preview: str | None = None) -> str | None:
        """Return the first matching sink id, or None."""
        for regex, sink in self._compiled:
            if regex.match(canonical_callee):
                if sink.require_arg_hint and not (
                    arg_preview and re.search(sink.require_arg_hint, arg_preview)
                ):
                    continue
                return sink.id
        return None

    def ids_for_category(self, category: str) -> set[str]:
        return {s.id for s in self.sinks.values() if s.category == category}

    def merged_with(self, sinks: list[SinkPattern], sources: list[SourcePattern],
                    disable: list[str] | None = None) -> "SinkRegistry":
        kept = [s for s in self.sinks.values() if s.id not in set(disable or [])]
        return SinkRegistry([*kept, *sinks], [*self.sources.values(), *sources])


def _load_toml(text: str) -> tuple[list[SinkPattern], list[SourcePattern], list[str]]:
    data = tomllib.loads(text)
    sinks = [
        SinkPattern(
            id=raw["id"],
            category=raw["category"],
            callee=raw["callee"],
            severity=raw.get("severity", "medium"),
            description=raw.get("description", ""),
            require_arg_hint=raw.get("require_arg_hint"),
        )
        for raw in data.get("sink", [])
    ]
    sources = [
        SourcePattern(
            id=raw["id"],
            category=raw["category"],
            callee=raw["callee"],
            description=raw.get("description", ""),
        )
        for raw in data.get("source", [])
    ]
    return sinks, sources, list(data.get("disable", []))


@cache
def builtin_registry() -> SinkRegistry:
    sinks: list[SinkPattern] = []
    sources: list[SourcePattern] = []
    data_dir = resource_files("entrygraph") / "data" / "sinks"
    for lang in _SINK_LANGS:
        candidate = data_dir / f"{lang}.toml"
        try:
            text = candidate.read_text()
        except FileNotFoundError:
            continue
        lang_sinks, lang_sources, _ = _load_toml(text)
        sinks.extend(lang_sinks)
        sources.extend(lang_sources)
    return SinkRegistry(sinks, sources)


_user_sinks: list[SinkPattern] = []
_user_sources: list[SourcePattern] = []


def register_sink(sink: SinkPattern) -> None:
    """Library-embedder extension point; applies to subsequent index runs."""
    _user_sinks.append(sink)


def register_source(source: SourcePattern) -> None:
    _user_sources.append(source)


def registry_for_repo(root: str | Path | None = None) -> SinkRegistry:
    """Built-ins + Python-registered patterns + repo-root entrygraph.toml."""
    registry = builtin_registry()
    extra_sinks = list(_user_sinks)
    extra_sources = list(_user_sources)
    disable: list[str] = []
    if root is not None:
        config = Path(root) / "entrygraph.toml"
        if config.is_file():
            try:
                file_sinks, file_sources, disable = _load_toml(config.read_text())
                extra_sinks.extend(file_sinks)
                extra_sources.extend(file_sources)
            except (tomllib.TOMLDecodeError, KeyError):
                pass
    if extra_sinks or extra_sources or disable:
        return registry.merged_with(extra_sinks, extra_sources, disable)
    return registry
