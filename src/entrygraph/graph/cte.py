"""Recursive-CTE reachability engine — the SQL-side fallback.

Same paths()/reachable() contract as AdjacencyCache, but the traversal runs
inside SQLite via a recursive CTE instead of loading adjacency into memory.
Path reconstruction and cycle-guarding use an encoded path string, which is why
this is the fallback (it does more work per row); the memory engine is primary.
The two are kept behaviorally identical by the parametrized reachability tests:
the walk stops at the first sink on a path (the recursive arm does not expand
past a node already in the sink set, matching the memory DFS), and the outer
query is bounded by LIMIT :max_paths.
"""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from entrygraph.graph.adjacency import Hop, PathList, _candidate_cap
from entrygraph.kinds import EdgeKind

_SEP = ","


class CteEngine:
    def __init__(
        self,
        session: Session,
        kinds: frozenset[str],
        min_confidence: int = 0,
        include_cha: bool = True,
        repo_id: int = 0,
    ) -> None:
        self.session = session
        self.kind_values = [EdgeKind(k).value for k in kinds]
        self.min_confidence = min_confidence
        self.include_cha = include_cha
        self.repo_id = repo_id  # scope the walk to one repo in a global DB (#116)

    def reachable(self, sources: set[int], sinks: set[int], max_depth: int) -> bool:
        if sources & sinks:
            return True
        return bool(self.paths(sources, sinks, max_depth=max_depth, max_paths=1))

    def paths(
        self, sources: set[int], sinks: set[int], max_depth: int = 25, max_paths: int = 10
    ) -> PathList:
        results: list[list[tuple[int, Hop | None]]] = []
        cap = _candidate_cap(max_paths)
        # A source that is itself a sink used to be emitted as a length-1 path, but
        # with `--source '*'` (matches every sink) those degenerate single-node rows
        # flood the output and out-rank real chains (#47). A taint path needs a
        # distinct source and sink (>= 2 nodes), so we no longer seed them; the
        # recursive walk below already starts from real edges (src NOT IN sinks).

        rows = self.session.execute(
            text(
                """
                WITH RECURSIVE walk(node, nodes, lines, kinds, ids, confs, depth) AS (
                    SELECT e.dst_symbol_id,
                           :sep || e.src_symbol_id || :sep || e.dst_symbol_id || :sep,
                           :sep || e.line || :sep,
                           :sep || e.kind || :sep,
                           :sep || e.id || :sep,
                           :sep || e.confidence || :sep,
                           1
                    FROM edges e
                    WHERE e.src_symbol_id IN :sources
                      AND e.repo_id = :repo_id
                      AND e.src_symbol_id NOT IN :sinks
                      AND e.dst_symbol_id IS NOT NULL
                      AND e.kind IN :kinds
                      AND e.confidence >= :minconf
                      AND (:include_cha OR e.via IS NULL OR e.via != 'cha')
                    UNION ALL
                    SELECT e.dst_symbol_id,
                           w.nodes || e.dst_symbol_id || :sep,
                           w.lines || e.line || :sep,
                           w.kinds || e.kind || :sep,
                           w.ids || e.id || :sep,
                           w.confs || e.confidence || :sep,
                           w.depth + 1
                    FROM walk w
                    JOIN edges e ON e.src_symbol_id = w.node
                    WHERE w.depth < :max_depth
                      AND e.repo_id = :repo_id
                      AND w.node NOT IN :sinks
                      AND e.dst_symbol_id IS NOT NULL
                      AND e.kind IN :kinds
                      AND e.confidence >= :minconf
                      AND (:include_cha OR e.via IS NULL OR e.via != 'cha')
                      AND w.nodes NOT LIKE '%' || :sep || e.dst_symbol_id || :sep || '%'
                )
                SELECT nodes, lines, kinds, ids, confs, depth FROM walk
                WHERE node IN :sinks
                ORDER BY depth
                LIMIT :cap
                """
            ).bindparams(
                bindparam("sources", expanding=True),
                bindparam("sinks", expanding=True),
                bindparam("kinds", expanding=True),
            ),
            {
                "sources": list(sources),
                "sinks": list(sinks),
                "kinds": self.kind_values,
                "minconf": self.min_confidence,
                "include_cha": 1 if self.include_cha else 0,
                "max_depth": max_depth,
                "cap": cap,
                "sep": _SEP,
                "repo_id": self.repo_id,
            },
        ).all()

        for nodes_str, lines_str, kinds_str, ids_str, confs_str, _depth in rows:
            if len(results) >= cap:
                break
            path = self._decode(nodes_str, lines_str, kinds_str, ids_str, confs_str)
            if path is not None:
                results.append(path)

        results.sort(key=lambda p: (len(p), [n for n, _ in p]))
        # The CTE has no per-visit budget (SQLite bounds it), so it never
        # silently under-returns the way the memory DFS could — never truncated.
        return PathList.of(results, truncated=False)

    @staticmethod
    def _decode(
        nodes_str, lines_str, kinds_str, ids_str, confs_str
    ) -> list[tuple[int, Hop | None]] | None:
        node_ids = [int(x) for x in nodes_str.strip(_SEP).split(_SEP) if x]
        lines = [int(x) for x in lines_str.strip(_SEP).split(_SEP) if x]
        kinds = [x for x in kinds_str.strip(_SEP).split(_SEP) if x]
        edge_ids = [int(x) for x in ids_str.strip(_SEP).split(_SEP) if x]
        confs = [int(x) for x in confs_str.strip(_SEP).split(_SEP) if x]
        if len(lines) != len(node_ids) - 1:
            return None
        path: list[tuple[int, Hop | None]] = [(node_ids[0], None)]
        for i in range(1, len(node_ids)):
            path.append(
                (
                    node_ids[i],
                    Hop(node_ids[i], kinds[i - 1], lines[i - 1], confs[i - 1], edge_ids[i - 1]),
                )
            )
        return path
