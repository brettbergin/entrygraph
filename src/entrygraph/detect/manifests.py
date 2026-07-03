"""Dependency-manifest parsers — stdlib + regex only.

Each parser takes file text and returns a set of normalized dependency names.
``parse_manifests(root)`` walks the well-known manifest locations and merges
results per ecosystem.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_GO_REQUIRE_BLOCK = re.compile(r"require\s*\(([^)]*)\)", re.DOTALL)
_GO_REQUIRE_LINE = re.compile(r"^\s*require\s+(\S+)", re.MULTILINE)
_GO_MODULE_LINE = re.compile(r"^\s*(\S+)\s+v[\w.\-+]+", re.MULTILINE)
_GRADLE_DEP = re.compile(
    r"""(?:implementation|api|compile|runtimeOnly|compileOnly|testImplementation)\s*[\(\s]\s*['"]([^'"]+)['"]"""
)
_GEMFILE_GEM = re.compile(r"""^\s*gem\s+['"]([^'"]+)['"]""", re.MULTILINE)


def parse_requirements_txt(text: str) -> set[str]:
    deps: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _REQ_LINE.match(line)
        if match:
            deps.add(match.group(1).lower().replace("_", "-"))
    return deps


def parse_pyproject_toml(text: str) -> set[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    deps: set[str] = set()
    project = data.get("project", {})
    raw: list[str] = list(project.get("dependencies", []) or [])
    for extra_deps in (project.get("optional-dependencies", {}) or {}).values():
        raw.extend(extra_deps or [])
    for spec in raw:
        match = _REQ_LINE.match(spec)
        if match:
            deps.add(match.group(1).lower().replace("_", "-"))
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
    deps.update(k.lower().replace("_", "-") for k in poetry if k.lower() != "python")
    return deps


def parse_package_json(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(data, dict):
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update(section)
    return deps


def parse_go_mod(text: str) -> set[str]:
    # `// indirect` requires are transitive deps the module doesn't use directly, so
    # they aren't evidence the repo uses that framework (gitea's gorilla/mux and
    # grpc were detected purely from indirect requires) (#38 / F-H18).
    deps: set[str] = set()
    for block in _GO_REQUIRE_BLOCK.findall(text):
        for line in block.splitlines():
            if "// indirect" in line:
                continue
            m = _GO_MODULE_LINE.match(line)
            if m:
                deps.add(m.group(1))
    for line in text.splitlines():
        if "// indirect" in line:
            continue
        m = _GO_REQUIRE_LINE.match(line)
        if m and m.group(1) != "(":
            deps.add(m.group(1))
    return deps


def parse_pom_xml(text: str) -> set[str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return set()
    ns = {"m": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    prefix = "m:" if ns else ""
    deps: set[str] = set()
    for dep in root.iter():
        if dep.tag.endswith("dependency"):
            group = dep.find(f"{prefix}groupId", ns)
            artifact = dep.find(f"{prefix}artifactId", ns)
            if group is not None and artifact is not None:
                deps.add(f"{group.text}:{artifact.text}")
    return deps


def parse_build_gradle(text: str) -> set[str]:
    return set(_GRADLE_DEP.findall(text))


def parse_gemfile(text: str) -> set[str]:
    return set(_GEMFILE_GEM.findall(text))


def parse_csproj(text: str) -> set[str]:
    """NuGet <PackageReference Include="..."> (and <Reference> fallback)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return set()
    deps: set[str] = set()
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("PackageReference", "Reference"):
            include = el.get("Include")
            if include:
                deps.add(include.split(",", 1)[0].strip().lower())
    return deps


def parse_packages_config(text: str) -> set[str]:
    """Legacy NuGet packages.config: <package id="..." />."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return set()
    ids: set[str] = set()
    for el in root.iter():
        pid = el.get("id")
        if el.tag.split("}")[-1] == "package" and pid:
            ids.add(pid.lower())
    return ids


def parse_composer_json(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(data, dict):
        return set()
    deps: set[str] = set()
    for key in ("require", "require-dev"):
        section = data.get(key)
        if isinstance(section, dict):
            for name in section:
                low = name.lower()
                if low == "php" or low.startswith("ext-"):
                    continue
                deps.add(low)
    return deps


def parse_cargo_toml(text: str) -> set[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    deps: set[str] = set()

    def collect(section) -> None:
        if isinstance(section, dict):
            deps.update(k.lower().replace("_", "-") for k in section)

    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        collect(data.get(key))
    collect(data.get("workspace", {}).get("dependencies"))
    for target in (data.get("target", {}) or {}).values():
        if isinstance(target, dict):
            collect(target.get("dependencies"))
    return deps


@dataclass(slots=True)
class ManifestDeps:
    """Dependencies per ecosystem, plus which manifest files provided them."""

    python: set[str] = field(default_factory=set)
    javascript: set[str] = field(default_factory=set)
    go: set[str] = field(default_factory=set)
    java: set[str] = field(default_factory=set)
    ruby: set[str] = field(default_factory=set)
    csharp: set[str] = field(default_factory=set)
    php: set[str] = field(default_factory=set)
    rust: set[str] = field(default_factory=set)
    sources: list[str] = field(default_factory=list)  # repo-relative manifest paths

    def for_language(self, language: str) -> set[str]:
        if language in ("typescript", "tsx"):
            language = "javascript"
        return getattr(self, language, set())


_MANIFEST_SPECS: list[tuple[str, str, Callable[[str], set[str]]]] = [  # (glob, ecosystem, parser)
    ("requirements*.txt", "python", parse_requirements_txt),
    ("pyproject.toml", "python", parse_pyproject_toml),
    ("package.json", "javascript", parse_package_json),
    ("go.mod", "go", parse_go_mod),
    ("pom.xml", "java", parse_pom_xml),
    ("build.gradle", "java", parse_build_gradle),
    ("build.gradle.kts", "java", parse_build_gradle),
    ("Gemfile", "ruby", parse_gemfile),
    ("*.csproj", "csharp", parse_csproj),
    ("packages.config", "csharp", parse_packages_config),
    ("composer.json", "php", parse_composer_json),
    ("Cargo.toml", "rust", parse_cargo_toml),
]

# root plus several levels: real monorepos nest project manifests deep, e.g.
# nopcommerce's src/Libraries/Nop.Core/Nop.Core.csproj (depth 3) — a shallower
# search read 0 C# deps and left framework detection empty (#38 / F-H30).
_MANIFEST_SEARCH_DEPTH = 5
# Dependency trees, build output, and non-app subprojects (benchmarks, examples,
# docs sites) carry their own manifests whose deps are not the repo's framework —
# they produced spurious detections (hono/strapi/superset saw express+react from
# benchmark package.json) (#38 / F-H18). "packages"/"apps" are kept: standard
# monorepo/workspace layouts hold real projects.
_MANIFEST_SKIP_DIRS = frozenset(
    {
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "target",
        "benchmark",
        "benchmarks",
        "bench",
        "example",
        "examples",
        "sample",
        "samples",
        "e2e",
        "fixtures",
        "testdata",
        "__tests__",
        "docs",
    }
)


def parse_manifests(root: str | Path) -> ManifestDeps:
    root = Path(root)
    result = ManifestDeps()
    for pattern, ecosystem, parser in _MANIFEST_SPECS:
        for depth in range(_MANIFEST_SEARCH_DEPTH):
            glob = "/".join(["*"] * depth + [pattern]) if depth else pattern
            for manifest in root.glob(glob):
                if not manifest.is_file():
                    continue
                # Check only the path *within* the repo — the absolute path to the
                # repo may itself contain a skip-dir name (e.g. tests/fixtures/...).
                if any(part in _MANIFEST_SKIP_DIRS for part in manifest.relative_to(root).parts):
                    continue
                try:
                    text = manifest.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                deps = parser(text)
                # A framework's own repo lists the framework as its package `name`,
                # not as a dependency (laravel-framework's composer name is
                # "laravel/framework"), so it went undetected. Treat the self-name as
                # a dependency so a framework source repo detects itself (#38).
                self_name = _manifest_self_name(pattern, text)
                if self_name:
                    deps.add(self_name)
                if deps:
                    getattr(result, ecosystem).update(deps)
                    result.sources.append(manifest.relative_to(root).as_posix())
    return result


def _manifest_self_name(pattern: str, text: str) -> str | None:
    """The manifest's own package name, for JSON manifests (package.json /
    composer.json). Lowercased to match how deps are normalized."""
    if pattern not in ("package.json", "composer.json"):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name.lower() if isinstance(name, str) and name else None
