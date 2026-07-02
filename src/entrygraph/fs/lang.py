"""Language detection: extension map, filename map, shebang sniff.

Deliberately dependency-free. Recognizes more languages than we extract so
repo language stats are honest; ``EXTRACTABLE`` marks the ones with extractors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

EXTRACTABLE = frozenset(
    {"python", "javascript", "typescript", "tsx", "go", "java", "ruby",
     "csharp", "php", "rust"}
)

_EXTENSION_MAP = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".rake": "ruby",
    ".gemspec": "ruby",
    # recognized but not extracted — kept for honest stats
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rs": "rust",
    ".kt": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".phtml": "php",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".rst": "restructuredtext",
    ".xml": "xml",
    ".tf": "hcl",
    ".proto": "protobuf",
}

_FILENAME_MAP = {
    "Rakefile": "ruby",
    "Gemfile": "ruby",
    "Guardfile": "ruby",
    "Vagrantfile": "ruby",
    "Jenkinsfile": "groovy",
    "Dockerfile": "dockerfile",
    "Makefile": "make",
    "BUILD": "starlark",
    "WORKSPACE": "starlark",
}

_SHEBANG_RE = re.compile(rb"^#!\s*\S*?\b(?:env\s+)?(python3?|node|ruby)\b")
_SHEBANG_LANG = {b"python": "python", b"python3": "python", b"node": "javascript", b"ruby": "ruby"}


def detect_language(path: str, first_line: bytes | None = None) -> str | None:
    """Detect the language of a repo-relative file path.

    ``first_line`` (if provided) enables shebang sniffing for extensionless
    executables; callers that have not read the file may omit it.
    """
    p = PurePosixPath(path)
    lang = _EXTENSION_MAP.get(p.suffix.lower())
    if lang:
        return lang
    lang = _FILENAME_MAP.get(p.name)
    if lang:
        return lang
    if not p.suffix and first_line:
        match = _SHEBANG_RE.match(first_line)
        if match:
            return _SHEBANG_LANG.get(match.group(1))
    return None


@dataclass(frozen=True, slots=True)
class LanguageStat:
    name: str
    file_count: int
    byte_count: int
    percent: float


class RepoLanguageProfile:
    """Accumulates per-language file/byte counts during a walk."""

    def __init__(self) -> None:
        self._counts: dict[str, list[int]] = {}  # lang -> [files, bytes]

    def add(self, language: str | None, size_bytes: int) -> None:
        if language is None:
            return
        entry = self._counts.setdefault(language, [0, 0])
        entry[0] += 1
        entry[1] += size_bytes

    def stats(self) -> list[LanguageStat]:
        total = sum(b for _, b in self._counts.values()) or 1
        return sorted(
            (
                LanguageStat(name=lang, file_count=f, byte_count=b, percent=100.0 * b / total)
                for lang, (f, b) in self._counts.items()
            ),
            key=lambda s: s.byte_count,
            reverse=True,
        )

    def extractable_languages(self) -> set[str]:
        return {lang for lang in self._counts if lang in EXTRACTABLE}
