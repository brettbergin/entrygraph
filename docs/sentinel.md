# Sentinel — self-hostable reachability gate service

Sentinel is the optional networked layer around the Continuous Reachability Gate
(#116). It is a **GitHub App + HTTP service** that runs the same `entrygraph gate`
engine on pull-request webhooks, keeps per-repo baselines centrally, records
findings, and posts a GitHub **Check Run** — so a team gets diff-aware
reachability gating without wiring the composite Action into every repo.

Sentinel is deliberately separate from the CLI/Action: it is a networked,
multi-tenant service with real security surface. It reuses the gate engine
unchanged — **entrygraph never executes analyzed code**, so scanning an untrusted
PR is a parse-and-query operation, not a sandbox problem.

Everything ships behind the `entrygraph[sentinel]` extra so the core CLI stays
dependency-lean.

## Architecture

```
GitHub --pull_request/push--> web (webhook, HMAC verify + dedupe) --> Redis (arq)
                                                                        |
                                                                     worker
                                                                        |
   installation token --> fetch head --> index --> gate diff --> Postgres
                                                        |
                                             Check Run + SARIF upload
   REST API (token-guarded) ------------------------> Postgres
```

- **web** — `entrygraph.sentinel.app:build_from_env` (uvicorn): the webhook
  receiver at `/` and the REST API at `/api`.
- **worker** — `arq entrygraph.sentinel.queue.WorkerSettings`: consumes scan and
  baseline-refresh jobs.
- **Postgres** — findings store (baselines, scan runs, findings, suppressions,
  policy, installations).
- **Redis** — the job queue.

## Deploy

```bash
cd deploy/sentinel
cp /path/to/your-app.private-key.pem ./app_private_key.pem   # git-ignored
cat > .env <<'ENV'
SENTINEL_GITHUB_APP_ID=123456
SENTINEL_WEBHOOK_SECRET=<the webhook secret you set on the App>
SENTINEL_API_TOKEN=<a long random token for the REST API>
ENV
docker compose up --build
```

Point the GitHub App's webhook at `https://<host>/webhook` and subscribe to the
**Pull request** and **Push** events. Grant the minimum repository permissions:

- **Contents: Read** — fetch the head commit to index.
- **Checks: Write** — post the Check Run.
- **Pull requests: Read** — read PR metadata.
- **Code scanning alerts: Write** — upload SARIF (optional; skipped gracefully if
  absent).

## Configuration

| Variable                                | Required    | Purpose                                                                                                                 |
| --------------------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------- |
| `SENTINEL_GITHUB_APP_ID`                | yes         | The GitHub App's id.                                                                                                    |
| `SENTINEL_WEBHOOK_SECRET`               | yes         | Verifies the `X-Hub-Signature-256` HMAC.                                                                                |
| `SENTINEL_GITHUB_PRIVATE_KEY` / `_FILE` | yes         | App private key (PEM inline or a file path).                                                                            |
| `SENTINEL_API_TOKEN`                    | for the API | Bearer token guarding `/api`; unset ⇒ the API fails closed (503).                                                       |
| `SENTINEL_DATABASE_URL`                 | no          | Findings store (default SQLite; use Postgres in prod).                                                                  |
| `SENTINEL_REDIS_URL`                    | no          | Job queue.                                                                                                              |
| `SENTINEL_GITHUB_API_URL`               | no          | Override for GitHub Enterprise.                                                                                         |
| `SENTINEL_CORS_ORIGINS`                 | no          | Comma-separated browser origins allowed to call the API (only needed if the dashboard is hosted on a different origin). |

Secrets come from the environment or a mounted secret file only — never the
database or logs. `SentinelConfig.redacted()` is the log-safe view.

## Security posture

- **Webhook forgery / replay** — every delivery's `X-Hub-Signature-256` HMAC is
  verified in constant time and its `X-GitHub-Delivery` id is deduplicated.
- **Untrusted PR code** — entrygraph never executes it; the worker runs
  unprivileged with a read-only root filesystem (`tmpfs` for scratch), and a
  per-scan size cap (default 512 MiB) bounds a hostile repo.
- **SSRF / arbitrary fetch** — only the installation-granted `clone_url` is
  fetched, with the installation token — never a user-supplied URL.
- **Multi-tenant isolation** — every scan/finding/baseline is scoped by
  `installation_id` + `repo_id`; the REST API 404s an `(installation, repo)` it
  has no history for, so a caller can't read across installations.
- **Baseline poisoning** — baselines refresh **only** from the protected default
  branch after a push; a PR head never moves the baseline it is measured against.
- **Uninstall** — an `installation` `deleted` webhook hard-deletes every trace of
  the installation (graph, baselines, findings, suppressions) via the repository
  cascade.

## Secret rotation runbook

1. **Webhook secret** — set a new secret on the App, update
   `SENTINEL_WEBHOOK_SECRET`, and redeploy the web process. Deliveries signed with
   the old secret will 401; GitHub retries, so re-trigger from the App's
   *Advanced → Recent Deliveries* if needed.
1. **App private key** — generate a new key in the App settings, replace the
   mounted `app_private_key.pem`, redeploy web + worker, then delete the old key
   in GitHub. Installation tokens are short-lived, so in-flight scans are
   unaffected.
1. **API token** — rotate `SENTINEL_API_TOKEN` and redeploy web; update any
   clients.

## Retention

Findings grow with every scan. `entrygraph.sentinel.store.purge_scans(session, repo_id, keep=N)` deletes all but the newest `N` scan runs for a repo (cascading
their findings), keeping the store bounded on always-on installations. Wire it
into a periodic maintenance job for your retention policy.

## Dashboard

A React + TypeScript dashboard (in [`frontend/`](../frontend/README.md)) walks
installations → repos → scans → findings and manages suppressions + policy per
repo. The Docker image builds it, and the web process serves it at **`/ui`** —
open `https://<host>/ui` and paste your `SENTINEL_API_TOKEN`. For local
development, `npm run dev` in `frontend/` proxies `/api` to a Sentinel on `:8000`.

## REST API

All routes are under `/api/installations/{installation_id}/repos/{owner}/{repo}`
and require `Authorization: Bearer $SENTINEL_API_TOKEN`. Two discovery routes,
`GET /api/installations` and `GET /api/installations/{id}/repos`, let the
dashboard enumerate what exists.

| Method     | Path                           | Purpose                        |
| ---------- | ------------------------------ | ------------------------------ |
| GET        | `/scans`                       | Recent scan runs with counts.  |
| GET        | `/scans/{id}/findings?status=` | A scan's findings.             |
| GET        | `/findings?status=`            | The latest scan's findings.    |
| GET / POST | `/suppressions`                | List / add a waiver.           |
| DELETE     | `/suppressions/{fingerprint}`  | Remove a waiver.               |
| GET / PUT  | `/policy`                      | Read / update the gate policy. |
