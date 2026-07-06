// Single source of truth for domain-term explanations. Every term-bearing
// badge/header/flag in the UI points here via <InfoPopover term="...">.

export interface GlossaryEntry {
  title: string;
  short: string;
  long?: string;
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
  entrypoint: {
    title: "Entrypoint",
    short: "Where outside input enters your code: an HTTP route, CLI command, scheduled task, lambda handler…",
    long: "Entrypoints come from framework detection — a Flask detection makes @app.route handlers entrypoints. They are the default sources for reachability questions.",
  },
  source: {
    title: "Taint source",
    short: "A place untrusted input enters: request params, CLI args, environment variables, file contents.",
    long: "Sources are cataloged per language/framework. 'Explicit' sources are proven reads (request.args.get(...)); handlers can also count as sources because they receive the input as parameters.",
  },
  sink: {
    title: "Sink",
    short: "A dangerous API: shell execution, raw SQL, file paths, deserialization, outbound HTTP.",
    long: "Sinks are cataloged with a category and severity. A path that carries attacker input into a sink is a potential vulnerability (e.g. command injection).",
  },
  sanitizer: {
    title: "Sanitizer",
    short: "A function that neutralizes dangerous input for a specific sink category (e.g. shlex.quote for shell commands).",
    long: "A sanitizer seen on or near a path discounts its risk but never zeroes it — without full dataflow the tool can't prove the sanitized value is the one reaching the sink.",
  },
  confidence: {
    title: "Edge confidence",
    short: "How certain the indexer is that a call edge is real: exact > import > fuzzy > unresolved.",
    long: "'Exact' edges are same-scope resolutions; 'import' followed the import graph; 'fuzzy' matched a unique name; 'unresolved' is a wildcard guess (e.g. any .execute method). Lower tiers trade precision for recall.",
  },
  cha: {
    title: "Class-hierarchy analysis (CHA)",
    short: "Recovers virtual dispatch: a call through a base class fans out to every subclass override.",
    long: "CHA edges are speculative — they widen the search when precise resolution finds nothing. Dashed edges in the call graph are CHA/fuzzy.",
  },
  callback: {
    title: "Callback edge",
    short: "A function passed as a value and invoked elsewhere — tracked as an extra edge kind.",
  },
  taint_verified: {
    title: "Taint verification",
    short: "A bounded dataflow check of whether the tainted value actually flows to the sink.",
    long: "Verified = flow observed; 'no flow proven' = the check found the value never reaches the sink (risk demoted ×0.25); unknown = the check couldn't decide.",
  },
  risk: {
    title: "Risk score",
    short: "Heuristic 0–1 ranking: edge confidence × source provenance × sink severity, discounted by sanitizers and constant arguments.",
  },
  fingerprint: {
    title: "Fingerprint",
    short: "A stable identity for a path (source + sink + hops) that survives line-number changes.",
    long: "Baselines and suppressions are keyed by fingerprint, so accepted paths stay accepted across refactors that don't change the path itself.",
  },
  baseline: {
    title: "Baseline",
    short: "The accepted set of dangerous paths for a branch. The gate fails only on paths NOT in it.",
    long: "Cut a baseline once ('these are known, tracked risks'), then every future gate run reports only newly introduced paths — a diff-aware security gate.",
  },
  gate: {
    title: "Reachability gate",
    short: "A CI check that fails when a change introduces a new source→sink path above the risk threshold.",
    long: "Classification: new (not in baseline), known (in baseline), fixed (in baseline but no longer reachable), suppressed (waived). Only new paths ≥ threshold gate, and only in block mode.",
  },
  suppression: {
    title: "Suppression",
    short: "A reviewed waiver for one fingerprint — the gate reports it but never fails on it.",
    long: "Suppressions can carry a reason and an expiry (so waivers don't rot). Use them for accepted risks; use the baseline for the pre-existing backlog.",
  },
  policy: {
    title: "Gate policy",
    short: "Per-repo knobs: risk threshold, which sink categories gate, block vs warn mode, minimum edge confidence.",
  },
  mode_widened: {
    title: "Widened search",
    short: "No high-confidence paths were found, so the search added speculative edges (CHA, unresolved calls). Treat results as leads.",
  },
};
