// Pure graph-layout helpers: BFS rank assignment and neighborhood merging.
// Kept free of D3/DOM so they're unit-testable; cycles are fine — first-visit
// rank wins.

import type { Neighborhood } from "../../../api/types";

export interface LayoutNode {
  qname: string;
  name: string;
  kind: string;
  file: string | null;
  line: number | null;
  /** signed BFS rank from the focus: callers negative, callees positive */
  rank: number;
  /** true while the node's own neighborhood hasn't been fetched yet */
  unexpanded: boolean;
}

export interface LayoutEdge {
  from: string;
  to: string;
}

export interface GraphModel {
  focus: string;
  nodes: Map<string, LayoutNode>;
  edges: Map<string, LayoutEdge>; // keyed "from->to"
  expanded: Set<string>;
}

export function edgeKey(e: LayoutEdge): string {
  return `${e.from}->${e.to}`;
}

export function emptyModel(focus: string): GraphModel {
  return { focus, nodes: new Map(), edges: new Map(), expanded: new Set() };
}

/** Merge a fetched neighborhood into the model (dedupe by qname / edge key). */
export function mergeNeighborhood(model: GraphModel, hood: Neighborhood): GraphModel {
  const nodes = new Map(model.nodes);
  const edges = new Map(model.edges);
  const expanded = new Set(model.expanded);
  const center = hood.nodes.find((n) => n.role === "center");
  if (center) expanded.add(center.qname);

  for (const n of hood.nodes) {
    const existing = nodes.get(n.qname);
    nodes.set(n.qname, {
      qname: n.qname,
      name: n.name,
      kind: n.kind,
      file: n.file,
      line: n.line,
      rank: existing?.rank ?? 0, // ranks recomputed below
      unexpanded: existing ? existing.unexpanded && n.qname !== center?.qname : n.role !== "center",
    });
  }
  for (const e of hood.edges) {
    edges.set(edgeKey(e), { from: e.from, to: e.to });
  }
  const merged: GraphModel = { focus: model.focus, nodes, edges, expanded };
  assignRanks(merged);
  return merged;
}

/**
 * Signed BFS ranks from the focus node: following edges forward (focus → callee)
 * increments rank, backward (caller → focus) decrements. A node reachable both
 * ways keeps its first-assigned (closest) rank, which also terminates on cycles.
 */
export function assignRanks(model: GraphModel): void {
  const out = new Map<string, string[]>();
  const inn = new Map<string, string[]>();
  for (const e of model.edges.values()) {
    out.set(e.from, [...(out.get(e.from) ?? []), e.to]);
    inn.set(e.to, [...(inn.get(e.to) ?? []), e.from]);
  }
  const ranks = new Map<string, number>();
  if (model.nodes.has(model.focus)) ranks.set(model.focus, 0);
  const queue: string[] = model.focus ? [model.focus] : [];
  while (queue.length) {
    const q = queue.shift()!;
    const r = ranks.get(q)!;
    for (const callee of out.get(q) ?? []) {
      if (!ranks.has(callee)) {
        ranks.set(callee, r + 1);
        queue.push(callee);
      }
    }
    for (const caller of inn.get(q) ?? []) {
      if (!ranks.has(caller)) {
        ranks.set(caller, r - 1);
        queue.push(caller);
      }
    }
  }
  for (const node of model.nodes.values()) {
    node.rank = ranks.get(node.qname) ?? 0;
  }
}

/** Refocus without dropping already-fetched nodes: keep the graph, re-rank. */
export function refocus(model: GraphModel, focus: string): GraphModel {
  const next: GraphModel = {
    focus,
    nodes: new Map(model.nodes),
    edges: new Map(model.edges),
    expanded: new Set(model.expanded),
  };
  assignRanks(next);
  return next;
}
