# entrygraph

Query your codebase like a graph. `entrygraph` indexes a repository into a
SQLite database (through the SQLAlchemy ORM) and answers questions about
**symbols**, **classes/methods**, **entrypoints** (HTTP routes, CLI commands,
main functions, tasks, lambda handlers), and **source → sink call-graph
reachability** ("can any HTTP route reach `subprocess.run`?").

Language-agnostic via [tree-sitter](https://tree-sitter.github.io/); first-class
support for **Python, JavaScript/TypeScript, Go, Java, Ruby, C#, PHP, and
Rust**, with language *and* framework detection.

Reachability is a heuristic taint tier, not just call-edge closure: paths are
**risk-ranked**, **sanitizers** prune or discount them, **class-hierarchy
analysis** recovers virtual dispatch, and confidence flags trade recall for
precision. Entrypoints include decorator/attribute routes, call-based route
registration, middleware, and config-file handlers (serverless, SAM, Procfile,
Dockerfile).

## Install

```bash
pip install entrygraph        # or: uv pip install entrygraph
```

Requires Python ≥ 3.13. Installs the `entrygraph` command (you can also run it as
`uv run entrygraph …` or `python -m entrygraph …`).

## Quick start (CLI)

Index a repo once, then query the resulting `.entrygraph.db` as often as you
like. Every query command takes `--db PATH` (defaults to discovering
`.entrygraph.db`) and `--json` for machine-readable output.

### `index` — build the graph

Walk the tree and extract symbols, imports, and calls into `.entrygraph.db`.
Incremental by default (only changed files are reparsed); `--full` rebuilds.

```bash
entrygraph index .
```

```
╭───────────────── ✓ indexed acme-api ──────────────────╮
│ files    5 indexed, 0 skipped, 0 deleted of 5 scanned │
│ graph    32 symbols  34 edges  5 entrypoints          │
│ db       /path/to/acme-api/.entrygraph.db             │
╰─────────────────────── 0.137s ────────────────────────╯
```

### `detect` — languages & frameworks

Byte-share per language plus framework detections scored from manifest
dependencies and code signals.

```bash
entrygraph detect
```

```
Languages
┏━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃LANGUAGE ┃ FILES ┃ SHARE              ┃
┡━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│python   │     5 │ ████████████ 100.0%│
└─────────┴───────┴────────────────────┘
Frameworks
┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃FRAMEWORK ┃ LANGUAGE ┃ CONFIDENCE     ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│flask     │ python   │ █████████░ 0.94│
│click     │ python   │ █████████░ 0.94│
└──────────┴──────────┴────────────────┘
```

### `entrypoints` — your attack surface

Every HTTP route, CLI command, task, lambda, middleware, and `main` — with its
framework, method, route, and handler symbol. Filter with `--kind`,
`--framework`, or `--route`.

```bash
entrypoints --kind http_route      # or: --framework flask / --route '/api/*'
```

```
┏━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃KIND        ┃ FRAMEWORK ┃ METHOD   ┃ ROUTE            ┃ HANDLER                 ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│http_route  │ flask     │ GET      │ /users/<user_id> │ app.routes.get_user     │
│http_route  │ flask     │ GET      │ /health          │ app.routes.health       │
│http_route  │ flask     │ GET,POST │ /reports         │ app.routes.create_report│
│cli_command │ click     │          │                  │ cli.report              │
│main        │           │          │                  │ cli                     │
└────────────┴───────────┴──────────┴──────────────────┴─────────────────────────┘
5 entrypoint(s)
```

### `symbols`, `callers`, `callees` — search & walk the call graph

`symbols` globs on name or qualified name (filter by `--kind`/`--file`);
`callers`/`callees` walk the call graph (`--depth N`).

```bash
entrygraph symbols --kind class --name 'Report*'
entrygraph callers app.services.run_report        # who calls it
entrygraph callees app.services.run_report        # what it calls
```

```
┏━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━┓
┃KIND  ┃ QNAME                     ┃ FILE            ┃ LINE┃
┡━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━┩
│class │ app.services.ReportRunner │ app/services.py │   10│
└──────┴───────────────────────────┴─────────────────┴─────┘
```

### `paths` — source → sink reachability

The security workhorse: *can anything reach a dangerous sink?* Paths are
**risk-ranked** (highest first) and drawn as call trees — the sink node is
flagged (`⚑`), each hop shows its resolution confidence
(`exact`/`import`/`fuzzy`/`unresolved`), and badges mark constant-argument sinks
and speculative edges.

```bash
entrygraph paths --source '*' --sink-category command_exec
```

```
7 path(s)  * → category:command_exec

[1] ■ risk 0.73  app.services.ReportRunner.render_and_execute
└── → py:subprocess.run   line 22  import  ⚑ py.command-exec.subprocess
[4] ■ risk 0.50  app.services.run_report
└── → app.services.ReportRunner.start   line 27  fuzzy
    └── → app.services.ReportRunner.render_and_execute   line 17  exact
        └── → py:subprocess.run   line 22  import  ⚑ py.command-exec.subprocess
[5] ■ risk 0.49  app.routes.create_report
└── → app.services.run_report   line 20  import
    └── → app.services.ReportRunner.start   line 27  fuzzy
        └── → app.services.ReportRunner.render_and_execute   line 17  exact
            └── → py:subprocess.run   line 22  import  ⚑ py.command-exec.subprocess
```

- **CI gate**: exits `0` when a path is found, `1` when none —
  `entrygraph paths --source '*' --sink-category command_exec && echo reachable`.
- **Catalog sources**: instead of a `--source` glob, use `--source-category` to
  start from every call site of a registered taint source (e.g.
  `--source-category http_input` / `env`) — `entrygraph paths --source-category http_input --sink-category sql`. Combine with `--source` to union both.
- **Precision/recall dial**: by default only high-confidence edges are traversed.
  Widen with `--include-unresolved` (wildcard `py:*.execute` sinks + dynamic
  calls), `--include-fuzzy` (speculative class-hierarchy edges), or
  `--include-callbacks` (function/method values passed as arguments — handler
  registrations like `http.HandleFunc("/", handler)` or `this::handle`).
- **Sanitizers**: a registered sanitizer for the sink's category called on a
  path (e.g. `shlex.quote`) *discounts* its risk score — heuristically, since
  there is no dataflow, so it never zeroes the risk or hides the path.
  `--prune-sanitized` opts into dropping those paths entirely.
- **Source provenance**: an `http_input`/`cli_arg` source is labeled `· explicit`
  when the handler demonstrably reads request input (a catalog accessor call like
  `request.args.get("q")`) or `· handler` when the handler is merely shaped like a
  source and reaches the sink without a proven read. Explicit sources rank above
  handler-as-source ones; `--explicit-sources` drops the handler-only seeds
  entirely (at the cost of property-read frameworks like Express `req.body`).
- **Flow verification**: a bounded reaching-defs check runs over the candidate
  findings and labels each with whether a request value actually flows to the
  sink — `flow: confirmed` when it does, `flow: not observed` when it provably
  doesn't (that path is down-weighted). It follows up to `--taint-hops` interior
  call hops (default 3; `0` = same-function only) and is conservative: anything
  it can't analyze stays unlabeled and unchanged. `--confirmed-only` keeps just
  the confirmed paths.
- Target an exact sink with `--sink py:subprocess.run` instead of a category.

**What a finding means.** A path is a *reachability lead to triage*, not a
confirmed dataflow: it says a source-bearing symbol can reach a sink-bearing
symbol through the call graph. The `flow:` label sharpens this where the check
can see the code, but an unlabeled path is still just reachability. The per-hop
confidence tags (`exact`/`fuzzy`/`unresolved`) are *edge-resolution* confidence,
not taint confidence. Rank, provenance, and flow labels are there to help you
triage the list, highest-signal first.

### `stats` & `--json`

```bash
entrygraph stats
entrygraph --help          # every command and flag
```

```
╭─────────────── index stats ────────────────╮
│ ┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓ │
│ ┃metric           ┃                 value┃ │
│ ┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩ │
│ │repo_root        │ /path/to/acme-api    │ │
│ │files            │                     5│ │
│ │symbols          │                    32│ │
│ │edges            │                    34│ │
│ │entrypoints      │                     5│ │
│ │sink_edges       │                     2│ │
│ └─────────────────┴──────────────────────┘ │
╰────────────────────────────────────────────╯
```

Add `--json` to any query command for machine-readable output (paths include
`risk_score` and `may_continue`):

```json
[
  {
    "risk_score": 0.4887,
    "may_continue": false,
    "symbols": [
      "app.routes.create_report", "app.services.run_report",
      "app.services.ReportRunner.start",
      "app.services.ReportRunner.render_and_execute", "py:subprocess.run"
    ],
    "lines": [20, 27, 17, 22]
  }
]
```

> Colored tables, share/confidence bars, and risk-tree highlighting render in a
> real terminal; piped or `--json` output is plain text.

## Python API

```python
from entrygraph import CodeGraph

# Index a repo (creates <repo>/.entrygraph.db by default)
graph = CodeGraph.index("/path/to/repo")

# ...or open an existing index
graph = CodeGraph.open("graph.db")

# Symbols — glob on name or qualified name, filter by kind or file
graph.symbols(kind="class", name="User*")
graph.symbol("app.services.Runner.execute")        # exact; raises if missing

# Detection
report = graph.detect()
report.languages      # -> [DetectedLanguage(name="python", percent=96.7, ...), ...]
report.frameworks     # -> [DetectedFramework(name="flask", confidence=0.94, ...), ...]

# Entrypoints
graph.entrypoints(framework="flask")
graph.entrypoints(kind="http_route", route="/api/*")

# Call graph
graph.callers("app.services.run_report")            # who calls it
graph.callees("app.services.run_report", depth=3)   # what it (transitively) calls
graph.references("app.models.CONST")                # inbound edges of any kind

# Source -> sink reachability (paths are risk-ranked, highest first)
paths = graph.paths(source="app.routes.*", sink_category="command_exec")
for p in paths:
    print(p.risk_score, p.render(), "(+may continue)" if p.may_continue else "")
    # 0.49 app.routes.create_report -> app.services.run_report (line 20)
    #   -> ...ReportRunner.render_and_execute (line 17) -> py:subprocess.run (line 22)

graph.reachable(source="app.routes.upload", sink="py:subprocess.run")   # -> bool

# Precision/recall dial. By default only EXACT/IMPORT and unique-name FUZZY
# edges are traversed. Opt into wider (noisier) traversal:
graph.paths(source="app.routes.*", sink_category="sql",
            include_unresolved=True)   # follow py:*.execute wildcard-sink guesses
graph.paths(source="app.routes.*", sink_category="command_exec",
            include_fuzzy=True)        # follow speculative class-hierarchy (CHA) edges
graph.paths(source="app.routes.*", sink_category="command_exec",
            prune_sanitized=True)      # drop paths where a shlex.quote etc. is called

# Incremental re-index (only changed/added/deleted files are reparsed)
graph.refresh()

# Escape hatches
graph.session()               # raw SQLAlchemy Session
graph.sql("SELECT ...")       # textual query -> list[dict]
```

Every result is a frozen, immutable dataclass detached from the DB session, so
results are safe to hold and trivial to serialize.

## How it works

1. **Walk** — `os.scandir` with hard-pruned junk dirs (`node_modules`, `.venv`,
   …), `.gitignore` rules, and size/binary/minified gates. Every skip is recorded
   with a reason.
1. **Extract** — tree-sitter `.scm` queries harvest definitions/imports/calls;
   small per-language "shaper" modules build qualified names, import maps, and
   receiver info. Parsing runs across a process pool for large repos.
1. **Resolve** — a two-pass resolver binds references to symbols with a
   confidence level (`exact` / `import` / `fuzzy` / `unresolved`). External
   callees (`subprocess.run`, `child_process.exec`, …) become placeholder nodes
   so sinks are real graph terminals.
1. **Detect** — frameworks are scored from manifest dependencies plus code
   signals (noisy-or); entrypoint rules map framework patterns to route/command
   records.
1. **Store** — everything persists to SQLite via the SQLAlchemy 2.0 ORM with
   bulk inserts and app-assigned keys. Re-indexing is incremental and
   content-hash driven.
1. **Query** — reachability runs over an in-memory adjacency cache (BFS/DFS with
   cycle handling); a recursive-CTE SQL engine is available as a fallback
   (`engine="sql"`).

## Extending

- **Custom sinks/sources/sanitizers** — drop an `entrygraph.toml` in the repo
  root with `[[sink]]` / `[[source]]` / `[[sanitizer]]` tables (same schema as
  the built-in `data/sinks/*.toml`), or call
  `entrygraph.detect.taint.register_sink(...)` / `register_sanitizer(...)`. A
  `[[sanitizer]]` called on a path discounts its risk score for that category;
  since reachability has no dataflow, the discount is capped (a match never
  zeroes risk or hides a path — use `--prune-sanitized` to drop them explicitly).
  Third-party wrapper libraries that reach a sink internally are covered by
  `data/sinks/lib_*.toml`
  "library summaries" (same schema, with a `library = "..."` tag).
- **New frameworks / entrypoints** — register a `FrameworkSpec` and an
  `EntrypointRule`; adding a framework is usually a few lines.
- **New languages** — add a `<lang>/{definitions,imports,calls}.scm` query set
  and a shaper implementing the `LanguageExtractor` protocol.

## Releasing

Merging to `main` auto-bumps the patch version (via a git tag) and publishes to
PyPI through Trusted Publishing — see [RELEASING.md](RELEASING.md). The package
version is derived from git tags by `hatch-vcs`, so it's never hand-edited.

## License

MIT
