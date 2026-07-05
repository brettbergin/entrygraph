// Shapes returned by the Sentinel REST API (src/entrygraph/sentinel/api.py).

export interface Installation {
  id: number;
  account_login: string;
  suspended: boolean;
  repo_count: number;
}

export interface ScanCounts {
  new: number;
  known: number;
  fixed: number;
  suppressed: number;
}

export interface Scan {
  id: number;
  pr_number: number | null;
  head_sha: string | null;
  base_sha: string | null;
  status: string;
  counts: ScanCounts;
  created_at: string | null;
}

export interface Repo {
  full_name: string;
  latest_scan: Scan | null;
}

export interface Finding {
  fingerprint: string;
  endpoint_fingerprint: string;
  source_category: string | null;
  sink_id: string | null;
  risk: number;
  status: string;
}

export interface Suppression {
  fingerprint: string;
  reason: string | null;
  created_by: string | null;
  expires_at: string | null;
}

export interface Policy {
  risk_threshold: number;
  gated_categories: string[] | null;
  mode: string;
  min_confidence: string;
}
