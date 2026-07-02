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
    # --- C6: additional framework specs ---
    # Python
    FrameworkSpec("tornado", "python", (
        FrameworkSignal("manifest_dep", "tornado", 0.8),
        FrameworkSignal("import", "tornado", 0.7),
    )),
    FrameworkSpec("aiohttp", "python", (
        FrameworkSignal("manifest_dep", "aiohttp", 0.8),
        FrameworkSignal("import", "aiohttp", 0.7),
    )),
    FrameworkSpec("bottle", "python", (
        FrameworkSignal("manifest_dep", "bottle", 0.8),
        FrameworkSignal("import", "bottle", 0.7),
    )),
    FrameworkSpec("pyramid", "python", (
        FrameworkSignal("manifest_dep", "pyramid", 0.8),
        FrameworkSignal("import", "pyramid", 0.7),
    )),
    FrameworkSpec("sanic", "python", (
        FrameworkSignal("manifest_dep", "sanic", 0.8),
        FrameworkSignal("import", "sanic", 0.7),
    )),
    FrameworkSpec("dramatiq", "python", (
        FrameworkSignal("manifest_dep", "dramatiq", 0.8),
        FrameworkSignal("import", "dramatiq", 0.7),
    )),
    FrameworkSpec("rq", "python", (
        FrameworkSignal("manifest_dep", "rq", 0.8),
        FrameworkSignal("import", "rq", 0.7),
    )),
    FrameworkSpec("airflow", "python", (
        FrameworkSignal("manifest_dep", "apache-airflow", 0.8),
        FrameworkSignal("import", "airflow", 0.7),
        FrameworkSignal("file_presence", "*dags/*", 0.2),
    )),
    # JS/TS
    FrameworkSpec("koa", "javascript", (
        FrameworkSignal("manifest_dep", "koa", 0.8),
        FrameworkSignal("import", "koa", 0.7),
    )),
    FrameworkSpec("hapi", "javascript", (
        FrameworkSignal("manifest_dep", "@hapi/hapi", 0.8),
        FrameworkSignal("import", "@hapi/hapi", 0.7),
    )),
    FrameworkSpec("remix", "javascript", (
        FrameworkSignal("manifest_dep", "@remix-run/*", 0.8),
        FrameworkSignal("file_presence", "app/routes/*", 0.3),
    )),
    FrameworkSpec("hono", "javascript", (
        FrameworkSignal("manifest_dep", "hono", 0.8),
        FrameworkSignal("import", "hono", 0.7),
    )),
    FrameworkSpec("electron", "javascript", (
        FrameworkSignal("manifest_dep", "electron", 0.8),
        FrameworkSignal("import", "electron", 0.7),
    )),
    FrameworkSpec("socket.io", "javascript", (
        FrameworkSignal("manifest_dep", "socket.io", 0.8),
        FrameworkSignal("import", "socket.io", 0.7),
    )),
    # Go
    FrameworkSpec("fiber", "go", (
        FrameworkSignal("manifest_dep", "github.com/gofiber/fiber*", 0.8),
        FrameworkSignal("import", "github.com/gofiber/fiber*", 0.7),
    )),
    FrameworkSpec("chi", "go", (
        FrameworkSignal("manifest_dep", "github.com/go-chi/chi*", 0.8),
        FrameworkSignal("import", "github.com/go-chi/chi*", 0.7),
    )),
    FrameworkSpec("gorilla-mux", "go", (
        FrameworkSignal("manifest_dep", "github.com/gorilla/mux", 0.8),
        FrameworkSignal("import", "github.com/gorilla/mux", 0.7),
    )),
    FrameworkSpec("grpc-go", "go", (
        FrameworkSignal("manifest_dep", "google.golang.org/grpc", 0.8),
        FrameworkSignal("import", "google.golang.org/grpc", 0.7),
    )),
    FrameworkSpec("urfave-cli", "go", (
        FrameworkSignal("manifest_dep", "github.com/urfave/cli*", 0.8),
        FrameworkSignal("import", "github.com/urfave/cli*", 0.7),
    )),
    # Java
    FrameworkSpec("micronaut", "java", (
        FrameworkSignal("manifest_dep", "*micronaut*", 0.8),
        FrameworkSignal("import", "io.micronaut*", 0.7),
    )),
    FrameworkSpec("quarkus", "java", (
        FrameworkSignal("manifest_dep", "*quarkus*", 0.8),
        FrameworkSignal("import", "io.quarkus*", 0.7),
    )),
    FrameworkSpec("vertx", "java", (
        FrameworkSignal("manifest_dep", "*vertx*", 0.8),
        FrameworkSignal("import", "io.vertx*", 0.7),
    )),
    FrameworkSpec("servlet-api", "java", (
        FrameworkSignal("import", "javax.servlet*", 0.7),
        FrameworkSignal("import", "jakarta.servlet*", 0.7),
        FrameworkSignal("file_presence", "*web.xml", 0.3),
    )),
    FrameworkSpec("dropwizard", "java", (
        FrameworkSignal("manifest_dep", "*dropwizard*", 0.8),
        FrameworkSignal("import", "io.dropwizard*", 0.7),
    )),
    # Ruby
    FrameworkSpec("hanami", "ruby", (
        FrameworkSignal("manifest_dep", "hanami", 0.8),
        FrameworkSignal("import", "hanami", 0.7),
    )),
    FrameworkSpec("grape", "ruby", (
        FrameworkSignal("manifest_dep", "grape", 0.8),
        FrameworkSignal("import", "grape", 0.7),
    )),
    FrameworkSpec("sidekiq", "ruby", (
        FrameworkSignal("manifest_dep", "sidekiq", 0.8),
        FrameworkSignal("import", "sidekiq", 0.7),
    )),
    FrameworkSpec("rack", "ruby", (
        FrameworkSignal("manifest_dep", "rack", 0.8),
        FrameworkSignal("file_presence", "config.ru", 0.6),
    )),
    # --- C2/C3/C4: C#, PHP, Rust ---
    FrameworkSpec("aspnetcore", "csharp", (
        FrameworkSignal("manifest_dep", "microsoft.aspnetcore*", 0.8),
        FrameworkSignal("import", "Microsoft.AspNetCore*", 0.7),
        FrameworkSignal("file_presence", "*appsettings.json", 0.2),
    )),
    FrameworkSpec("ef-core", "csharp", (
        FrameworkSignal("manifest_dep", "microsoft.entityframeworkcore*", 0.8),
        FrameworkSignal("import", "Microsoft.EntityFrameworkCore*", 0.7),
    )),
    FrameworkSpec("laravel", "php", (
        FrameworkSignal("manifest_dep", "laravel/framework", 0.8),
        FrameworkSignal("file_presence", "artisan", 0.5),
        FrameworkSignal("file_presence", "routes/web.php", 0.3),
    )),
    FrameworkSpec("symfony", "php", (
        FrameworkSignal("manifest_dep", "symfony/framework-bundle", 0.8),
        FrameworkSignal("manifest_dep", "symfony/*", 0.5),
        FrameworkSignal("file_presence", "bin/console", 0.4),
    )),
    FrameworkSpec("wordpress", "php", (
        FrameworkSignal("file_presence", "wp-config.php", 0.7),
        FrameworkSignal("symbol_name", "wp_*", 0.2),
    )),
    FrameworkSpec("actix-web", "rust", (
        FrameworkSignal("manifest_dep", "actix-web", 0.8),
        FrameworkSignal("import", "actix_web", 0.7),
    )),
    FrameworkSpec("axum", "rust", (
        FrameworkSignal("manifest_dep", "axum", 0.8),
        FrameworkSignal("import", "axum", 0.7),
    )),
    FrameworkSpec("rocket", "rust", (
        FrameworkSignal("manifest_dep", "rocket", 0.8),
        FrameworkSignal("import", "rocket", 0.7),
    )),
    FrameworkSpec("clap", "rust", (
        FrameworkSignal("manifest_dep", "clap", 0.8),
        FrameworkSignal("import", "clap", 0.7),
    )),
    FrameworkSpec("tokio", "rust", (
        FrameworkSignal("manifest_dep", "tokio", 0.8),
        FrameworkSignal("import", "tokio", 0.7),
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
