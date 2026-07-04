"""Per-function syntactic facts for the reaching-defs check (#96 Phase 2).

Facts are extracted from a fresh tree-sitter parse of one function body. They are
deliberately coarse and alias-blind: an expression reduces to the set of *root
identifiers* it depends on (``req.body.name`` -> ``{req}``, ``a + b`` -> ``{a,
b}``). Over-tainting is acceptable — a false "reaches" only fails to demote a
finding; a false "does not reach" would wrongly demote a real one, so the
refutation path (``complete``) is conservative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from entrygraph.parsing.parsers import parse, supported

if TYPE_CHECKING:  # pragma: no cover
    from tree_sitter import Node


@dataclass(frozen=True, slots=True)
class AssignFact:
    targets: tuple[str, ...]
    rhs_roots: frozenset[str]
    line: int


@dataclass(frozen=True, slots=True)
class CallFact:
    callee_name: str  # rightmost segment: "run"
    callee_text: str  # full: "subprocess.run"
    arg_roots: frozenset[str]  # root identifiers among the arguments
    nested_call_names: frozenset[str]  # callee names of calls nested in the args
    assign_target: str | None  # LHS if the call is the sole RHS of an assignment
    line: int


@dataclass(frozen=True, slots=True)
class ReturnFact:
    rhs_roots: frozenset[str]
    line: int


@dataclass(slots=True)
class FunctionFacts:
    params: tuple[str, ...]
    facts: list  # AssignFact | CallFact | ReturnFact, in source order
    complete: bool  # False if an unmodeled construct could hide a flow


@dataclass(frozen=True, slots=True)
class _LangTable:
    function_types: frozenset[str]
    assignment_types: frozenset[str]
    call_types: frozenset[str]
    identifier_types: frozenset[str]
    member_types: frozenset[str]
    param_container_types: frozenset[str]
    # node types that could hide a flow we don't model -> mark facts incomplete
    opaque_types: frozenset[str] = frozenset()


_PYTHON = _LangTable(
    function_types=frozenset({"function_definition"}),
    assignment_types=frozenset({"assignment", "augmented_assignment", "named_expression"}),
    call_types=frozenset({"call"}),
    identifier_types=frozenset({"identifier"}),
    member_types=frozenset({"attribute", "subscript"}),
    param_container_types=frozenset({"parameters"}),
    opaque_types=frozenset({"exec", "global_statement", "nonlocal_statement"}),
)

_JS = _LangTable(
    function_types=frozenset(
        {"function_declaration", "function_expression", "arrow_function", "method_definition"}
    ),
    assignment_types=frozenset(
        {"assignment_expression", "variable_declarator", "augmented_assignment_expression"}
    ),
    call_types=frozenset({"call_expression"}),
    identifier_types=frozenset(
        {"identifier", "shorthand_property_identifier", "property_identifier"}
    ),
    member_types=frozenset({"member_expression", "subscript_expression"}),
    param_container_types=frozenset({"formal_parameters"}),
    opaque_types=frozenset({"with_statement"}),
)

_LANG_TABLES: dict[str, _LangTable] = {
    "python": _PYTHON,
    "javascript": _JS,
    "typescript": _JS,
    "tsx": _JS,
}


def language_supported(language: str | None) -> bool:
    return language in _LANG_TABLES


def _node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _root_ident(node: Node, table: _LangTable) -> str | None:
    """The leftmost identifier of an attribute/subscript/call chain, or None."""
    cur: Node | None = node
    seen = 0
    while cur is not None and seen < 64:
        seen += 1
        t = cur.type
        if t in table.identifier_types:
            return _node_text(cur)
        if t in table.member_types:
            cur = cur.child_by_field_name("object") or cur.child_by_field_name("value")
            continue
        if t in table.call_types:
            cur = cur.child_by_field_name("function")
            continue
        if t == "parenthesized_expression" and cur.named_child_count:
            cur = cur.named_children[0]
            continue
        return None
    return None


def _expr_roots(node: Node | None, table: _LangTable) -> set[str]:
    """Root identifiers an expression depends on. Member/subscript/call chains
    contribute only their leftmost identifier; everything else is walked."""
    if node is None:
        return set()
    roots: set[str] = set()
    stack = [node]
    seen = 0
    while stack and seen < 4096:
        seen += 1
        n = stack.pop()
        t = n.type
        if t in table.identifier_types:
            roots.add(_node_text(n))
            continue
        if t in table.member_types:
            r = _root_ident(n, table)
            if r is not None:
                roots.add(r)
            continue
        if t in table.call_types:
            # a call's value depends on its receiver root and its arguments
            fn = n.child_by_field_name("function")
            if fn is not None:
                r = _root_ident(fn, table)
                if r is not None:
                    roots.add(r)
            args = n.child_by_field_name("arguments")
            if args is not None:
                stack.extend(args.named_children)
            continue
        stack.extend(n.named_children)
    return roots


def _call_names(node: Node, table: _LangTable) -> set[str]:
    """Callee names of calls nested anywhere under a node (for accessor-in-arg)."""
    names: set[str] = set()
    stack = [node]
    seen = 0
    while stack and seen < 4096:
        seen += 1
        n = stack.pop()
        if n.type in table.call_types:
            fn = n.child_by_field_name("function")
            if fn is not None:
                text = _node_text(fn)
                names.add(text.rsplit(".", 1)[-1])
        stack.extend(n.named_children)
    return names


def _callee_parts(fn: Node | None, table: _LangTable) -> tuple[str, str]:
    if fn is None:
        return "", ""
    text = _node_text(fn)
    if fn.type in table.member_types:
        prop = fn.child_by_field_name("property") or fn.child_by_field_name("attribute")
        name = _node_text(prop) if prop is not None else text.rsplit(".", 1)[-1]
    else:
        name = text.rsplit(".", 1)[-1]
    return name, text


def _params(func: Node, table: _LangTable) -> tuple[str, ...]:
    for child in func.children:
        if child.type in table.param_container_types:
            out: list[str] = []
            for p in child.named_children:
                r = _leftmost_param_ident(p, table)
                if r is not None:
                    out.append(r)
            return tuple(out)
    return ()


def _leftmost_param_ident(node: Node, table: _LangTable) -> str | None:
    if node.type in table.identifier_types:
        return _node_text(node)
    # typed / default / destructured param: find the first identifier child
    stack = list(node.named_children)
    while stack:
        n = stack.pop(0)
        if n.type in table.identifier_types:
            return _node_text(n)
        stack[:0] = list(n.named_children)
    return None


def _find_function(root: Node, start_line: int, end_line: int, table: _LangTable) -> Node | None:
    """Smallest function node whose span covers [start_line, end_line]."""
    best: Node | None = None
    stack = [root]
    while stack:
        n = stack.pop()
        if (
            n.type in table.function_types
            and n.start_point.row + 1 <= start_line
            and n.end_point.row + 1 >= end_line
            and (
                best is None
                or (n.end_point.row - n.start_point.row)
                < (best.end_point.row - best.start_point.row)
            )
        ):
            best = n
        stack.extend(n.children)
    return best


def _collect(body: Node, func: Node, table: _LangTable, out: FunctionFacts) -> None:
    """Walk the function body (excluding nested function defs) collecting facts."""
    stack = [body]
    while stack:
        n = stack.pop()
        t = n.type
        if t in table.function_types and n is not func:
            continue  # a nested def has its own scope; don't descend
        if t in table.opaque_types:
            out.complete = False
        if t in table.assignment_types:
            _emit_assignment(n, table, out)
        elif t in table.call_types:
            _emit_call(n, table, out)
        stack.extend(n.children)


def _emit_assignment(node: Node, table: _LangTable, out: FunctionFacts) -> None:
    left = node.child_by_field_name("left") or node.child_by_field_name("name")
    right = node.child_by_field_name("right") or node.child_by_field_name("value")
    targets = tuple(_expr_roots(left, table)) if left is not None else ()
    if not targets:
        return
    out.facts.append(
        AssignFact(
            targets=targets,
            rhs_roots=frozenset(_expr_roots(right, table)),
            line=node.start_point.row + 1,
        )
    )


def _emit_call(node: Node, table: _LangTable, out: FunctionFacts) -> None:
    fn = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    name, text = _callee_parts(fn, table)
    arg_roots: set[str] = set()
    nested: set[str] = set()
    if args is not None:
        for a in args.named_children:
            arg_roots |= _expr_roots(a, table)
            nested |= _call_names(a, table)
    out.facts.append(
        CallFact(
            callee_name=name,
            callee_text=text,
            arg_roots=frozenset(arg_roots),
            nested_call_names=frozenset(nested),
            assign_target=_assign_target(node, table),
            line=node.start_point.row + 1,
        )
    )


def _assign_target(call: Node, table: _LangTable) -> str | None:
    """LHS identifier if the call is the sole RHS of a single-target assignment."""
    parent = call.parent
    if parent is None:
        return None
    if parent.type in table.assignment_types:
        left = parent.child_by_field_name("left") or parent.child_by_field_name("name")
        right = parent.child_by_field_name("right") or parent.child_by_field_name("value")
        # compare by byte span, not identity: child_by_field_name returns a fresh
        # wrapper each call, so `right is call` is always False in tree-sitter.
        same = (
            right is not None
            and right.start_byte == call.start_byte
            and right.end_byte == call.end_byte
        )
        if same and left is not None and left.type in table.identifier_types:
            return _node_text(left)
    return None


def extract_function_facts(
    language: str | None, source: bytes, start_line: int, end_line: int
) -> FunctionFacts | None:
    """Facts for the function spanning [start_line, end_line] in ``source``.

    Returns None for an unsupported language, an unparseable file, or when no
    function node covers the range (e.g. a module-level source symbol)."""
    if language is None:
        return None
    table = _LANG_TABLES.get(language)
    if table is None or not supported(language):
        return None
    try:
        tree = parse(language, source)
    except Exception:
        return None
    func = _find_function(tree.root_node, start_line, end_line, table)
    if func is None:
        return None
    body = func.child_by_field_name("body") or func
    out = FunctionFacts(
        params=_params(func, table), facts=[], complete=not tree.root_node.has_error
    )
    _collect(body, func, table, out)
    return out
