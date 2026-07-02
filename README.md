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

Requires Python ≥ 3.11. Depends on `sqlalchemy`, `tree-sitter`,
`tree-sitter-language-pack`, and `pathspec`.

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
    # 0.72 app.routes.create_report -> app.services.run_report (line 20)
    #   -> ...ReportRunner.render_and_execute (line 17) -> py:subprocess.run (line 22)

graph.reachable(source="app.routes.upload", sink="py:subprocess.run")   # -> bool

# Precision/recall dial. By default only EXACT/IMPORT and unique-name FUZZY
# edges are traversed. Opt into wider (noisier) traversal:
graph.paths(source="app.routes.*", sink_category="sql",
            include_unresolved=True)   # follow py:*.execute wildcard-sink guesses
graph.paths(source="app.routes.*", sink_category="command_exec",
            include_fuzzy=True)        # follow speculative class-hierarchy (CHA) edges
graph.paths(source="app.routes.*", sink_category="command_exec",
            prune_sanitized=True)      # drop paths neutralized by a shlex.quote etc.

# Incremental re-index (only changed/added/deleted files are reparsed)
graph.refresh()

# Escape hatches
graph.session()               # raw SQLAlchemy Session
graph.sql("SELECT ...")       # textual query -> list[dict]
```

Every result is a frozen, immutable dataclass detached from the DB session, so
results are safe to hold and trivial to serialize.

## CLI

```bash
entrygraph index PATH [--full] [--paranoid]     # incremental by default
entrygraph detect
entrygraph symbols --kind class --name 'User*'
entrygraph entrypoints --framework flask
entrygraph callers  app.services.run_report --depth 2
entrygraph callees  app.services.run_report
entrygraph paths --source 'app.routes.*' --sink-category command_exec
entrygraph stats
```

Add `--json` to any query command for machine-readable output. `entrygraph paths`
exits 0 when a path is found and 1 when none is — handy in CI:

```bash
entrygraph paths --source '*' --sink-category command_exec && echo "reachable!"
```

## How it works

1. **Walk** — `os.scandir` with hard-pruned junk dirs (`node_modules`, `.venv`,
   …), `.gitignore` rules, and size/binary/minified gates. Every skip is recorded
   with a reason.
2. **Extract** — tree-sitter `.scm` queries harvest definitions/imports/calls;
   small per-language "shaper" modules build qualified names, import maps, and
   receiver info. Parsing runs across a process pool for large repos.
3. **Resolve** — a two-pass resolver binds references to symbols with a
   confidence level (`exact` / `import` / `fuzzy` / `unresolved`). External
   callees (`subprocess.run`, `child_process.exec`, …) become placeholder nodes
   so sinks are real graph terminals.
4. **Detect** — frameworks are scored from manifest dependencies plus code
   signals (noisy-or); entrypoint rules map framework patterns to route/command
   records.
5. **Store** — everything persists to SQLite via the SQLAlchemy 2.0 ORM with
   bulk inserts and app-assigned keys. Re-indexing is incremental and
   content-hash driven.
6. **Query** — reachability runs over an in-memory adjacency cache (BFS/DFS with
   cycle handling); a recursive-CTE SQL engine is available as a fallback
   (`engine="sql"`).

## Extending

- **Custom sinks/sources/sanitizers** — drop an `entrygraph.toml` in the repo
  root with `[[sink]]` / `[[source]]` / `[[sanitizer]]` tables (same schema as
  the built-in `data/sinks/*.toml`), or call
  `entrygraph.detect.taint.register_sink(...)` / `register_sanitizer(...)`. A
  `[[sanitizer]]` with `effect = "neutralizes"` prunes a path for its category;
  `effect = "reduces"` only discounts the risk score. Third-party wrapper
  libraries that reach a sink internally are covered by `data/sinks/lib_*.toml`
  "library summaries" (same schema, with a `library = "..."` tag).
- **New frameworks / entrypoints** — register a `FrameworkSpec` and an
  `EntrypointRule`; adding a framework is usually a few lines.
- **New languages** — add a `<lang>/{definitions,imports,calls}.scm` query set
  and a shaper implementing the `LanguageExtractor` protocol.

## License

MIT
