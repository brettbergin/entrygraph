import { describe, expect, it } from "vitest";
import { emptyModel, mergeNeighborhood, refocus } from "./graphLayout";
import type { Neighborhood } from "../../../api/types";

function sym(qname: string, role: "center" | "caller" | "callee") {
  return {
    id: qname.length,
    kind: "function",
    name: qname.split(".").pop()!,
    qname,
    file: null,
    line: null,
    end_line: null,
    signature: null,
    exported: null,
    role,
  };
}

const HOOD_B: Neighborhood = {
  nodes: [sym("b", "center"), sym("a", "caller"), sym("c", "callee")],
  edges: [
    { from: "a", to: "b" },
    { from: "b", to: "c" },
  ],
};

describe("mergeNeighborhood", () => {
  it("assigns signed ranks: callers negative, callees positive", () => {
    const m = mergeNeighborhood(emptyModel("b"), HOOD_B);
    expect(m.nodes.get("a")!.rank).toBe(-1);
    expect(m.nodes.get("b")!.rank).toBe(0);
    expect(m.nodes.get("c")!.rank).toBe(1);
  });

  it("marks the fetched center expanded and neighbors unexpanded", () => {
    const m = mergeNeighborhood(emptyModel("b"), HOOD_B);
    expect(m.expanded.has("b")).toBe(true);
    expect(m.nodes.get("b")!.unexpanded).toBe(false);
    expect(m.nodes.get("a")!.unexpanded).toBe(true);
  });

  it("dedupes nodes and edges across merges", () => {
    const m1 = mergeNeighborhood(emptyModel("b"), HOOD_B);
    const hoodC: Neighborhood = {
      nodes: [sym("c", "center"), sym("b", "caller"), sym("d", "callee")],
      edges: [
        { from: "b", to: "c" }, // duplicate edge
        { from: "c", to: "d" },
      ],
    };
    const m2 = mergeNeighborhood(m1, hoodC);
    expect(m2.nodes.size).toBe(4);
    expect(m2.edges.size).toBe(3);
    expect(m2.nodes.get("d")!.rank).toBe(2);
    // b stays expanded from the first merge
    expect(m2.nodes.get("b")!.unexpanded).toBe(false);
  });

  it("terminates on cycles and keeps first-visit rank", () => {
    const cyclic: Neighborhood = {
      nodes: [sym("b", "center"), sym("a", "caller"), sym("c", "callee")],
      edges: [
        { from: "a", to: "b" },
        { from: "b", to: "c" },
        { from: "c", to: "a" }, // cycle back
      ],
    };
    const m = mergeNeighborhood(emptyModel("b"), cyclic);
    // BFS from b reaches a both backward (rank -1) and forward via c (rank 2);
    // the closest assignment wins and the walk terminates.
    expect(m.nodes.get("a")!.rank).toBe(-1);
    expect(m.nodes.get("c")!.rank).toBe(1);
  });
});

describe("refocus", () => {
  it("re-ranks around the new focus without dropping nodes", () => {
    const m = mergeNeighborhood(emptyModel("b"), HOOD_B);
    const r = refocus(m, "c");
    expect(r.nodes.size).toBe(3);
    expect(r.nodes.get("c")!.rank).toBe(0);
    expect(r.nodes.get("b")!.rank).toBe(-1);
    expect(r.nodes.get("a")!.rank).toBe(-2);
  });
});
