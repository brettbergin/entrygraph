# entrygraph

Query your codebase like a graph. `entrygraph` indexes a repository into a
SQLite database (through the SQLAlchemy ORM) and answers questions about
**symbols**, **classes/methods**, **entrypoints** (HTTP routes, CLI commands,
main functions, tasks, lambda handlers), the **call graph** (callers, callees,
references), and **source → sink reachability** ("can any HTTP route reach
`subprocess.run`?").

Language-agnostic via [tree-sitter](https://tree-sitter.github.io/); first-class
support for **Python, JavaScript/TypeScript, Go, Java, Ruby, C#, PHP, and
Rust**, with language *and* framework detection.

Entrypoints include decorator/attribute routes, call-based route registration,
middleware, and config-file handlers (serverless, SAM, Procfile, Dockerfile).
Reachability enumerates real call paths from a catalog taint source to a tagged
sink and reports **checkable facts** about each — the sink's catalog severity,
the weakest edge confidence, and a same-function reaching-defs verdict (`flow:
confirmed` / `not observed`) — with class-hierarchy analysis to recover virtual
dispatch. Every hop carries a `file:line` and the literal source and sink lines,
so a finding is a lead you can open and verify, not a score to trust.

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

The positional argument may also be a **git URL** — entrygraph clones it and
indexes the checkout:

```bash
entrygraph index https://github.com/semgrep/semgrep     # or git@github.com:org/repo.git
```

The clone lands in a reused workspace (`./.entrygraph/clones/<host>/<org>/<repo>`)
and the index database in the current directory (`./<repo>.entrygraph.db`), so
follow-up queries work with `--db <repo>.entrygraph.db`. Re-running `index <url>`
fetches and updates the existing checkout instead of re-cloning. The clone is
hardened — shallow, repo hooks disabled, no interactive credential prompt, and a
wall-clock timeout — and the indexed code is never executed.

| URL flag                     | Meaning                                                                          |
| ---------------------------- | -------------------------------------------------------------------------------- |
| `--ref REF`                  | branch, tag, or commit to check out (default: remote HEAD)                       |
| `--depth N` / `--full-clone` | clone depth (default 1; `--full-clone` = full history)                           |
| `--clone-dir DIR`            | where to place the checkout                                                      |
| `--ephemeral`                | clone to a temp dir and delete it after indexing (no `paths` snippets afterward) |
| `--timeout SECONDS`          | max clone/fetch wall-time (default 600)                                          |

Private repos work when the ambient git environment already authenticates (SSH
agent, credential helper, or a token in the URL); entrygraph never prompts for or
stores secrets.

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

### `symbols`, `callers`, `callees`, `references` — search & walk the call graph

`symbols` globs on name or qualified name (filter by `--kind`/`--file`);
`callers`/`callees` walk the call graph (`--depth N`); `references` lists every
individual call site targeting a symbol, each with its `file:line` and edge
confidence.

```bash
entrygraph symbols --kind class --name 'Report*'
entrygraph callers app.services.run_report        # who calls it
entrygraph callees app.services.run_report        # what it calls
entrygraph references app.services.run_report     # each call site + file:line
```

```
┏━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━┓
┃KIND  ┃ QNAME                     ┃ FILE            ┃ LINE┃
┡━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━┩
│class │ app.services.ReportRunner │ app/services.py │   10│
└──────┴───────────────────────────┴─────────────────┴─────┘
```

By default `callers`/`callees` list only *resolved* edges (exact/import and
unique-name fuzzy binds). `--include-speculative` adds class-hierarchy guesses
and unresolved wildcard/dynamic calls (lower confidence, noisier).

### `paths` — source → sink reachability

*Can anything reach a dangerous sink?* Paths are drawn as call cards, ordered by
their facts — confirmed flows first, then by sink severity, then by the weakest
edge confidence. Each hop shows its resolution confidence
(`exact`/`import`/`fuzzy`/`unresolved`) and its `file:line`; the sink node is
flagged (`⚡`), and the literal source and sink lines are printed so you can read
the actual call.

```bash
entrygraph paths --source-category http_input --sink-category command_exec
```

```
1 path(s)  category:http_input → category:command_exec

[1] severity high  confidence import
  source  create_report   app/routes.py:12  (http_input · explicit · query "cmd")
          cmd = request.args.get("cmd")
    ↓     run_report      app/routes.py:20  import
    ↓     start           app/services.py:27  fuzzy
  sink    subprocess.run  app/services.py:22  ⚡ py.command-exec.subprocess  import
          subprocess.run(cmd, shell=True)
       flow: confirmed (2 hops)
```

- **Reachability check**: `paths` exits `0` when a path is found, `1` when none —
  `entrygraph paths --source-category http_input --sink-category command_exec && echo reachable`.
- **Sources & sinks**: use `--source-category`/`--sink-category` to start from
  every registered taint source and end at every tagged sink of a category, or
  name an exact `--source`/`--sink` (the language prefix is optional —
  `--sink subprocess.run` resolves to `py:subprocess.run`). Combine a `--source`
  glob with a category to union both. `paths --list-categories` prints the valid
  category names; an unknown category is a hard error, never a silent empty
  result.
- **Precision/recall dial**: by default only high-confidence edges are traversed.
  Widen with `--include-unresolved` (wildcard `py:*.execute` sinks + dynamic
  calls), `--include-fuzzy` (speculative class-hierarchy edges), or
  `--include-callbacks` (function/method values passed as arguments — handler
  registrations like `http.HandleFunc("/", handler)` or `this::handle`).
- **Source provenance**: an `http_input`/`cli_arg` source is labeled `· explicit`
  when the handler demonstrably reads request input (a catalog accessor call like
  `request.args.get("q")`) or `· handler` when the handler is merely shaped like a
  source and reaches the sink without a proven read. `--explicit-sources` drops
  the handler-only seeds entirely (at the cost of property-read frameworks like
  Express `req.body`).
- **Flow verification**: a bounded reaching-defs check labels each path with
  whether a source value actually flows to the sink — `flow: confirmed` when it
  does, `flow: not observed` when it provably doesn't. It follows up to
  `--taint-hops` interior call hops (default 5; `0` = same-function only) and is
  conservative: anything it can't analyze stays unlabeled. `--confirmed-only`
  keeps just the confirmed paths.

**What a finding means.** A path is a *reachability lead to triage*, not a
confirmed dataflow: it says a source-bearing symbol can reach a sink-bearing
symbol through the call graph. Every field is a checkable fact — open the
`file:line`s and read the code. The `severity` is the tagged sink's catalog
severity; the per-hop confidence tags (`exact`/`fuzzy`/`unresolved`) are
*edge-resolution* confidence, not taint confidence; the `flow:` label is the
reaching-defs verdict where the check can see the code. There is no blended
"risk" number to trust — the ordering surfaces confirmed, higher-severity,
better-resolved paths first so you triage the list top-down.

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

Add `--json` to any query command for machine-readable output (each path
includes its `severity`, `min_confidence`, `taint_verified`, and `may_continue`):

```json
[
  {
    "severity": "high",
    "min_confidence": 40,
    "taint_verified": true,
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

> Colored tables, share/confidence bars, and severity highlighting render in a
> real terminal; piped or `--json` output is plain text.

### `serve` — a web UI over the index

To browse an index visually rather than via the CLI, `entrygraph serve` runs a
web app that walks a repo's **symbols**, **entrypoints/routes**,
**callers/callees**, **source→sink paths**, and an interactive **call graph**,
and can register and index repositories from the UI:

```bash
entrygraph index . --db /tmp/graph.db
entrygraph serve --db /tmp/graph.db           # http://127.0.0.1:8100
```

It supports Authentik SSO (`EG_OIDC_*`), with a zero-setup local mode (no auth)
on loopback by default, and ships behind the `entrygraph[server]` extra. Build
the UI once with `cd webapp && npm run build`; the build lands in the package and
is served at `/`.

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

# Source -> sink reachability (ordered by facts: confirmed flows, severity, confidence)
paths = graph.paths(source="app.routes.*", sink_category="command_exec")
for p in paths:
    print(p.severity, p.taint_verified, p.render(), "(+may continue)" if p.may_continue else "")
    # high True  app.routes.create_report -> app.services.run_report (line 20)
    #   -> ...ReportRunner.render_and_execute (line 17) -> py:subprocess.run (line 22)

graph.reachable(source="app.routes.upload", sink="py:subprocess.run")   # -> bool

# Valid category names (an unknown category raises UnknownCategoryError)
graph.sink_categories()      # -> ["command_exec", "sql", "path_traversal", ...]
graph.source_categories()    # -> ["http_input", "cli_arg", "env_input", ...]

# Precision/recall dial. By default only EXACT/IMPORT and unique-name FUZZY
# edges are traversed. Opt into wider (noisier) traversal:
graph.paths(source="app.routes.*", sink_category="sql",
            include_unresolved=True)   # follow py:*.execute wildcard-sink guesses
graph.paths(source="app.routes.*", sink_category="command_exec",
            include_fuzzy=True)        # follow speculative class-hierarchy (CHA) edges
graph.paths(source="app.routes.*", sink_category="command_exec",
            confirmed_only=True)       # keep only paths where a flow is confirmed

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

- **Custom sinks/sources** — drop an `entrygraph.toml` in the repo root with
  `[[sink]]` / `[[source]]` tables (same schema as the built-in
  `data/sinks/*.toml`), or call `entrygraph.detect.taint.register_sink(...)` /
  `register_source(...)`. Third-party wrapper libraries that reach a sink
  internally are covered by `data/sinks/lib_*.toml` "library summaries" (same
  schema, with a `library = "..."` tag).
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
