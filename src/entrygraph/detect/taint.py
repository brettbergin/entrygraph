"""Source/sink/sanitizer pattern registry.

Built-in catalogs ship as TOML package data — every ``entrygraph/data/sinks/
*.toml`` file is loaded (per-language ``<lang>.toml`` plus ``lib_*.toml``
third-party wrapper summaries) — in the same schema users write in a repo-root
``entrygraph.toml``. Patterns are language-prefixed qualified-name globs with
brace expansion, e.g. ``py:subprocess.{run,call,Popen}``; they are compiled to
one alternation regex per registry and matched against each call edge's
canonical callee (dst_qname) at index time, stamping ``edges.sink_id``.

Sanitizers are matched at query time (not index time) so retuning them never
requires a re-index: a path passing through a registered sanitizer for the
sink's category is downgraded (``effect="reduces"``) or pruned
(``effect="neutralizes"``).
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files as resource_files
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SinkPattern:
    id: str
    category: str
    callee: str  # brace-glob over canonical qnames
    severity: str = "medium"
    description: str = ""
    require_arg_hint: str | None = None  # regex against arg_preview to cut noise
    library: str | None = None  # third-party wrapper this summarizes (informational)


@dataclass(frozen=True, slots=True)
class SourcePattern:
    id: str
    category: str
    callee: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class SanitizerPattern:
    """A call that neutralizes or reduces taint for a sink category.

    `effect="neutralizes"` means the value is considered safe downstream (paths
    through it can be pruned); `effect="reduces"` only discounts the risk score.
    """

    id: str
    category: str
    callee: str  # brace-glob over canonical qnames
    effect: str = "reduces"  # "neutralizes" | "reduces"
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


def _compile(callee: str) -> re.Pattern:
    return re.compile("|".join(fnmatch.translate(g) for g in expand_braces(callee)))


class SinkRegistry:
    def __init__(
        self,
        sinks: list[SinkPattern],
        sources: list[SourcePattern],
        sanitizers: list[SanitizerPattern] | None = None,
    ) -> None:
        self.sinks = {s.id: s for s in sinks}
        self.sources = {s.id: s for s in sources}
        self.sanitizers = {s.id: s for s in (sanitizers or [])}
        self._compiled = [(_compile(s.callee), s) for s in sinks]
        self._compiled_sanitizers = [(_compile(s.callee), s) for s in (sanitizers or [])]

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

    def match_sanitizers(self, canonical_callee: str) -> list[SanitizerPattern]:
        """Sanitizers whose pattern matches this callee qname."""
        return [s for regex, s in self._compiled_sanitizers if regex.match(canonical_callee)]

    def sanitizers_for_category(self, category: str) -> list[SanitizerPattern]:
        return [s for s in self.sanitizers.values() if s.category == category]

    def ids_for_category(self, category: str) -> set[str]:
        return {s.id for s in self.sinks.values() if s.category == category}

    def severity_of(self, sink_id: str | None) -> str | None:
        sink = self.sinks.get(sink_id) if sink_id else None
        return sink.severity if sink else None

    def merged_with(
        self,
        sinks: list[SinkPattern],
        sources: list[SourcePattern],
        disable: list[str] | None = None,
        sanitizers: list[SanitizerPattern] | None = None,
    ) -> "SinkRegistry":
        disabled = set(disable or [])
        kept = [s for s in self.sinks.values() if s.id not in disabled]
        kept_san = [s for s in self.sanitizers.values() if s.id not in disabled]
        return SinkRegistry(
            [*kept, *sinks],
            [*self.sources.values(), *sources],
            [*kept_san, *(sanitizers or [])],
        )


def _load_toml(
    text: str,
) -> tuple[list[SinkPattern], list[SourcePattern], list[SanitizerPattern], list[str]]:
    data = tomllib.loads(text)
    sinks = [
        SinkPattern(
            id=raw["id"],
            category=raw["category"],
            callee=raw["callee"],
            severity=raw.get("severity", "medium"),
            description=raw.get("description", ""),
            require_arg_hint=raw.get("require_arg_hint"),
            library=raw.get("library"),
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
    sanitizers = [
        SanitizerPattern(
            id=raw["id"],
            category=raw["category"],
            callee=raw["callee"],
            effect=raw.get("effect", "reduces"),
            description=raw.get("description", ""),
        )
        for raw in data.get("sanitizer", [])
    ]
    return sinks, sources, sanitizers, list(data.get("disable", []))


@cache
def builtin_registry() -> SinkRegistry:
    """All shipped catalogs: data/sinks/*.toml (per-language + lib_* summaries)."""
    sinks: list[SinkPattern] = []
    sources: list[SourcePattern] = []
    sanitizers: list[SanitizerPattern] = []
    data_dir = resource_files("entrygraph") / "data" / "sinks"
    for entry in sorted(data_dir.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".toml"):
            continue
        s, src, san, _ = _load_toml(entry.read_text())
        sinks.extend(s)
        sources.extend(src)
        sanitizers.extend(san)
    return SinkRegistry(sinks, sources, sanitizers)


_user_sinks: list[SinkPattern] = []
_user_sources: list[SourcePattern] = []
_user_sanitizers: list[SanitizerPattern] = []


def register_sink(sink: SinkPattern) -> None:
    """Library-embedder extension point; applies to subsequent index runs."""
    _user_sinks.append(sink)


def register_source(source: SourcePattern) -> None:
    _user_sources.append(source)


def register_sanitizer(sanitizer: SanitizerPattern) -> None:
    _user_sanitizers.append(sanitizer)


def registry_for_repo(root: str | Path | None = None) -> SinkRegistry:
    """Built-ins + Python-registered patterns + repo-root entrygraph.toml."""
    registry = builtin_registry()
    extra_sinks = list(_user_sinks)
    extra_sources = list(_user_sources)
    extra_sanitizers = list(_user_sanitizers)
    disable: list[str] = []
    if root is not None:
        config = Path(root) / "entrygraph.toml"
        if config.is_file():
            try:
                file_sinks, file_sources, file_san, disable = _load_toml(config.read_text())
                extra_sinks.extend(file_sinks)
                extra_sources.extend(file_sources)
                extra_sanitizers.extend(file_san)
            except (tomllib.TOMLDecodeError, KeyError):
                pass
    if extra_sinks or extra_sources or extra_sanitizers or disable:
        return registry.merged_with(extra_sinks, extra_sources, disable, extra_sanitizers)
    return registry
