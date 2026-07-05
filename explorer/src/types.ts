// Shapes returned by the explorer read API (src/entrygraph/explore/api.py).

export interface Repo {
  id: number;
  root_path: string;
  name: string;
  files: number;
  symbols: number;
}

export interface Stats {
  files: number;
  symbols: number;
  edges: number;
  resolved_edges: number;
  entrypoints: number;
  sink_edges: number;
  source_edges: number;
}

export interface Language {
  name: string;
  files: number;
  percent: number;
}

export interface Framework {
  name: string;
  language: string;
  confidence: number;
}

export interface StatsResponse {
  stats: Stats;
  languages: Language[];
  frameworks: Framework[];
}

export interface Symbol {
  id: number;
  kind: string;
  name: string;
  qname: string;
  file: string | null;
  line: number;
  end_line: number;
  signature: string | null;
  exported: boolean;
}

export interface Entrypoint {
  id: number;
  kind: string;
  framework: string | null;
  route: string | null;
  http_method: string | null;
  handler: Symbol | null;
}

export interface SymbolDetail {
  symbol: Symbol;
  callers: Symbol[];
  callees: Symbol[];
}

export interface Hop {
  qname: string;
  name: string;
  file: string | null;
  kind: string;
}

export interface Path {
  risk: number | null;
  verified: boolean | null;
  source_category: string | null;
  source_channel: string | null;
  source_key: string | null;
  may_continue: boolean;
  hops: Hop[];
  sink_id: string | null;
}

export interface GraphNode extends Symbol {
  role: "center" | "caller" | "callee";
}

export interface GraphEdge {
  from: string;
  to: string;
}

export interface Neighborhood {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
