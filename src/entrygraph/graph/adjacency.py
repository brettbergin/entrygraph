"""In-memory adjacency over the edges table — the primary reachability engine.

One indexed scan loads all resolved edges of the requested kinds into forward
and reverse adjacency dicts; every subsequent traversal is pure-Python BFS/DFS.
The cache is keyed by (edge kinds, index generation) and dropped on re-index.

Confidence and class-hierarchy (CHA) filtering happen per traversal, not at
build time, so a single cache serves every ``min_confidence`` / ``include_cha``
combination instead of building (and retaining) a full duplicate graph per combo.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.db.models import Edge
from entrygraph.kinds import EdgeKind

_MAX_DFS_VISITS = 200_000  # hard bound on path-enumeration work


@dataclass(frozen=True, slots=True)
class Hop:
    dst: int
    kind: str
    line: int
    confidence: int
    edge_id: int = 0
    via: str | None = None


def _passes(hop: Hop, min_confidence: int, include_cha: bool) -> bool:
    """Per-traversal filter: below the confidence floor, or a CHA edge when CHA is
    opted out. Applied at traversal time so one cache serves all combinations."""
    if hop.confidence < min_confidence:
        return False
    return include_cha or hop.via != "cha"


class AdjacencyCache:
    def __init__(self, generation: int, kinds: frozenset[str]) -> None:
        self.generation = generation
        self.kinds = kinds
        self.forward: dict[int, list[Hop]] = {}
        self.reverse: dict[int, list[Hop]] = {}

    @classmethod
    def build(
        cls,
        session: Session,
        generation: int,
        kinds: frozenset[str],
    ) -> AdjacencyCache:
        # Load every resolved edge of these kinds regardless of confidence/via;
        # traversals filter. One cache then serves all min_confidence/include_cha
        # settings instead of a full duplicate graph per combination.
        cache = cls(generation, kinds)
        stmt = select(
            Edge.src_symbol_id,
            Edge.dst_symbol_id,
            Edge.kind,
            Edge.line,
            Edge.confidence,
            Edge.id,
            Edge.via,
        ).where(
            Edge.kind.in_([EdgeKind(k) for k in kinds]),
            Edge.dst_symbol_id.is_not(None),
        )
        for src, dst, kind, line, confidence, edge_id, via in session.execute(stmt):
            cache.forward.setdefault(src, []).append(
                Hop(dst, kind.value, line, confidence, edge_id, via)
            )
            cache.reverse.setdefault(dst, []).append(
                Hop(src, kind.value, line, confidence, edge_id, via)
            )
        for adjacency in (cache.forward, cache.reverse):
            for hops in adjacency.values():
                hops.sort(key=lambda h: (h.dst, h.line))
        return cache

    # ---------------- traversals ----------------

    def neighborhood(
        self,
        starts: set[int],
        depth: int,
        direction: str,
        min_confidence: int = 0,
        include_cha: bool = True,
    ) -> set[int]:
        """All nodes within `depth` hops (excluding the starts themselves)."""
        adjacency = self.forward if direction == "out" else self.reverse
        seen = set(starts)
        frontier = set(starts)
        found: set[int] = set()
        for _ in range(depth):
            next_frontier: set[int] = set()
            for node in frontier:
                for hop in adjacency.get(node, ()):
                    if not _passes(hop, min_confidence, include_cha):
                        continue
                    if hop.dst not in seen:
                        seen.add(hop.dst)
                        next_frontier.add(hop.dst)
                        found.add(hop.dst)
            if not next_frontier:
                break
            frontier = next_frontier
        return found

    def reachable(
        self,
        sources: set[int],
        sinks: set[int],
        max_depth: int,
        min_confidence: int = 0,
        include_cha: bool = True,
    ) -> bool:
        if sources & sinks:
            return True
        seen = set(sources)
        frontier = deque((s, 0) for s in sources)
        while frontier:
            node, depth = frontier.popleft()
            if depth >= max_depth:
                continue
            for hop in self.forward.get(node, ()):
                if not _passes(hop, min_confidence, include_cha):
                    continue
                if hop.dst in sinks:
                    return True
                if hop.dst not in seen:
                    seen.add(hop.dst)
                    frontier.append((hop.dst, depth + 1))
        return False

    def paths(
        self,
        sources: set[int],
        sinks: set[int],
        max_depth: int = 25,
        max_paths: int = 10,
        min_confidence: int = 0,
        include_cha: bool = True,
    ) -> list[list[tuple[int, Hop | None]]]:
        """Enumerate simple paths as [(symbol_id, hop_into_it | None), ...].

        DFS with an on-path visited set; neighbor order is deterministic.
        Results are sorted shortest-first. A global visit budget bounds work on
        pathological graphs; enumeration also stops at max_paths.
        """
        results: list[list[tuple[int, Hop | None]]] = []
        budget = _MAX_DFS_VISITS

        for source in sorted(sources):
            if budget <= 0 or len(results) >= max_paths:
                break
            stack: list[tuple[int, Hop | None]] = [(source, None)]
            on_path = {source}

            def dfs(node: int, depth: int) -> None:
                nonlocal budget
                if budget <= 0 or len(results) >= max_paths:
                    return
                budget -= 1
                if node in sinks:
                    results.append(list(stack))
                    return
                if depth >= max_depth:
                    return
                for hop in self.forward.get(node, ()):
                    if not _passes(hop, min_confidence, include_cha):
                        continue
                    if hop.dst in on_path:
                        continue
                    stack.append((hop.dst, hop))
                    on_path.add(hop.dst)
                    dfs(hop.dst, depth + 1)
                    on_path.discard(hop.dst)
                    stack.pop()

            dfs(source, 0)

        results.sort(key=lambda p: (len(p), [n for n, _ in p]))
        return results[:max_paths]
