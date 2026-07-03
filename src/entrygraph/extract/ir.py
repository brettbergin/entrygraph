"""The extraction intermediate representation.

This is the contract between (a) parse workers and the main process, (b) the
resolver, and (c) the DB writer. Everything here is plain, slotted, and
pickle-cheap — never tree-sitter objects, which hold unpicklable C pointers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from entrygraph.kinds import EntrypointKind, SymbolKind


@dataclass(slots=True)
class Span:
    start_line: int  # 1-based
    start_col: int
    end_line: int
    end_col: int


@dataclass(slots=True)
class RawSymbol:
    kind: SymbolKind
    name: str  # "run"
    qualified_name: str  # "app.services.runner.Runner.run"
    span: Span
    parent_qualified_name: str | None = None  # enclosing class/function FQN
    signature: str | None = None  # first line of the def, truncated
    decorators: list[str] = field(default_factory=list)  # raw source text
    bases: list[str] = field(default_factory=list)  # raw supertype expressions
    modifiers: list[str] = field(default_factory=list)  # static/async/exported...
    docstring: str | None = None
    is_exported: bool = True


@dataclass(slots=True)
class RawImport:
    module: str  # "subprocess", "./utils", "com.example.Foo"
    imported_name: str | None  # None = whole-module import; "*" = star import
    alias: str  # name bound in local scope: "sub", "run", "Foo"
    span: Span
    is_relative: bool = False
    relative_level: int = 0  # Python: number of leading dots


@dataclass(slots=True)
class RawReference:
    # "call" | "inherit" | "decorator" | "annotation"
    # | "implement" (interface conformance) | "callback" (function name passed as
    #   an argument) | "dynamic_call" (getattr/computed/send — target unknowable)
    kind: str
    callee_text: str  # full source text: "self.client.get"
    callee_name: str  # rightmost segment: "get"
    receiver_text: str | None  # "self.client" or None for bare calls
    span: Span
    caller_qualified_name: str | None  # FQN of enclosing def; None = module level
    arg_count: int = 0
    arg_preview: str | None = None  # truncated literal args, for sink triage
    # LHS variable when this call is the sole RHS of a single-var `:=`/`=`
    # (`api := app.Group("/api")`). Lets router-group rules link routes registered
    # on the group var back to its path prefix. Populated by the Go extractor.
    assign_target: str | None = None


@dataclass(slots=True)
class RawReexport:
    """A `export { X } from "./y"` style re-export (barrel files).

    Kept separate from RawImport so re-exported names never enter the local
    import map — they are only followed when resolving *inbound* references that
    land on this module.
    """

    module: str  # source module specifier: "./handler"
    exported_name: str | None  # local/exported name; None with is_star
    alias: str | None  # renamed export (`export { X as Y }`) or None
    span: Span
    is_star: bool = False  # `export * from "..."`
    is_relative: bool = False


@dataclass(slots=True)
class EntrypointHint:
    rule_id: str  # "python.flask.route"
    kind: EntrypointKind
    handler_qualified_name: str | None  # None if handler is an inline lambda/expr
    route: str | None = None  # "/users/<id>"
    http_methods: list[str] = field(default_factory=list)
    name: str | None = None  # CLI command name, task name
    span: Span | None = None
    framework: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class FileExtraction:
    path: str  # repo-relative
    language: str
    module_path: str  # "app.services.runner" / "src/utils"
    parse_ok: bool
    error_count: int
    symbols: list[RawSymbol] = field(default_factory=list)
    imports: list[RawImport] = field(default_factory=list)
    references: list[RawReference] = field(default_factory=list)
    reexports: list[RawReexport] = field(default_factory=list)
    entrypoint_hints: list[EntrypointHint] = field(default_factory=list)
    framework_signals: list[tuple[str, str]] = field(default_factory=list)  # (kind, value)
