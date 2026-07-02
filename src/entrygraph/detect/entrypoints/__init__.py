"""Entrypoint rule registry. Importing this package registers built-in rules."""

import importlib

from entrygraph.detect.entrypoints.base import (
    EntrypointRule,
    all_rules,
    register,
    rules_for,
)

# Importing each rule module registers its rules. Done defensively so a
# language still under construction doesn't break the whole registry.
for _mod in ("python", "javascript", "golang", "java", "ruby"):
    _full = f"entrygraph.detect.entrypoints.{_mod}"
    try:
        importlib.import_module(_full)
    except ModuleNotFoundError as _exc:  # noqa: PERF203
        if _exc.name != _full:
            raise

__all__ = ["EntrypointRule", "all_rules", "register", "rules_for"]
