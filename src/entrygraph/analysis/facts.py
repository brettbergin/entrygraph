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
    arg_roots: frozenset[str]  # root identifiers among the arguments (union)
    nested_call_names: frozenset[str]  # callee names of calls nested in the args
    assign_target: str | None  # LHS if the call is the sole RHS of an assignment
    line: int
    arg_roots_by_pos: tuple[frozenset[str], ...] = ()  # per positional argument (#96 P3)
    has_nonpositional: bool = False  # kwarg/spread/variadic present -> position unknown


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
    # field names holding the object/receiver of a member or call, tried in order
    # (py/js "object"/"value"; go "operand"; java/ruby "object"/"receiver")
    object_fields: tuple[str, ...] = ("object", "value")
    # field names holding a call's callee expression / method name, tried in order
    callee_fields: tuple[str, ...] = ("function",)
    # field names holding the method-name node when there is no callee expr
    # (java method_invocation "name", ruby call "method")
    name_fields: tuple[str, ...] = ()


_PYTHON = _LangTable(
    function_types=frozenset({"function_definition"}),
    assignment_types=frozenset({"assignment", "augmented_assignment", "named_expression"}),
    call_types=frozenset({"call"}),
    identifier_types=frozenset({"identifier"}),
    member_types=frozenset({"attribute", "subscript"}),
    param_container_types=frozenset({"parameters"}),
    opaque_types=frozenset({"exec", "global_statement", "nonlocal_statement"}),
    object_fields=("object", "value"),
    callee_fields=("function",),
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
    object_fields=("object", "value"),
    callee_fields=("function",),
)

_RUBY = _LangTable(
    function_types=frozenset({"method", "singleton_method"}),
    assignment_types=frozenset({"assignment", "operator_assignment"}),
    call_types=frozenset({"call"}),
    identifier_types=frozenset({"identifier", "instance_variable", "constant"}),
    # a Ruby `a.b` method access is a `call` node handled by the call_types branch
    # (receiver descent), so only element_reference is a "member" here
    member_types=frozenset({"element_reference"}),
    param_container_types=frozenset({"method_parameters"}),
    object_fields=("receiver",),
    callee_fields=(),
    name_fields=("method",),
)

_GO = _LangTable(
    function_types=frozenset({"function_declaration", "method_declaration", "func_literal"}),
    assignment_types=frozenset({"short_var_declaration", "assignment_statement", "var_spec"}),
    call_types=frozenset({"call_expression"}),
    identifier_types=frozenset({"identifier", "field_identifier"}),
    member_types=frozenset({"selector_expression", "index_expression"}),
    param_container_types=frozenset({"parameter_list"}),
    object_fields=("operand",),
    callee_fields=("function",),
)

_JAVA = _LangTable(
    function_types=frozenset(
        {"method_declaration", "constructor_declaration", "lambda_expression"}
    ),
    assignment_types=frozenset({"variable_declarator", "assignment_expression"}),
    call_types=frozenset({"method_invocation"}),
    identifier_types=frozenset({"identifier"}),
    member_types=frozenset({"field_access", "array_access"}),
    param_container_types=frozenset({"formal_parameters"}),
    object_fields=("object", "array"),
    callee_fields=(),
    name_fields=("name",),
)

_PHP = _LangTable(
    function_types=frozenset({"function_definition", "method_declaration", "anonymous_function"}),
    assignment_types=frozenset({"assignment_expression", "augmented_assignment_expression"}),
    call_types=frozenset({"function_call_expression", "member_call_expression"}),
    identifier_types=frozenset({"variable_name", "name"}),
    member_types=frozenset({"member_access_expression", "subscript_expression"}),
    param_container_types=frozenset({"formal_parameters"}),
    object_fields=("object",),
    callee_fields=("function",),
    name_fields=("name",),
)

_LANG_TABLES: dict[str, _LangTable] = {
    "python": _PYTHON,
    "javascript": _JS,
    "typescript": _JS,
    "tsx": _JS,
    "ruby": _RUBY,
    "go": _GO,
    "java": _JAVA,
    "php": _PHP,
}


def language_supported(language: str | None) -> bool:
    return language in _LANG_TABLES


def _node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _field(node: Node, fields: tuple[str, ...]) -> Node | None:
    for f in fields:
        child = node.child_by_field_name(f)
        if child is not None:
            return child
    return None


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
            cur = _field(cur, table.object_fields)
            continue
        if t in table.call_types:
            cur = _field(cur, table.callee_fields) or _field(cur, table.object_fields)
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
            fn = _field(n, table.callee_fields) or _field(n, table.object_fields)
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
            name, _ = _callee_parts(n, table)
            if name:
                names.add(name)
        stack.extend(n.named_children)
    return names


def _callee_parts(call: Node, table: _LangTable) -> tuple[str, str]:
    """(method name, full callee text) for a call node across language shapes:
    function-field (py/js/go/php), or name+object (java), or method+receiver
    (ruby)."""
    fn = _field(call, table.callee_fields)
    if fn is not None:
        text = _node_text(fn)
        if fn.type in table.member_types:
            prop = _field(fn, ("property", "attribute", "field"))
            name = _node_text(prop) if prop is not None else text.rsplit(".", 1)[-1]
        else:
            name = text.rsplit(".", 1)[-1]
        return name, text
    # java method_invocation / ruby call: name + receiver via separate fields
    name_node = _field(call, table.name_fields)
    if name_node is not None:
        name = _node_text(name_node)
        obj = _field(call, table.object_fields)
        text = f"{_node_text(obj)}.{name}" if obj is not None else name
        return name, text
    return "", ""


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


_NONPOSITIONAL_ARG_TYPES = frozenset(
    {"keyword_argument", "spread_element", "dictionary_splat", "list_splat", "variadic_argument"}
)


def _emit_call(node: Node, table: _LangTable, out: FunctionFacts) -> None:
    args = node.child_by_field_name("arguments")
    name, text = _callee_parts(node, table)
    arg_roots: set[str] = set()
    nested: set[str] = set()
    by_pos: list[frozenset[str]] = []
    has_nonpositional = False
    if args is not None:
        for a in args.named_children:
            if a.type in _NONPOSITIONAL_ARG_TYPES:
                has_nonpositional = True
            roots = _expr_roots(a, table)
            arg_roots |= roots
            nested |= _call_names(a, table)
            by_pos.append(frozenset(roots))
    out.facts.append(
        CallFact(
            callee_name=name,
            callee_text=text,
            arg_roots=frozenset(arg_roots),
            nested_call_names=frozenset(nested),
            assign_target=_assign_target(node, table),
            line=node.start_point.row + 1,
            arg_roots_by_pos=tuple(by_pos),
            has_nonpositional=has_nonpositional,
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
