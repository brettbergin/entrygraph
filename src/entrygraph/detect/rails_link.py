"""Cross-file Rails route -> controller-action binding (graphql_link-style pass).

The routes DSL names its handler indirectly (`resources :posts`,
`to: 'admin/reports#show'`); the routes file itself contains no callable, so
without this pass every Rails route binds to the routes.rb module symbol — an
honest location but a dead end for reachability. This pass runs in the scanner
after all files' symbols are registered and, for each rails hint whose metadata
carries ``controller``/``action``, rebinds ``handler_qualified_name`` to the
matching ``FooController#action`` method found via the symbol table. No or
ambiguous match leaves the hint unbound (module fallback) — never guess wrong.
"""

from __future__ import annotations

import re

from entrygraph.extract.ir import EntrypointHint, FileExtraction, ParameterHint, RawSymbol
from entrygraph.kinds import SymbolKind
from entrygraph.resolve.symbol_table import SymbolTable

_CALLABLE_KINDS = (SymbolKind.METHOD, SymbolKind.FUNCTION)
_SYMBOLS = re.compile(r":(\w+)")
_QUERY_METHODS = frozenset({"GET", "DELETE", "HEAD"})


def link_rails(extractions: list[tuple[str, FileExtraction, bool]], table: SymbolTable) -> int:
    """Bind rails route hints to controller actions. Returns the rebind count.

    A bound action whose defining file is in this run's extractions is also
    enriched with the parameters its body reveals: strong-parameter ``permit``
    chains (directly or via a called ``*_params`` helper) and observed
    ``params[:x]`` reads. Enrichment is best-effort — on an incremental run
    whose controller file was unchanged, the binding still lands (symbol
    table) but body-derived parameters wait for the next full/heal scan."""
    locator: dict[str, tuple[FileExtraction, RawSymbol]] = {}
    for _path, x, _pkg in extractions:
        if x.language != "ruby":
            continue
        for sym in x.symbols:
            locator[sym.qualified_name] = (x, sym)

    rebound = 0
    for _path, x, _pkg in extractions:
        if x.language != "ruby":
            continue
        for hint in x.entrypoint_hints:
            if hint.rule_id != "ruby.rails.routes" or hint.handler_qualified_name:
                continue
            controller = hint.metadata.get("controller")
            action = hint.metadata.get("action")
            if not controller or not action:
                continue
            target = _resolve_action(controller, action, table)
            if target is None:
                continue
            hint.handler_qualified_name = target
            rebound += 1
            located = locator.get(target)
            if located is not None:
                _enrich_from_action(hint, *located)
    return rebound


def _singularize(name: str) -> str:
    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if any(name.endswith(s) for s in ("ses", "xes", "zes", "ches", "shes")):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _enrich_from_action(hint: EntrypointHint, x: FileExtraction, action: RawSymbol) -> None:
    names = {p.name for p in hint.parameters}
    spans = [(action.span.start_line, action.span.end_line)]
    # A strong-params helper contributes its permit chain. A parenthesized call
    # (`post_params()`) surfaces as an implicit-self reference in the action
    # body; the conventional paren-less form (`Post.new(post_params)`) is a bare
    # identifier the extractor can't see, so for body-consuming routes the
    # conventionally-named `{resource}_params` sibling counts too.
    helper_names = {
        ref.callee_name
        for ref in x.references
        if ref.kind == "call"
        and ref.receiver_text == "self"  # implicit-self send inside a method
        and ref.callee_name.endswith("_params")
        and spans[0][0] <= ref.span.start_line <= spans[0][1]
    }
    if any(m in ("POST", "PUT", "PATCH") for m in hint.http_methods):
        base = (hint.metadata.get("controller") or "").rsplit("/", 1)[-1]
        if base:
            helper_names |= {f"{base}_params", f"{_singularize(base)}_params"}
    parent = action.parent_qualified_name
    for sym in x.symbols:
        if sym.name in helper_names and sym.parent_qualified_name == parent:
            spans.append((sym.span.start_line, sym.span.end_line))

    def _in_spans(line: int) -> bool:
        return any(s <= line <= e for s, e in spans)

    for ref in x.references:
        if ref.kind != "call" or not ref.arg_preview or not _in_spans(ref.span.start_line):
            continue
        if ref.callee_name == "permit" and "params" in (ref.receiver_text or ""):
            for key in _SYMBOLS.findall(ref.arg_preview):
                if key in names:
                    continue
                names.add(key)
                hint.parameters.append(
                    ParameterHint(
                        name=key,
                        location="body",
                        required=False,
                        provenance="strong_params",
                        line=ref.span.start_line,
                    )
                )
        elif (
            ref.callee_name == "params"
            and ref.receiver_text is None
            and ref.span.end_line == ref.span.start_line  # a read, not a params block
        ):
            key_m = re.search(r'["\']([^"\']+)', ref.arg_preview)
            key = key_m.group(1) if key_m else None
            if not key or key in names:
                continue
            names.add(key)
            location = "query" if all(m in _QUERY_METHODS for m in hint.http_methods) else "form"
            hint.parameters.append(
                ParameterHint(
                    name=key,
                    location=location,
                    required=False,
                    provenance="usage",
                    line=ref.span.start_line,
                )
            )


def _camelize(name: str) -> str:
    return "".join(part.title() for part in name.split("_"))


def _resolve_action(controller: str, action: str, table: SymbolTable) -> str | None:
    """``admin/posts`` + ``show`` -> the qname of ``Admin::PostsController#show``.

    Candidates are callable ruby symbols named ``action`` whose immediate
    container is the controller class — either the bare class name (nested in
    ``module Admin``) or the scope-operator form (``class Admin::PostsController``,
    which the extractor keeps as one symbol name). Multiple matches narrow by the
    namespace segments appearing anywhere in the qname (module or path casing);
    anything still ambiguous stays unbound."""
    *namespace, base = controller.split("/")
    class_names = {_camelize(base) + "Controller"}
    if namespace:
        class_names.add("::".join(_camelize(s) for s in (*namespace, base)) + "Controller")
    candidates = [
        sid
        for sid in table.by_name.get(action, [])
        if table.kinds.get(sid) in _CALLABLE_KINDS and table.lang.get(sid) == "ruby"
    ]
    matches = []
    for sid in candidates:
        parts = table.qname_of[sid].split(".")
        if len(parts) >= 2 and parts[-2] in class_names:
            matches.append(sid)
    if len(matches) > 1 and namespace:
        needles = {s.lower() for s in namespace}
        matches = [
            sid
            for sid in matches
            if needles <= {part.lower() for part in table.qname_of[sid].split(".")}
        ]
    return table.qname_of[matches[0]] if len(matches) == 1 else None
