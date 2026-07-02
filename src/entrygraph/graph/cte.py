"""Recursive-CTE reachability engine — the SQL-side fallback.

Same paths()/reachable() contract as AdjacencyCache, but the traversal runs
inside SQLite via a recursive CTE instead of loading adjacency into memory.
Path reconstruction and cycle-guarding use an encoded path string, which is why
this is the fallback (it does more work per row); the memory engine is primary.
The two are kept behaviorally identical by the parametrized reachability tests.
"""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from entrygraph.graph.adjacency import Hop
from entrygraph.kinds import EdgeKind

_SEP = ","


class CteEngine:
    def __init__(self, session: Session, kinds: frozenset[str], min_confidence: int = 0,
                 include_cha: bool = True) -> None:
        self.session = session
        self.kind_values = [EdgeKind(k).value for k in kinds]
        self.min_confidence = min_confidence
        self.include_cha = include_cha

    def reachable(self, sources: set[int], sinks: set[int], max_depth: int) -> bool:
        if sources & sinks:
            return True
        return bool(self.paths(sources, sinks, max_depth=max_depth, max_paths=1))

    def paths(
        self, sources: set[int], sinks: set[int], max_depth: int = 25, max_paths: int = 10
    ) -> list[list[tuple[int, Hop | None]]]:
        results: list[list[tuple[int, Hop | None]]] = []
        # a source that is itself a sink is a length-1 path (matches memory engine)
        for src in sorted(sources & sinks):
            results.append([(src, None)])

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
                      AND e.dst_symbol_id IS NOT NULL
                      AND e.kind IN :kinds
                      AND e.confidence >= :minconf
                      AND (:include_cha OR e.via IS NULL OR e.via != 'cha')
                      AND w.nodes NOT LIKE '%' || :sep || e.dst_symbol_id || :sep || '%'
                )
                SELECT nodes, lines, kinds, ids, confs, depth FROM walk
                WHERE node IN :sinks
                ORDER BY depth
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
                "sep": _SEP,
            },
        ).all()

        for nodes_str, lines_str, kinds_str, ids_str, confs_str, _depth in rows:
            if len(results) >= max_paths:
                break
            path = self._decode(nodes_str, lines_str, kinds_str, ids_str, confs_str)
            if path is not None:
                results.append(path)

        results.sort(key=lambda p: (len(p), [n for n, _ in p]))
        return results[:max_paths]

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
                (node_ids[i], Hop(node_ids[i], kinds[i - 1], lines[i - 1],
                                  confs[i - 1], edge_ids[i - 1]))
            )
        return path
