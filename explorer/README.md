# entrygraph explorer (UI)

A React + TypeScript (Vite) web UI to explore an entrygraph index, built with
GitHub's [Primer](https://primer.style/) design system. It talks to the read-only
explorer API (`src/entrygraph/explore/api.py`) and gives you, per indexed repo:

- **Overview** — symbol/edge/entrypoint counts, detected languages and frameworks.
- **Symbols** — searchable, kind-filterable symbol table; click one for its
  signature, callers, and callees.
- **Entrypoints** — HTTP routes, CLI commands, tasks, and handlers by framework.
- **Reachability** — risk-ranked source→sink paths with the full call chain.
- **Graph** — an interactive call-graph neighborhood (callers · symbol · callees),
  click a node to re-center.

## Run it

The simplest path — let the CLI serve the built UI + API together:

```bash
entrygraph index /path/to/repo --db /tmp/graph.db      # build an index
cd explorer && npm install && npm run build            # build the UI once
entrygraph explore serve --db /tmp/graph.db            # http://127.0.0.1:8100
```

`npm run build` emits into `src/entrygraph/explore/static`, which
`entrygraph explore serve` serves at `/`. The index is a global multi-repo store,
so `--db` can hold many repos — the UI has a repo picker.

## Develop

```bash
cd explorer
npm run dev            # http://localhost:5173, proxies /api -> http://localhost:8100
```

Run the API separately with `entrygraph explore serve --db <index>` on `:8100`;
the dev server proxies `/api` to it and hot-reloads the UI.

## Layout

- `src/api.ts` / `src/types.ts` — typed client + API shapes.
- `src/App.tsx` — repo picker + tabbed views (Overview…Graph).
- `src/components/` — `Symbols`, `Entrypoints`, `Reachability`, `GraphView`, and
  shared `ui` helpers (`useAsync`, kind labels, formatting).

The build output and `node_modules` are git-ignored; the UI is not covered by the
Python CI (the API is).
