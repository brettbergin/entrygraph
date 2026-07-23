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
    # Written return type of a function/method (`func New() *Ingester` -> "Ingester";
    # Rust `fn make() -> Foo` -> "Foo"). Resolved to a qname in resolve_bindings and
    # keyed into SymbolTable.return_types so `x := pkg.New(..)` types x (#113).
    return_type_text: str | None = None


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
    # Identifier/selector arguments in call order (`t.Ingester`, `router_var`),
    # capped — replaces the preview-regex that mount/gRPC resolvers scraped (#98).
    arg_idents: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RawBinding:
    """A syntactic name->type binding at a construction/declaration site (#98).

    Not full inference: the ``type_text`` is the *written* type at the binding
    site (constructor name, declared field type), resolved to a qname later in
    the main process against the file's import map — the same two-phase split
    ``RawSymbol.bases`` -> ``resolve_hierarchy`` already uses.
    """

    name: str  # bound name: "t", "router"; fields owner-qualified: "App.Ingester"
    type_text: str  # written type: "ingester.Ingester", "Foo", "express"
    span: Span
    scope: str | None = None  # enclosing-function FQN; None = module/class level
    kind: str = "constructor"  # constructor|declared|field|receiver|call_result


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
class ParameterHint:
    """A declared or observed input parameter of an entrypoint.

    ``location`` uses the taint SourcePattern.channel vocabulary
    (path|query|body|form|header|cookie) so parameters join path results on
    (source_key, source_channel) without re-mapping. ``provenance`` records how
    the parameter was learned: "route" (template segment), "dsl" (a params
    declaration block), "strong_params" (Rails permit), "usage" (an observed
    params[:x] read).
    """

    name: str
    location: str
    required: bool = True
    type_ref: str | None = None  # declared type name (Grape `type: String`)
    provenance: str = "route"
    line: int | None = None  # declaration site, when known


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
    parameters: list[ParameterHint] = field(default_factory=list)


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
    bindings: list[RawBinding] = field(default_factory=list)  # name->type sites (#98)
    entrypoint_hints: list[EntrypointHint] = field(default_factory=list)
    framework_signals: list[tuple[str, str]] = field(default_factory=list)  # (kind, value)
    # Repo-relative paths of files that are test-only submodules declared elsewhere,
    # e.g. Rust `#[cfg(test)] mod tests;` pointing at a separate `tests.rs`. The
    # file-level test gate can't see these; the scanner drops them unless
    # --include-tests. Candidate paths (both `X.rs` and `X/mod.rs`) — #100 follow-up.
    test_submodule_files: list[str] = field(default_factory=list)
    # Repo-wide names of project functions that forward to a native route registrar
    # (Django path/re_path/url), e.g. Zulip's rest_path. Populated by the scanner
    # across all files before rules run, so a per-file rule can follow one level of
    # wrapper indirection (#50).
    route_wrappers: set[str] = field(default_factory=set)
    # `export default <identifier>` — the local name re-exported as the module's
    # default. Lets the Express mount resolver alias the default export to the router
    # var routes are registered on (#36).
    default_export: str | None = None
    # Express router var -> the path prefix it is mounted under, resolved across
    # files by the scanner's mount graph and consumed by the express route rule (#36).
    route_prefixes: dict[str, str] = field(default_factory=dict)
