"""Same-function reaching check over per-function facts (#96 Phase 2).

Tri-state result:
- ``True``  — a request-derived value provably reaches the sink argument.
- ``False`` — facts were complete, the sink call was found, and no tainted value
              reaches it (a provable non-flow — the only case that demotes).
- ``None``  — unknown (unsupported construct, sink not located, incomplete facts).
"""

from __future__ import annotations

from entrygraph.analysis.facts import AssignFact, CallFact, FunctionFacts, ReturnFact


def reaches(
    facts: FunctionFacts,
    seed_roots: set[str],
    source_lines: set[int],
    sink_line: int,
    sink_callee: str,
) -> bool | None:
    """Does a value derived from a seed reach the sink call's arguments?

    ``seed_roots`` are initially-tainted identifiers (handler params and the
    assign-targets of explicit source accessor calls). ``source_lines`` mark
    accessor call sites so an inline ``sink(accessor())`` is caught.
    """
    tainted: set[str] = set(seed_roots)

    # Seed from a source-accessor call site: the value read on that line is
    # tainted. Bound to a variable via an assignment (`q = request.args[...]`) or
    # via the call's own assign_target (`q = request.args.get(...)`).
    for f in facts.facts:
        if f.line in source_lines:
            if isinstance(f, AssignFact):
                tainted.update(f.targets)
            elif isinstance(f, CallFact) and f.assign_target:
                tainted.add(f.assign_target)

    # Fixpoint over assignments/calls; bounded by fact count + 1 (handles loops
    # and out-of-order defs). Any syntactic dependence propagates taint.
    for _ in range(len(facts.facts) + 1):
        changed = False
        for f in facts.facts:
            if isinstance(f, AssignFact):
                if f.rhs_roots & tainted:
                    for tgt in f.targets:
                        if tgt not in tainted:
                            tainted.add(tgt)
                            changed = True
            elif isinstance(f, CallFact) and f.assign_target and f.assign_target not in tainted:
                if f.arg_roots & tainted:
                    tainted.add(f.assign_target)
                    changed = True
            elif isinstance(f, ReturnFact):
                continue
        if not changed:
            break

    # Locate the sink call on its line and test its arguments.
    sink_fact = next(
        (
            f
            for f in facts.facts
            if isinstance(f, CallFact) and f.line == sink_line and f.callee_name == sink_callee
        ),
        None,
    )
    if sink_fact is None:
        # try line-only match (callee canonicalization may differ from source text)
        sink_fact = next(
            (f for f in facts.facts if isinstance(f, CallFact) and f.line == sink_line),
            None,
        )
    if sink_fact is None:
        return None
    if sink_fact.arg_roots & tainted:
        return True
    # inline accessor in the sink argument: sink(request.args.get("q"))
    if sink_fact.line in source_lines:
        return True
    if not facts.complete:
        return None
    return False
