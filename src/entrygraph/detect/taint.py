"""Source/sink pattern registry.

Built-in catalogs ship as TOML package data — every ``entrygraph/data/sinks/
*.toml`` file is loaded (per-language ``<lang>.toml`` plus ``lib_*.toml``
third-party wrapper summaries) — in the same schema users write in a repo-root
``entrygraph.toml``. Patterns are language-prefixed qualified-name globs with
brace expansion, e.g. ``py:subprocess.{run,call,Popen}``; each is compiled to a
regex, bucketed by language prefix, and matched against each call edge's
canonical callee (dst_qname) at index time, stamping ``edges.sink_id``. Bucketing
means a ``py:`` callee only tests ``py:`` patterns, not the whole catalog.
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
    channel: str | None = None  # http_input: query|path|header|cookie|body|form


def expand_braces(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    expanded: list[str] = []
    for option in match.group(1).split(","):
        expanded.extend(expand_braces(head + option.strip() + tail))
    return expanded


def _compile(callee: str) -> re.Pattern:
    return re.compile("|".join(fnmatch.translate(g) for g in expand_braces(callee)))


def _prefix_of(callee: str) -> str:
    """Language prefix of a callee glob/qname (`py:subprocess.run` -> `py`)."""
    return callee.split(":", 1)[0] if ":" in callee else ""


def _bucket(compiled: list) -> dict[str, list]:
    """Group (regex, pattern) pairs by language prefix, preserving order."""
    buckets: dict[str, list] = {}
    for regex, pat in compiled:
        buckets.setdefault(_prefix_of(pat.callee), []).append((regex, pat))
    return buckets


def _candidates(buckets: dict[str, list], canonical_callee: str) -> list:
    """Compiled patterns that could match this callee: its language bucket plus
    any prefix-less (catch-all) patterns, in registration order. A `py:` callee
    never tests `js:`/`go:`/... patterns, cutting the per-edge scan from all
    patterns to one language's worth."""
    prefix = _prefix_of(canonical_callee)
    specific = buckets.get(prefix, [])
    if prefix == "":
        return specific  # the catch-all bucket itself
    catchall = buckets.get("", [])
    if not catchall:
        return specific
    if not specific:
        return catchall
    return [*specific, *catchall]


class SinkRegistry:
    def __init__(
        self,
        sinks: list[SinkPattern],
        sources: list[SourcePattern],
    ) -> None:
        self.sinks = {s.id: s for s in sinks}
        self.sources = {s.id: s for s in sources}
        self._sinks_by_prefix = _bucket([(_compile(s.callee), s) for s in sinks])
        self._sources_by_prefix = _bucket([(_compile(s.callee), s) for s in sources])

    def match(self, canonical_callee: str, arg_preview: str | None = None) -> str | None:
        """Return the first matching sink id, or None."""
        for regex, sink in _candidates(self._sinks_by_prefix, canonical_callee):
            if regex.match(canonical_callee):
                if sink.require_arg_hint and not (
                    arg_preview and re.search(sink.require_arg_hint, arg_preview)
                ):
                    continue
                return sink.id
        return None

    def match_source(self, canonical_callee: str) -> str | None:
        """Return the first matching taint-source id for this callee, or None.

        A call to a source function (e.g. ``flask.request.args.get``, ``os.getenv``)
        marks its calling site as a taint origin. Stamped on edges at index time,
        symmetric to :meth:`match`."""
        for regex, source in _candidates(self._sources_by_prefix, canonical_callee):
            if regex.match(canonical_callee):
                return source.id
        return None

    def ids_for_category(self, category: str) -> set[str]:
        # "all" -> every tagged sink, regardless of category (any-sink queries)
        if category == "all":
            return set(self.sinks)
        return {s.id for s in self.sinks.values() if s.category == category}

    def source_ids_for_category(self, category: str) -> set[str]:
        if category == "all":
            return set(self.sources)
        return {s.id for s in self.sources.values() if s.category == category}

    def sink_categories(self) -> list[str]:
        """Sorted distinct sink categories in this registry (for validation/help)."""
        return sorted({s.category for s in self.sinks.values()})

    def source_categories(self) -> list[str]:
        """Sorted distinct source categories in this registry (for validation/help)."""
        return sorted({s.category for s in self.sources.values()})

    def severity_of(self, sink_id: str | None) -> str | None:
        sink = self.sinks.get(sink_id) if sink_id else None
        return sink.severity if sink else None

    def merged_with(
        self,
        sinks: list[SinkPattern],
        sources: list[SourcePattern],
        disable: list[str] | None = None,
    ) -> SinkRegistry:
        disabled = set(disable or [])
        kept = [s for s in self.sinks.values() if s.id not in disabled]
        return SinkRegistry([*kept, *sinks], [*self.sources.values(), *sources])


