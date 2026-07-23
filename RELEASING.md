# Releasing

Releases are **fully automated**. Every merge/push to `main` runs the test
suite, bumps the patch version, tags it, builds the package, and publishes it to
PyPI — no manual version edits, no tokens.

## How it works

1. A merge lands on `main` → [`.github/workflows/release.yml`](.github/workflows/release.yml) runs.
1. The test suite must pass (gate).
1. The workflow finds the latest `vX.Y.Z` tag and computes the next **patch**
   version (`v0.1.4` → `v0.1.5`). With no tags yet, the first release is `v0.1.0`.
1. It creates and pushes that tag. [`hatch-vcs`](https://github.com/ofek/hatch-vcs)
   derives the package version from the tag, so nothing is committed back to `main`.
1. The SPA is built (`npm ci && npm run build` in `webapp/`) into
   `src/entrygraph/server/static/` — that directory is gitignored, so this step
   is what gets the web UI into the published package.
1. `uv build` produces the sdist + wheel. A guard step fails the release if the
   wheel is missing `entrygraph/server/static/index.html`, so a UI-less package
   can never ship. The artifacts are published to PyPI via **Trusted Publishing
   (OIDC)** and attached to a GitHub Release.

Pull requests are tested separately by [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
across Python 3.13–3.14, so broken code never reaches `main`.

## One-time setup (required before the first release)

### 1. Configure PyPI Trusted Publishing

This lets GitHub Actions publish without an API token. Because the project
doesn't exist on PyPI yet, add it as a **pending** publisher:

1. Log in to PyPI → <https://pypi.org/manage/account/publishing/>.
1. Under "Add a new pending publisher", enter:
   | Field             | Value           |
   | ----------------- | --------------- |
   | PyPI Project Name | `entrygraph`    |
   | Owner             | `brettbergin`   |
   | Repository name   | `entrygraph`    |
   | Workflow name     | `release.yml`   |
   | Environment name  | *(leave blank)* |
1. Save. The first successful run creates the project and binds the publisher.

> The PyPI project name `entrygraph` must be available (or already owned by you).
> If it's taken, rename the project in `pyproject.toml` before the first release.

### 2. Allow the workflow to push tags

The workflow pushes tags with the built-in `GITHUB_TOKEN` (granted
`contents: write`). This works out of the box unless you've added a **tag
protection rule** — if so, allow `v*` tags to be created by Actions.

## Everyday use

Just merge to `main`. That's it — a new patch version ships automatically.

## Database versioning: which constant do I bump?

The graph DB carries two independent versions (`src/entrygraph/db/meta.py`), and
picking the wrong one is the difference between a seamless customer upgrade and
a fleet-wide forced re-index. The rule:

- **Changed a table/column/index in `db/models.py`?** Bump `SCHEMA_VERSION`
  **and ship an ordered migration step in `db/migrations.py`** (plus a test in
  `tests/test_migrations.py` that migrates a previous-version fixture DB
  forward). Customer DBs then upgrade **in place** at next open — no data loss,
  no re-index. Never ship a `SCHEMA_VERSION` bump without its migration: a gap
  in the migration chain falls back to a full rebuild, which is exactly the
  outage this system exists to prevent.
- **Changed extraction/detection logic** (new framework rule, new entrypoint
  kind's detector, extractor behavior — anything that would make already-stored
  rows differ from a fresh index)? Bump `ANALYZER_VERSION` **only**. Do not
  touch `SCHEMA_VERSION`. Already-indexed repos keep serving their existing
  (still-valid) results, show as _refreshing_, and re-scan per-repo in the
  background (`entrygraph reindex --stale`, or the server's automatic heal
  sweep). No outage, no manual customer action.
- **Neither?** (Bug fix with identical output, CLI/UI change, docs): bump
  nothing.

If a change is both structural *and* semantic, do both: `SCHEMA_VERSION` + its
migration for the shape, `ANALYZER_VERSION` for the contents.

### Cutting a minor or major release

The workflow only auto-bumps the **patch** segment. To move the minor or major,
create the tag yourself once; the next merge continues from there:

```bash
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0        # publishes v0.2.0; the next merge -> v0.2.1
```

(Or run the **Release** workflow manually from the Actions tab via
`workflow_dispatch` after pushing the tag — it will detect HEAD is already tagged
and skip re-tagging.)

## Token fallback (if you'd rather not use Trusted Publishing)

Add a `PYPI_API_TOKEN` repository secret and give the publish step a password:

```yaml
      - name: Publish to PyPI
        if: steps.ver.outputs.release == 'true'
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{ secrets.PYPI_API_TOKEN }}
```

## Local sanity check

```bash
(cd webapp && npm ci && npm run build)   # bundle the SPA into server/static
uv build            # version comes from `git describe`; a dev tree -> X.Y.Z.devN
uv run entrygraph --version
unzip -l dist/*.whl | grep server/static/index.html   # UI made it into the wheel
```
