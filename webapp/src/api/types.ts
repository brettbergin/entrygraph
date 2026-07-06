// API response shapes — the /api/v1 contract (see server/routes/serializers.py).

export interface Me {
  user: {
    id: number | null;
    name: string;
    email?: string | null;
    role: "admin" | "viewer";
    via: string;
  };
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

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface IndexStats {
  files_scanned: number;
  files_indexed: number;
  files_skipped: number;
  files_deleted: number;
  symbols: number;
  edges: number;
  entrypoints: number;
  duration_seconds: number;
}

export interface Job {
  id: string;
  type: string;
  status: JobStatus;
  params: Record<string, unknown>;
  repo_root: string | null;
  repo_id: number | null;
  progress: number;
  phase: string | null;
  message: string | null;
  error: string | null;
  stats: IndexStats | null;
  created_by: string | null;
  cancel_requested: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface RegisterRepoRequest {
  source: string;
  ref?: string;
  depth?: number;
  include_tests?: boolean;
}

export interface GateFinding {
  fingerprint: string;
  endpoint_fingerprint: string;
  source_category: string | null;
  sink_id: string | null;
  sink_category: string | null;
  risk: number;
  hops: Array<{ qname: string; file: string | null; line: number | null }>;
}

export interface GateResult {
  status: "passed" | "failed" | "warned" | "no-baseline";
  passed: boolean;
  mode: "block" | "warn";
  has_baseline: boolean;
  scan_id: number | null;
  counts: { new: number; known: number; fixed: number; suppressed: number };
  new: GateFinding[];
  gating: GateFinding[];
  fixed: GateFinding[];
}

export interface Scan {
  id: number;
  status: string;
  pr_number: number | null;
  head_sha: string | null;
  counts: { new: number; known: number; fixed: number; suppressed: number };
  created_at: string | null;
}

export interface ScanFinding {
  id: number;
  fingerprint: string;
  status: "new" | "known" | "fixed" | "suppressed";
  source_category: string | null;
  sink_id: string | null;
  risk: number;
  path: {
    sink_category: string | null;
    hops: Array<{ qname: string; file: string | null; line: number | null }>;
  } | null;
}

export interface BaselineInfo {
  branch: string;
  commit_sha: string | null;
  created_at: string | null;
  paths: GateFinding[];
}

export interface PolicyData {
  risk_threshold: number;
  gated_categories: string[] | null;
  mode: "block" | "warn";
  min_confidence: "exact" | "import" | "fuzzy" | "unresolved";
}

export interface Suppression {
  fingerprint: string;
  reason: string | null;
  created_by: string | null;
  expires_at: string | null;
}

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  role: "admin" | "viewer";
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface Installation {
  id: number;
  account_login: string;
  suspended: boolean;
  repo_count: number;
}

export interface InstallationRepo {
  repo_id: number;
  full_name: string;
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
