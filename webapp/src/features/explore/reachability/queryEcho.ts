// Build the equivalent `entrygraph paths ...` command for a query — teaches the
// CLI and doubles as a copy-paste artifact. Pure; unit-tested.

import type { PathsQuery } from "../../../api/types";

export function pathsCliEcho(q: PathsQuery): string {
  const parts = ["entrygraph paths"];
  if (q.source) parts.push(`--source '${q.source}'`);
  else if (q.source_category) parts.push(`--source-category ${q.source_category}`);
  if (q.sink) parts.push(`--sink '${q.sink}'`);
  else if (q.sink_category) parts.push(`--sink-category ${q.sink_category}`);
  if (q.min_confidence) parts.push(`--min-confidence ${q.min_confidence}`);
  if (q.strict) parts.push("--strict");
  if (q.include_fuzzy) parts.push("--include-fuzzy");
  if (q.include_unresolved) parts.push("--include-unresolved");
  if (q.include_callbacks) parts.push("--include-callbacks");
  if (q.prune_sanitized) parts.push("--prune-sanitized");
  if (q.explicit_sources) parts.push("--explicit-sources");
  if (q.confirmed_only) parts.push("--confirmed-only");
  if (q.max_depth && q.max_depth !== 25) parts.push(`--max-depth ${q.max_depth}`);
  if (q.taint_hops != null && q.taint_hops !== 5) parts.push(`--taint-hops ${q.taint_hops}`);
  return parts.join(" ");
}