def _load_toml(
    text: str,
) -> tuple[list[SinkPattern], list[SourcePattern], list[str]]:
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
            channel=raw.get("channel"),
        )
        for raw in data.get("source", [])
    ]
    return sinks, sources, list(data.get("disable", []))


@cache
def builtin_registry() -> SinkRegistry:
    """All shipped catalogs: data/sinks/*.toml (per-language + lib_* summaries)."""
    sinks: list[SinkPattern] = []
    sources: list[SourcePattern] = []
    data_dir = resource_files("entrygraph") / "data" / "sinks"
    for entry in sorted(data_dir.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".toml"):
            continue
        s, src, _ = _load_toml(entry.read_text())
        sinks.extend(s)
        sources.extend(src)
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


# ---------------- catalog coverage (#95) ----------------

# canonical-callee language prefixes -> fs.lang language names
_PREFIX_LANGUAGES: dict[str, tuple[str, ...]] = {
    "py": ("python",),
    "js": ("javascript", "typescript", "tsx"),
    "go": ("go",),
    "java": ("java",),
    "rb": ("ruby",),
    "cs": ("csharp",),
    "php": ("php",),
    "rs": ("rust",),
}

LANGUAGE_PREFIX: dict[str, str] = {
    lang: prefix for prefix, langs in _PREFIX_LANGUAGES.items() for lang in langs
}

# Extractable languages with no call semantics: pure declarations (GraphQL SDL
# type/field definitions). They can never have sink/source catalog entries, so
# the every-extractable-language-has-a-catalog guard exempts them.
DECLARATION_ONLY_LANGUAGES: frozenset[str] = frozenset({"graphql"})


@dataclass(frozen=True, slots=True)
class CatalogCoverage:
    """How much taint catalog backs one language — descriptive counts + a coarse
    tier, so absence-of-findings can be read honestly (#95)."""

    sinks: int
    sources: int
    sink_categories: tuple[str, ...]
    tier: str  # "full" | "partial" | "minimal"


def _tier(sinks: int, sources: int) -> str:
    # Stable, deliberately coarse thresholds calibrated to the shipped catalog
    # spread (rust 7 sinks .. python 16): a tier shift on a catalog edit should
    # mean the coverage story actually changed.
    if sinks < 9 or sources < 1:
        return "minimal"
    if sinks >= 12 and sources >= 2:
        return "full"
    return "partial"


def catalog_coverage(registry: SinkRegistry) -> dict[str, CatalogCoverage]:
    """Per-language pattern counts for this registry, keyed by language name.

    Languages sharing a callee prefix (typescript/tsx ride `js:`) each get an
    entry so callers can join on `fs.lang` names directly.
    """
    by_prefix: dict[str, dict[str, list]] = {}
    for kind, patterns in (
        ("sinks", registry.sinks.values()),
        ("sources", registry.sources.values()),
    ):
        for pat in patterns:
            prefix = _prefix_of(pat.callee)
            by_prefix.setdefault(prefix, {"sinks": [], "sources": []})[kind].append(pat)

    coverage: dict[str, CatalogCoverage] = {}
    for prefix, groups in by_prefix.items():
        for language in _PREFIX_LANGUAGES.get(prefix, ()):
            coverage[language] = CatalogCoverage(
                sinks=len(groups["sinks"]),
                sources=len(groups["sources"]),
                sink_categories=tuple(sorted({s.category for s in groups["sinks"]})),
                tier=_tier(len(groups["sinks"]), len(groups["sources"])),
            )
    return coverage
