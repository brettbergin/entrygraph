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
    long: "Verified = flow observed; 'not observed' = the check found the value never reaches the sink; unknown = the check couldn't decide.",
  },
};
