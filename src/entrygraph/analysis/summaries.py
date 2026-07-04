"""Bounded interprocedural taint verification (#96 Phase 3).

Extends the same-function reaching check across a small number of call hops by
mapping tainted values to argument *positions* at each hop and into the next
function's parameters. Deliberately conservative: any ambiguity (non-positional
args, position out of range, an unanalyzable hop, a cycle) yields ``None`` (no
verdict), never a wrong ``False`` — a false demotion would hide a real finding.
"""

from __future__ import annotations

from entrygraph.analysis.facts import CallFact, FunctionFacts
from entrygraph.analysis.reaching import propagate

# languages whose method calls carry an implicit receiver (self/this) as param 0,
# so a call's positional arg p maps to the method's param p+1
_IMPLICIT_RECEIVER_LANGS = frozenset({"python", "ruby"})


def _tainted_positions_at_call(
    facts: FunctionFacts, tainted: set[str], call_line: int
) -> set[int] | None:
    """Positional-arg indices tainted at the call on ``call_line``. None if that
    call can't be found or has non-positional args (position mapping unknown)."""
    call = next(
        (f for f in facts.facts if isinstance(f, CallFact) and f.line == call_line),
        None,
    )
    if call is None or call.has_nonpositional:
        return None
    return {i for i, roots in enumerate(call.arg_roots_by_pos) if roots & tainted}


def _seed_from_positions(
    facts: FunctionFacts, positions: set[int], language: str, is_method: bool
) -> set[str] | None:
    """Parameter names tainted by incoming ``positions``. None if a position is
    out of range (arity mismatch -> can't map safely)."""
    params = facts.params
    offset = 1 if is_method and language in _IMPLICIT_RECEIVER_LANGS else 0
    seed: set[str] = set()
    for p in positions:
        idx = p + offset
        if idx >= len(params):
            return None  # arity mismatch / variadic tail -> unsafe to map
        seed.add(params[idx])
    return seed


def verify_interprocedural(
    symbols,
    edge_lines: list[int],
    seed_roots: set[str],
    source_lines: set[int],
    languages: list[str | None],
    is_method: list[bool],
    facts_list: list[FunctionFacts | None],
    sink_callee: str,
    hop_limit: int,
) -> bool | None:
    """Tri-state verdict for a source->...->sink path spanning multiple functions.

    ``facts_list[i]`` are the facts for function ``symbols[i]`` (the last symbol is
    the external sink, so ``facts_list`` covers ``symbols[:-1]``). ``edge_lines[i]``
    is the call-site line in ``symbols[i]`` that invokes ``symbols[i+1]``.
    """
    n_functions = len(facts_list)  # symbols[:-1]
    interior_hops = n_functions - 1  # calls between functions (last is the sink call)
    if interior_hops > hop_limit:
        return None
    if any(f is None for f in facts_list):
        return None

    visited: set[tuple[int, frozenset[int]]] = set()
    # Function 0: seed and propagate.
    facts0 = facts_list[0]
    assert facts0 is not None
    tainted = propagate(facts0, seed_roots, source_lines)

    # Walk each interior hop, carrying tainted argument positions forward.
    positions: set[int] | None = None
    for i in range(interior_hops):
        facts_i = facts_list[i]
        assert facts_i is not None
        if i > 0:
            # seed function i from the positions tainted by the previous hop
            if positions is None:
                return None
            key = (symbols[i].id, frozenset(positions))
            if key in visited:  # recursion / cycle -> no verdict
                return None
            visited.add(key)
            seed = _seed_from_positions(facts_i, positions, languages[i] or "", is_method[i])
            if seed is None:
                return None
            tainted = propagate(facts_i, seed, set())
        positions = _tainted_positions_at_call(facts_i, tainted, edge_lines[i])
        if positions is None:
            return None

    # Terminal function: seed from the last hop's positions (unless single-function),
    # then test the sink call.
    terminal = facts_list[-1]
    assert terminal is not None
    if interior_hops > 0:
        if positions is None:
            return None
        seed = _seed_from_positions(terminal, positions, languages[-1] or "", is_method[-1])
        if seed is None:
            return None
        tainted = propagate(terminal, seed, set())

    sink_line = edge_lines[-1]
    sink_fact = next(
        (
            f
            for f in terminal.facts
            if isinstance(f, CallFact) and f.line == sink_line and f.callee_name == sink_callee
        ),
        None,
    )
    if sink_fact is None:
        sink_fact = next(
            (f for f in terminal.facts if isinstance(f, CallFact) and f.line == sink_line),
            None,
        )
    if sink_fact is None:
        return None
    if sink_fact.arg_roots & tainted:
        return True
    if interior_hops == 0 and sink_line in source_lines:
        return True  # inline accessor in the sink arg (same-function case)
    if not terminal.complete:
        return None
    return False
