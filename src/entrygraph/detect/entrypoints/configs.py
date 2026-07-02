"""Config-file entrypoints — handlers declared outside source code.

The walker only feeds source files to extractors, so serverless/SAM handler
fields, Procfile process lines, and Dockerfile CMD/ENTRYPOINT never reach the
IR-driven rules. This module scans a handful of well-known root files with the
same stdlib+regex approach as detect/manifests.py (there is no YAML dependency),
producing ConfigHints that bind to a real symbol where possible.

`Entrypoint.symbol_id` is NOT NULL, so a hint whose handler cannot be bound to
an indexed symbol is dropped rather than synthesizing a placeholder symbol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from entrygraph.kinds import EntrypointKind

_SERVERLESS_HANDLER = re.compile(r"^\s*handler:\s*([^\s#]+)", re.MULTILINE)
_SAM_HANDLER = re.compile(r"^\s*Handler:\s*([^\s#]+)", re.MULTILINE)
_PROCFILE_LINE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
_DOCKER_CMD = re.compile(r"^\s*(?:CMD|ENTRYPOINT)\s+(.+)$", re.MULTILINE | re.IGNORECASE)

# frameworks whose entrypoints are re-derived from config each index run
CONFIG_FRAMEWORKS = ("serverless", "sam", "procfile", "docker")


@dataclass(slots=True)
class ConfigHint:
    kind: EntrypointKind
    framework: str
    handler_ref: str  # raw handler string as written in the config
    name: str
    route: str | None = None
    metadata: dict = field(default_factory=dict)


def scan_config_entrypoints(root: str | Path) -> list[ConfigHint]:
    root = Path(root)
    hints: list[ConfigHint] = []
    for name in ("serverless.yml", "serverless.yaml"):
        hints += _scan(
            root / name, _SERVERLESS_HANDLER, EntrypointKind.LAMBDA_HANDLER, "serverless"
        )
    for name in ("template.yaml", "template.yml"):
        hints += _scan(root / name, _SAM_HANDLER, EntrypointKind.LAMBDA_HANDLER, "sam")
    hints += _scan_procfile(root / "Procfile")
    hints += _scan_dockerfile(root / "Dockerfile")
    return hints


def _read(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return None


def _scan(
    path: Path, pattern: re.Pattern, kind: EntrypointKind, framework: str
) -> list[ConfigHint]:
    text = _read(path)
    if text is None:
        return []
    hints = []
    for match in pattern.finditer(text):
        ref = match.group(1).strip().strip("'\"")
        hints.append(ConfigHint(kind=kind, framework=framework, handler_ref=ref, name=ref))
    return hints


def _scan_procfile(path: Path) -> list[ConfigHint]:
    text = _read(path)
    if text is None:
        return []
    hints = []
    for proc, command in _PROCFILE_LINE.findall(text):
        hints.append(
            ConfigHint(
                kind=EntrypointKind.MAIN,
                framework="procfile",
                handler_ref=command.strip(),
                name=proc,
            )
        )
    return hints


def _scan_dockerfile(path: Path) -> list[ConfigHint]:
    text = _read(path)
    if text is None:
        return []
    hints = []
    for command in _DOCKER_CMD.findall(text):
        hints.append(
            ConfigHint(
                kind=EntrypointKind.MAIN,
                framework="docker",
                handler_ref=command.strip(),
                name="cmd",
            )
        )
    return hints


def bind_handler(
    handler_ref: str, symbol_id_by_qname: dict[str, int], module_symbol_ids: dict[str, int]
) -> int | None:
    """Best-effort map a config handler string to an indexed symbol id.

    Handles the common forms:
      - ``src/app.handler`` / ``app.lambda_handler`` (dotted or path+dot)
      - ``pkg.mod:app`` (gunicorn/uvicorn colon form)
      - ``python -m pkg.mod`` / ``node src/server.js`` (process commands)
    """
    for qname in _candidate_qnames(handler_ref):
        if qname in symbol_id_by_qname:
            return symbol_id_by_qname[qname]
        if qname in module_symbol_ids:
            return module_symbol_ids[qname]
    return None


_SRC_PREFIXES = ("src/", "lib/", "app/")
_CODE_EXTS = (".py", ".js", ".ts", ".mjs", ".cjs", ".rb", ".go")


def _candidate_qnames(handler_ref: str) -> list[str]:
    ref = handler_ref.strip()
    # process command: take the token that looks like a module/path
    if " " in ref:
        tokens = ref.split()
        if "-m" in tokens:
            i = tokens.index("-m")
            if i + 1 < len(tokens):
                ref = tokens[i + 1]
        else:
            ref = next((t for t in tokens[1:] if "/" in t or "." in t), tokens[-1])
    ref = (
        ref.split(":", 1)[0]
        if ":" in ref and "/" not in ref.split(":", 1)[1]
        else ref.replace(":", ".")
    )
    for prefix in _SRC_PREFIXES:
        if ref.startswith(prefix):
            ref = ref[len(prefix) :]
    for ext in _CODE_EXTS:
        if ref.endswith(ext):
            ref = ref[: -len(ext)]
            break
    dotted = ref.replace("/", ".").strip(".")
    candidates = [dotted]
    # a handler like module.function -> also try the bare module
    if "." in dotted:
        candidates.append(dotted.rsplit(".", 1)[0])
    return [c for c in candidates if c]
