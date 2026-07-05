import { TextInput } from "@primer/react";
import { useState } from "react";
import { api } from "../api";
import type { GraphNode, Neighborhood } from "../types";
import { Empty, ErrorFlash, Loading, useAsync } from "./ui";

const NODE_W = 300;
const NODE_H = 34;
const ROW = 48;
const COL = { caller: 20, center: 400, callee: 780 };
const WIDTH = COL.callee + NODE_W + 20;

const short = (s: string) => (s.length > 40 ? "…" + s.slice(-39) : s);

export function GraphView({
  repoId,
  qname,
  onFocus,
}: {
  repoId: number;
  qname: string;
  onFocus: (q: string) => void;
}) {
  const [input, setInput] = useState(qname);

  return (
    <div>
      <form
        className="row section"
        onSubmit={(e) => {
          e.preventDefault();
          if (input.trim()) onFocus(input.trim());
        }}
      >
        <TextInput
          className="mono"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="symbol qualified name…"
          style={{ minWidth: 420 }}
        />
        <span className="muted fs0">callers on the left, callees on the right — click to re-center</span>
      </form>
      {qname ? (
        <Graph key={qname} repoId={repoId} qname={qname} onFocus={onFocus} setInput={setInput} />
      ) : (
        <Empty>Enter a symbol qname (or open one from the Symbols / Entrypoints tabs).</Empty>
      )}
    </div>
  );
}

function Graph({
  repoId,
  qname,
  onFocus,
  setInput,
}: {
  repoId: number;
  qname: string;
  onFocus: (q: string) => void;
  setInput: (q: string) => void;
}) {
  const { data, error, loading } = useAsync<Neighborhood>(
    () => api.graph(repoId, qname),
    [repoId, qname],
  );
  if (error) return <ErrorFlash message={error} />;
  if (loading || !data) return <Loading label="Building graph…" />;

  const center = data.nodes.find((n) => n.role === "center")!;
  const callers = data.nodes.filter((n) => n.role === "caller");
  const callees = data.nodes.filter((n) => n.role === "callee");
  const height = Math.max(callers.length, callees.length, 1) * ROW + 20;
  const yFor = (i: number, count: number) => (height - count * ROW) / 2 + i * ROW + 10;
  const centerY = height / 2 - NODE_H / 2;

  const recenter = (q: string) => {
    setInput(q);
    onFocus(q);
  };

  return (
    <div
      className="card section"
      style={{ overflow: "auto", padding: 12, maxHeight: "74vh" }}
    >
      <svg width={WIDTH} height={height} style={{ display: "block" }}>
        {callers.map((_, i) => (
          <Edge key={"ce" + i} x1={COL.caller + NODE_W} y1={yFor(i, callers.length) + NODE_H / 2}
            x2={COL.center} y2={centerY + NODE_H / 2} />
        ))}
        {callees.map((_, i) => (
          <Edge key={"ee" + i} x1={COL.center + NODE_W} y1={centerY + NODE_H / 2}
            x2={COL.callee} y2={yFor(i, callees.length) + NODE_H / 2} />
        ))}
        {callers.map((n, i) => (
          <Node key={n.id} node={n} x={COL.caller} y={yFor(i, callers.length)} onClick={() => recenter(n.qname)} />
        ))}
        {callees.map((n, i) => (
          <Node key={n.id} node={n} x={COL.callee} y={yFor(i, callees.length)} onClick={() => recenter(n.qname)} />
        ))}
        <Node node={center} x={COL.center} y={centerY} center />
      </svg>
    </div>
  );
}

function Edge({ x1, y1, x2, y2 }: { x1: number; y1: number; x2: number; y2: number }) {
  const mx = (x1 + x2) / 2;
  return (
    <path
      d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
      fill="none"
      stroke="var(--border)"
      strokeWidth={1.5}
    />
  );
}

function Node({
  node,
  x,
  y,
  center,
  onClick,
}: {
  node: GraphNode;
  x: number;
  y: number;
  center?: boolean;
  onClick?: () => void;
}) {
  return (
    <g style={{ cursor: onClick ? "pointer" : "default" }} onClick={onClick}>
      <title>{node.qname}</title>
      <rect
        x={x} y={y} width={NODE_W} height={NODE_H} rx={7}
        fill="var(--canvas-subtle)"
        stroke={center ? "var(--accent)" : "var(--border)"}
        strokeWidth={center ? 2 : 1}
      />
      <text
        x={x + 12} y={y + NODE_H / 2 + 4}
        fill={center ? "var(--fg)" : "var(--fg-muted)"}
        fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
        fontSize={12}
      >
        {short(node.name || node.qname)}
      </text>
    </g>
  );
}
