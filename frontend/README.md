# Sentinel dashboard

A React + TypeScript (Vite) dashboard for the [Sentinel](../docs/sentinel.md)
reachability-gate service, built with GitHub's own [Primer](https://primer.style/)
design system (`@primer/react`) so it looks native to GitHub. It walks
**installations → repos → scans → findings**, and lets you manage
**suppressions** and the **gate policy** per repo. It talks to the Sentinel REST
API (`/api`) with a bearer token you paste on the sign-in screen (stored in
`localStorage`).

## Develop

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api -> http://localhost:8000
```

Run a Sentinel API on `:8000` (see [docs/sentinel.md](../docs/sentinel.md)); the
dev server proxies `/api` to it. On the sign-in screen, leave the API base blank
(same origin) and paste your `SENTINEL_API_TOKEN`.

## Build (served by the service)

```bash
npm run build      # emits into ../src/entrygraph/sentinel/static
```

The Sentinel app then serves the dashboard at **`/ui`** automatically — the
service mounts `sentinel/static` when the build exists (see `app.py`). The
`deploy/sentinel` Docker image runs `npm run build` so the dashboard ships with
the service; point a browser at `https://<host>/ui`.

## Cross-origin

If you host the dashboard on a different origin than the API, set
`SENTINEL_CORS_ORIGINS` (comma-separated) on the service so the browser is
allowed to call it, and enter the API base URL on the sign-in screen.

## Layout

- `src/main.tsx` — Primer `ThemeProvider`/`BaseStyles` + the primitives CSS that
  supplies the dark-mode color tokens.
- `src/api.ts` — typed client + token/base storage.
- `src/types.ts` — API response shapes.
- `src/App.tsx` — top-level navigation (installations → repos → repo), Primer
  `Header` + `Breadcrumbs`.
- `src/components/RepoDetail.tsx` — scans/findings (`DataTable`), suppressions,
  and a policy form, tabbed with `UnderlineNav`.
- `src/components/ui.tsx` — shared bits (`useAsync`, status `Label`s, `Blankslate`
  empty states, formatting).

The build output (`../src/entrygraph/sentinel/static`) and `node_modules` are
git-ignored; the dashboard is not covered by the Python CI.
