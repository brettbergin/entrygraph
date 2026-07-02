"""Framework detection: manifest deps + code signals, noisy-or confidence.

confidence = 1 - prod(1 - w_i) over fired signals. A manifest dependency or an
import alone is strong evidence; file-presence signals corroborate.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal

from entrygraph.detect.manifests import ManifestDeps

SignalKind = Literal["manifest_dep", "import", "file_presence", "symbol_name"]


@dataclass(frozen=True, slots=True)
class FrameworkSignal:
    kind: SignalKind
    pattern: str  # dep glob, import module, path glob, or symbol-name glob
    weight: float


@dataclass(frozen=True, slots=True)
class FrameworkSpec:
    name: str
    language: str
    signals: tuple[FrameworkSignal, ...]
    threshold: float = 0.5


@dataclass(frozen=True, slots=True)
class DetectedFramework:
    name: str
    language: str
    confidence: float
    evidence: tuple[str, ...]

    @property
    def detected(self) -> bool:
        return True


FRAMEWORKS: list[FrameworkSpec] = [
    FrameworkSpec("flask", "python", (
        FrameworkSignal("manifest_dep", "flask", 0.8),
        FrameworkSignal("import", "flask", 0.7),
    )),
    FrameworkSpec("fastapi", "python", (
        FrameworkSignal("manifest_dep", "fastapi", 0.8),
        FrameworkSignal("import", "fastapi", 0.7),
    )),
    FrameworkSpec("django", "python", (
        FrameworkSignal("manifest_dep", "django", 0.8),
        FrameworkSignal("import", "django", 0.7),
        FrameworkSignal("file_presence", "*manage.py", 0.3),
        FrameworkSignal("file_presence", "*urls.py", 0.2),
    )),
    FrameworkSpec("click", "python", (
        FrameworkSignal("manifest_dep", "click", 0.8),
        FrameworkSignal("import", "click", 0.7),
    )),
    FrameworkSpec("typer", "python", (
        FrameworkSignal("manifest_dep", "typer", 0.8),
        FrameworkSignal("import", "typer", 0.7),
    )),
    FrameworkSpec("argparse", "python", (
        FrameworkSignal("import", "argparse", 0.7),
    )),
    FrameworkSpec("celery", "python", (
        FrameworkSignal("manifest_dep", "celery", 0.8),
        FrameworkSignal("import", "celery", 0.7),
    )),
    FrameworkSpec("aws-lambda", "python", (
        FrameworkSignal("file_presence", "serverless.y*ml", 0.5),
        FrameworkSignal("file_presence", "template.y*ml", 0.3),
        FrameworkSignal("symbol_name", "lambda_handler", 0.5),
    )),
    # JS/TS, Go, Java, Ruby specs land with their extractors (M8)
    FrameworkSpec("express", "javascript", (
        FrameworkSignal("manifest_dep", "express", 0.8),
        FrameworkSignal("import", "express", 0.7),
    )),
    FrameworkSpec("fastify", "javascript", (
        FrameworkSignal("manifest_dep", "fastify", 0.8),
        FrameworkSignal("import", "fastify", 0.7),
    )),
    FrameworkSpec("nestjs", "javascript", (
        FrameworkSignal("manifest_dep", "@nestjs/*", 0.8),
        FrameworkSignal("import", "@nestjs/*", 0.7),
    )),
    FrameworkSpec("next", "javascript", (
        FrameworkSignal("manifest_dep", "next", 0.8),
        FrameworkSignal("file_presence", "next.config.*", 0.5),
    )),
    FrameworkSpec("react", "javascript", (
        FrameworkSignal("manifest_dep", "react", 0.8),
        FrameworkSignal("import", "react", 0.7),
    )),
    FrameworkSpec("gin", "go", (
        FrameworkSignal("manifest_dep", "github.com/gin-gonic/gin", 0.8),
        FrameworkSignal("import", "github.com/gin-gonic/gin", 0.7),
    )),
    FrameworkSpec("echo", "go", (
        FrameworkSignal("manifest_dep", "github.com/labstack/echo*", 0.8),
        FrameworkSignal("import", "github.com/labstack/echo*", 0.7),
    )),
    FrameworkSpec("cobra", "go", (
        FrameworkSignal("manifest_dep", "github.com/spf13/cobra", 0.8),
        FrameworkSignal("import", "github.com/spf13/cobra", 0.7),
    )),
    FrameworkSpec("net/http", "go", (
        FrameworkSignal("import", "net/http", 0.7),
    )),
    FrameworkSpec("spring-boot", "java", (
        FrameworkSignal("manifest_dep", "*spring-boot*", 0.8),
        FrameworkSignal("import", "org.springframework*", 0.7),
    )),
    FrameworkSpec("jax-rs", "java", (
        FrameworkSignal("import", "javax.ws.rs*", 0.7),
        FrameworkSignal("import", "jakarta.ws.rs*", 0.7),
    )),
    FrameworkSpec("rails", "ruby", (
        FrameworkSignal("manifest_dep", "rails", 0.8),
        FrameworkSignal("file_presence", "config/routes.rb", 0.5),
    )),
    FrameworkSpec("sinatra", "ruby", (
        FrameworkSignal("manifest_dep", "sinatra", 0.8),
        FrameworkSignal("import", "sinatra", 0.7),
    )),
    FrameworkSpec("rake", "ruby", (
        FrameworkSignal("file_presence", "Rakefile", 0.6),
    )),
]


def detect_frameworks(
    manifests: ManifestDeps,
    import_signals: set[tuple[str, str]],  # (language, module)
    file_paths: list[str],
    symbol_names: set[str] | None = None,
    languages_present: set[str] | None = None,
) -> list[DetectedFramework]:
    symbol_names = symbol_names or set()
    detected: list[DetectedFramework] = []
    for spec in FRAMEWORKS:
        if languages_present is not None and spec.language not in languages_present:
            continue
        fired: list[tuple[FrameworkSignal, str]] = []
        for signal in spec.signals:
            evidence = _check_signal(signal, spec, manifests, import_signals, file_paths, symbol_names)
            if evidence:
                fired.append((signal, evidence))
        if not fired:
            continue
        confidence = 1.0
        for signal, _ in fired:
            confidence *= 1.0 - signal.weight
        confidence = 1.0 - confidence
        if confidence >= spec.threshold:
            detected.append(
                DetectedFramework(
                    name=spec.name,
                    language=spec.language,
                    confidence=round(confidence, 3),
                    evidence=tuple(e for _, e in fired),
                )
            )
    detected.sort(key=lambda d: d.confidence, reverse=True)
    return detected


def _check_signal(
    signal: FrameworkSignal,
    spec: FrameworkSpec,
    manifests: ManifestDeps,
    import_signals: set[tuple[str, str]],
    file_paths: list[str],
    symbol_names: set[str],
) -> str | None:
    if signal.kind == "manifest_dep":
        for dep in manifests.for_language(spec.language):
            if fnmatch.fnmatch(dep.lower(), signal.pattern.lower()):
                return f"manifest dependency {dep!r}"
    elif signal.kind == "import":
        for lang, module in import_signals:
            if _same_ecosystem(lang, spec.language) and fnmatch.fnmatch(module, signal.pattern):
                return f"import of {module!r}"
    elif signal.kind == "file_presence":
        for path in file_paths:
            if fnmatch.fnmatch(path, signal.pattern):
                return f"file {path!r}"
    elif signal.kind == "symbol_name":
        for name in symbol_names:
            if fnmatch.fnmatch(name, signal.pattern):
                return f"symbol {name!r}"
    return None


def _same_ecosystem(lang: str, spec_lang: str) -> bool:
    js = {"javascript", "typescript", "tsx"}
    if spec_lang == "javascript":
        return lang in js
    return lang == spec_lang
