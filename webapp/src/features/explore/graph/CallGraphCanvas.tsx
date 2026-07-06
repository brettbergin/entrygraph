// D3-owns-DOM call-graph canvas. React passes the model + callbacks down; the
// simulation mutates positions at tick rate inside the SVG without touching
// React state. Layout: force simulation with layered x-forces — BFS rank
// (callers left, focus center, callees right) drives forceX, so the picture
// reads like a layered DAG but tolerates cycles and animates on merge.

import { useEffect, useRef } from "react";
import { drag, type D3DragEvent } from "d3-drag";
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior } from "d3-zoom";
import type { GraphModel, LayoutNode } from "./graphLayout";
import { resolvePalette } from "./graphStyle";

const COLUMN_WIDTH = 240;
const NODE_RADIUS = 7;

interface SimNode extends SimulationNodeDatum, LayoutNode {}

interface SimLink extends SimulationLinkDatum<SimNode> {
  key: string;
}

export interface CanvasHandle {
  zoomToFit: () => void;
}

export function CallGraphCanvas({
  model,
  onSelect,
  onExpand,
  onRefocus,
  handleRef,
}: {
  model: GraphModel;
  onSelect: (qname: string) => void;
  onExpand: (qname: string) => void;
  onRefocus: (qname: string) => void;
  handleRef?: (h: CanvasHandle) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  // node positions survive re-renders so merges animate instead of restarting
  const positions = useRef(new Map<string, { x: number; y: number }>());

  useEffect(() => {
    const svgEl = svgRef.current;
    if (!svgEl) return;
    const palette = resolvePalette(svgEl);
    const { width, height } = svgEl.getBoundingClientRect();

    const nodes: SimNode[] = [...model.nodes.values()].map((n) => ({
      ...n,
      ...(positions.current.get(n.qname) ?? {
        x: n.rank * COLUMN_WIDTH + (Math.random() - 0.5) * 40,
        y: (Math.random() - 0.5) * 200,
      }),
    }));
    const byQname = new Map(nodes.map((n) => [n.qname, n]));
    const links: SimLink[] = [...model.edges.values()]
      .filter((e) => byQname.has(e.from) && byQname.has(e.to))
      .map((e) => ({ source: e.from, target: e.to, key: `${e.from}->${e.to}` }));

    const svg = select(svgEl);
    svg.selectAll("*").remove();

    // arrowhead marker
    svg
      .append("defs")
      .append("marker")
      .attr("id", "arrow")
      .attr("viewBox", "0 -4 8 8")
      .attr("refX", 8 + NODE_RADIUS)
      .attr("refY", 0)
      .attr("markerWidth", 7)
      .attr("markerHeight", 7)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-4L8,0L0,4")
      .attr("fill", palette.edge);

    const root = svg.append("g");

    const linkSel = root
      .append("g")
      .selectAll("line")
      .data(links, (d) => (d as SimLink).key)
      .join("line")
      .attr("stroke", palette.edge)
      .attr("stroke-width", 1.2)
      .attr("marker-end", "url(#arrow)");

    const nodeSel = root
      .append("g")
      .selectAll<SVGGElement, SimNode>("g")
      .data(nodes, (d) => (d as SimNode).qname)
      .join("g")
      .style("cursor", "pointer");

    nodeSel
      .append("circle")
      .attr("r", (d) => (d.qname === model.focus ? NODE_RADIUS + 2 : NODE_RADIUS))
      .attr("fill", (d) =>
        d.qname === model.focus
          ? palette.nodeFocus
          : d.unexpanded
            ? palette.nodeUnexpanded
            : palette.node,
      )
      .attr("stroke", (d) => (d.qname === model.focus ? palette.nodeFocus : palette.stroke))
      .attr("stroke-width", 1.5);

    // unexpanded hint ring: "there's more here — click to expand"
    nodeSel
      .filter((d) => d.unexpanded && d.qname !== model.focus)
      .append("circle")
      .attr("r", NODE_RADIUS + 4)
      .attr("fill", "none")
      .attr("stroke", palette.stroke)
      .attr("stroke-dasharray", "2 3");

    nodeSel
      .append("text")
      .text((d) => d.name)
      .attr("dy", -NODE_RADIUS - 6)
      .attr("text-anchor", "middle")
      .attr("font-size", 11)
      .attr("font-family", "ui-monospace, SFMono-Regular, Menlo, monospace")
      .attr("fill", (d) => (d.qname === model.focus ? palette.text : palette.textMuted))
      .attr("paint-order", "stroke")
      .attr("stroke", palette.halo)
      .attr("stroke-width", 3);

    nodeSel.append("title").text((d) => `${d.qname}\n${d.kind}${d.file ? ` · ${d.file}` : ""}`);

    nodeSel.on("click", (event: MouseEvent, d: SimNode) => {
      event.stopPropagation();
      onSelect(d.qname);
      if (d.unexpanded) onExpand(d.qname);
    });
    nodeSel.on("dblclick", (event: MouseEvent, d: SimNode) => {
      event.stopPropagation();
      onRefocus(d.qname);
    });

    const simulation = forceSimulation<SimNode>(nodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(links)
          .id((d) => d.qname)
          .distance(110)
          .strength(0.3),
      )
      .force("charge", forceManyBody().strength(-220))
      .force("x", forceX<SimNode>((d) => d.rank * COLUMN_WIDTH).strength(0.5))
      .force("y", forceY<SimNode>(0).strength(0.05))
      .force("collide", forceCollide<SimNode>(NODE_RADIUS * 3.2));

    simulation.on("tick", () => {
      linkSel
        .attr("x1", (d) => (d.source as SimNode).x!)
        .attr("y1", (d) => (d.source as SimNode).y!)
        .attr("x2", (d) => (d.target as SimNode).x!)
        .attr("y2", (d) => (d.target as SimNode).y!);
      nodeSel.attr("transform", (d) => `translate(${d.x},${d.y})`);
      for (const n of nodes) positions.current.set(n.qname, { x: n.x!, y: n.y! });
    });

    nodeSel.call(
      drag<SVGGElement, SimNode>()
        .on("start", (event: D3DragEvent<SVGGElement, SimNode, SimNode>, d) => {
          if (!event.active) simulation.alphaTarget(0.2).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (event: D3DragEvent<SVGGElement, SimNode, SimNode>, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on("end", (event: D3DragEvent<SVGGElement, SimNode, SimNode>, d) => {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        }),
    );

    const zoomBehavior: ZoomBehavior<SVGSVGElement, unknown> = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 3])
      .on("zoom", (event) => root.attr("transform", event.transform.toString()));
    svg.call(zoomBehavior);
    // start centered
    svg.call(zoomBehavior.transform, zoomIdentity.translate(width / 2, height / 2));

    handleRef?.({
      zoomToFit: () => {
        const xs = nodes.map((n) => n.x ?? 0);
        const ys = nodes.map((n) => n.y ?? 0);
        if (!xs.length) return;
        const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
        const [minY, maxY] = [Math.min(...ys), Math.max(...ys)];
        const scale = Math.min(
          2,
          0.85 / Math.max((maxX - minX) / width, (maxY - minY) / height, 0.001),
        );
        svg.call(
          zoomBehavior.transform,
          zoomIdentity
            .translate(width / 2, height / 2)
            .scale(scale)
            .translate(-(minX + maxX) / 2, -(minY + maxY) / 2),
        );
      },
    });

    return () => {
      simulation.stop();
    };
  }, [model, onSelect, onExpand, onRefocus, handleRef]);

  return <svg ref={svgRef} role="img" aria-label="call graph" />;
}
