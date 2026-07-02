# Releasing

Releases are **fully automated**. Every merge/push to `main` runs the test
suite, bumps the patch version, tags it, builds the package, and publishes it to
PyPI — no manual version edits, no tokens.

## How it works

1. A merge lands on `main` → [`.github/workflows/release.yml`](.github/workflows/release.yml) runs.
2. The test suite must pass (gate).
3. The workflow finds the latest `vX.Y.Z` tag and computes the next **patch**
   version (`v0.1.4` → `v0.1.5`). With no tags yet, the first release is `v0.1.0`.
4. It creates and pushes that tag. [`hatch-vcs`](https://github.com/ofek/hatch-vcs)
   derives the package version from the tag, so nothing is committed back to `main`.
5. `uv build` produces the sdist + wheel, which are published to PyPI via
   **Trusted Publishing (OIDC)** and attached to a GitHub Release.

Pull requests are tested separately by [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
across Python 3.13–3.14, so broken code never reaches `main`.

## One-time setup (required before the first release)

### 1. Configure PyPI Trusted Publishing

This lets GitHub Actions publish without an API token. Because the project
doesn't exist on PyPI yet, add it as a **pending** publisher:

1. Log in to PyPI → <https://pypi.org/manage/account/publishing/>.
2. Under "Add a new pending publisher", enter:
   | Field | Value |
   | --- | --- |
   | PyPI Project Name | `entrygraph` |
   | Owner | `brettbergin` |
   | Repository name | `entrygraph` |
   | Workflow name | `release.yml` |
   | Environment name | *(leave blank)* |
3. Save. The first successful run creates the project and binds the publisher.

> The PyPI project name `entrygraph` must be available (or already owned by you).
> If it's taken, rename the project in `pyproject.toml` before the first release.

### 2. Allow the workflow to push tags

The workflow pushes tags with the built-in `GITHUB_TOKEN` (granted
`contents: write`). This works out of the box unless you've added a **tag
protection rule** — if so, allow `v*` tags to be created by Actions.

## Everyday use

Just merge to `main`. That's it — a new patch version ships automatically.

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
uv build            # version comes from `git describe`; a dev tree -> X.Y.Z.devN
uv run entrygraph --version
```
