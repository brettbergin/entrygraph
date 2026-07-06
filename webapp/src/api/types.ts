// API response shapes — the /api/v1 contract (see server/routes/serializers.py).

export interface Me {
  user: { id: number | null; name: string; role: "admin" | "viewer"; via: string };
  auth_mode: "none" | "oidc";
  auth_disabled: boolean;
  sentinel_enabled: boolean;
}

export interface RepoSourceInfo {
  url: string | null;
  ref: string | null;
  depth: number;
  include_tests: boolean;
}

export interface Repo {
  id: number;
  root_path: string;
  name: string;
  files: number;
  symbols: number;
  indexed_at: string | null;
  sentinel: boolean;
  source: RepoSourceInfo | null;
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
  bytes: number;
  percent: number;
}

export interface Framework {
  name: string;
  language: string;
  confidence: number;
  evidence: string[];
}

export interface Detection {
  languages: Language[];
  frameworks: Framework[];
}

export interface Symbol {
  id: number;
  kind: string;
  name: string;
  qname: string;
  file: string | null;
  line: number | null;
  end_line: number | null;
  signature: string | null;
  exported: boolean | null;
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

export type NodeRole = "center" | "caller" | "callee";

export interface GraphNode extends Symbol {
  role: NodeRole;
}

export interface GraphEdge {
  from: string;
  to: string;
}

export interface Neighborhood {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface PathHop {
  qname: string;
  name: string;
  file: string | null;
  kind: string;
}

export interface PathEdge {
  kind: string;
  line: number;
  confidence: number;
  via: string | null;
  sink_id: string | null;
  constant_args: boolean;
  sanitized_by: string[];
}

export interface CallPath {
  risk: number | null;
  verified: boolean | null;
  min_confidence: number;
  source_category: string | null;
  source_kind: string | null;
  source_channel: string | null;
  source_key: string | null;
  may_continue: boolean;
  sink_id: string | null;
  source_snippet: string | null;
  sink_snippet: string | null;
  hops: PathHop[];
  edges: PathEdge[];
}

export interface PathsResponse {
  paths: CallPath[];
  mode: "precise" | "widened" | "strict" | "explicit" | null;
  truncated: boolean;
}

export interface PathsQuery {
  source_category?: string;
  sink_category?: string;
  source?: string;
  sink?: string;
  max_depth?: number;
  max_paths?: number;
  min_confidence?: "exact" | "import" | "fuzzy" | "unresolved";
  strict?: boolean;
  include_fuzzy?: boolean;
  include_unresolved?: boolean;
  include_callbacks?: boolean;
  prune_sanitized?: boolean;
  explicit_sources?: boolean;
  confirmed_only?: boolean;
  taint_hops?: number;
}
