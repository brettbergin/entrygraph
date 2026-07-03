"""Shared enums used by the ORM models, the extraction IR, and the public API.

Kept dependency-free so worker processes can unpickle IR without importing
SQLAlchemy machinery.
"""

from __future__ import annotations

import enum


class SymbolKind(enum.Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    CONSTANT = "constant"
    INTERFACE = "interface"
    STRUCT = "struct"
    PROPERTY = "property"
    FIELD = "field"
    EXTERNAL = "external"


class EdgeKind(enum.Enum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    REFERENCES = "references"
    PASSED_AS_CALLBACK = "callback"  # function name handed to another call as an argument
    DECORATES = "decorates"  # annotation / attribute / derive-macro application (not a call)


class EntrypointKind(enum.Enum):
    HTTP_ROUTE = "http_route"
    CLI_COMMAND = "cli_command"
    MAIN = "main"
    TASK = "task"
    LAMBDA_HANDLER = "lambda_handler"
    EVENT_HANDLER = "event_handler"
    MIDDLEWARE = "middleware"  # request/response interceptor (before_request, app.use, ...)
    RPC_HANDLER = "rpc_handler"  # gRPC / RPC service registration (RegisterXxxServer)


class Confidence(enum.IntEnum):
    """How a reference was bound to its target symbol."""

    UNRESOLVED = 0  # kept with its textual target only
    FUZZY = 1  # unique-name match, no import evidence
    IMPORT = 2  # via the file's import map (project or external)
    EXACT = 3  # same-scope / same-module / known-class-method
