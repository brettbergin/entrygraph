# entrygraph

`entrygraph` builds a queryable graph of your codebase. It indexes a repository
into a local SQLite database, then answers questions about the code: what
symbols and classes exist, what the entrypoints are (HTTP routes, CLI commands,
`main`, tasks, lambdas), who calls what, and whether untrusted input can reach a
dangerous function.

It works across **Python, JavaScript/TypeScript, Go, Java, Ruby, C#, PHP, and
Rust**, using [tree-sitter](https://tree-sitter.github.io/) to parse and
per-language rules to detect frameworks and entrypoints.

## Install

```bash
pip install entrygraph      # or: uv pip install entrygraph
```

Requires Python 3.13+. This installs the `entrygraph` command.

## Quick start

Index a repo, then query it:

```bash
cd ~/code/my-app
entrygraph index .
entrygraph entrypoints
entrygraph callers my_app.services.charge
```

The index lives in `~/.entrygraph/.entrygraph.db` and holds every repo you
index, keyed by path. Query commands automatically use the repo you're standing
in; to query another repo, add `--repo <name>` (run `entrygraph repos` to see
what's indexed). Add `--json` to any command for machine-readable output.

## Commands

| Command               | What it does                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------------ |
| `index <path\|url>`   | Build or update the graph. Incremental by default; `--full` rebuilds. A git URL is cloned first. |
| `detect`              | Languages (by byte share) and detected frameworks.                                               |
| `symbols`             | Search symbols by name, qualified name, kind, or file.                                           |
| `entrypoints`         | Every route, command, `main`, task, and handler, with its framework and location.                |
| `callers` / `callees` | Who calls a symbol / what it calls (`--depth N`).                                                |
| `references`          | Every call site targeting a symbol, with file:line.                                              |
| `paths`               | Source → sink reachability (see below).                                                          |
| `stats`               | Counts for the current repo.                                                                     |
| `repos`               | List the repositories in the database.                                                           |
| `serve`               | Web UI over the index.                                                                           |

Run `entrygraph <command> --help` for the flags on each.

## Reachability (`paths`)

`paths` answers "can untrusted input reach a dangerous function?" — for example,
can an HTTP request reach `subprocess.run`. It traces call paths from a
**source** (where input enters) to a **sink** (a risky API), using a built-in
catalog of both.

```bash
entrygraph paths --source-category http_input --sink-category command_exec
```

```
1 path  http_input → command_exec

[1] confirmed data flow → high-severity command_exec sink
  entrypoint POST /reports                flask http_route
  source     create_report               app/routes.py:12  query "cmd"
             cmd = request.args.get("cmd")
      ↓      run_report                  app/routes.py:20
  sink       subprocess.run              app/services.py:22
             subprocess.run(cmd, shell=True)
  confidence resolved — every call is exact/import
```

Read each finding top-down:

- The **headline** tells you whether to act: `confirmed data flow` means input
  actually reaches the sink; `reachable, but no data flow observed` means the
  call path exists but the input doesn't provably flow through it. It's paired
  with the sink's severity and category.
- **entrypoint** — the route or command the path is reachable through, when the
  source is one. This is the surface an attacker would actually hit.
- **source → sink** — the call chain, each with its `file:line` and the literal
  line of code. Uncertain links are flagged (`~ fuzzy (guess)`); the rest are
  solidly resolved.
- **confidence** — how much to trust the weakest link in the chain.

Paths are ordered best first (confirmed flows, then by severity and confidence).
A finding is a lead to review, not proof of a bug.

Useful options:

- `--source` / `--sink` name an exact symbol instead of a category (the language
  prefix is optional: `--sink subprocess.run`).
- `--list-categories` prints the valid source and sink categories.
- `--confirmed-only` keeps only paths with a confirmed flow.
- `--strict` reports only high-confidence paths; otherwise the search widens
  automatically when it finds nothing.

## Web UI

```bash
entrygraph serve
```

Browse symbols, entrypoints, the call graph, and reachability in the browser, and
index repos from the UI. Runs locally with no auth by default; supports OIDC SSO
for shared deployments. Everything is bundled — `uvx entrygraph serve` just works.
(When developing from a source checkout, build the UI once with
`cd webapp && npm run build`.)

## Python API

Every CLI command is a thin wrapper over the `CodeGraph` class:

```python
from entrygraph import CodeGraph

graph = CodeGraph.index("/path/to/repo")     # or CodeGraph.open("index.db")

graph.entrypoints(framework="flask")
graph.callers("app.services.charge")
graph.paths(source_category="http_input", sink_category="sql")
graph.reachable(source="app.routes.upload", sink="py:subprocess.run")  # -> bool
```

Results are plain frozen dataclasses, safe to hold and easy to serialize.

## How it works

entrygraph walks the tree (skipping vendored and generated files), parses each
file with tree-sitter, resolves references to their definitions with a confidence
level, detects frameworks and entrypoints, and stores everything in SQLite.
Re-indexing only reparses changed files. Reachability is a graph traversal over
the stored call edges; the analyzed code is never executed.

## Extending

Add custom sinks and sources with an `entrygraph.toml` in the repo root (same
format as the built-in catalogs under `data/sinks/`). New frameworks and
languages are added with small rule and tree-sitter query modules.

## License

MIT
