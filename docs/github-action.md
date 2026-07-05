# Reachability gate GitHub Action

A composite action that runs the [`entrygraph gate`](../README.md#gate--baseline--block-new-reachable-paths-in-ci)
in CI: it indexes the checkout, diffs its reachable source→sink paths against a
baseline cut from your default branch, and **fails a pull request that introduces
a new reachable dangerous path**. Findings are uploaded to GitHub code scanning
as SARIF. The analyzed code is never executed — indexing is a parse-and-query
operation.

## Quick start

Add `.github/workflows/reachability.yml` to your repository:

```yaml
name: reachability gate
on:
  push:
    branches: [main] # refreshes the baseline
  pull_request: {} # gates the PR

permissions:
  contents: read
  security-events: write # required to upload SARIF to code scanning

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: brettbergin/entrygraph@main
        with:
          threshold: "0.5" # risk floor a NEW path must clear to fail
          mode: block # or "warn" to report without failing
```

That's it. No servers, no tokens.

## How it behaves

- **Push to the default branch** → the action indexes the checkout and runs
  `entrygraph baseline update`, recording the accepted set of reachable dangerous
  paths. The baseline is cached (under `~/.entrygraph`) keyed by the default
  branch.
- **Pull request** → the action restores that baseline, indexes the PR head, and
  runs `entrygraph gate`. If the diff introduces a **new** reachable path at or
  above `threshold`, the job fails (in `block` mode); either way the findings are
  uploaded as SARIF.
- **First ever run** (no baseline yet) → the gate is bootstrap-safe: it reports
  every path as new but **passes**, and prints a note to cut a baseline. Merge a
  commit to your default branch to establish one.

Because paths are identified by a line-independent fingerprint, moving or
reindenting code is **never** reported as new — only a genuinely new source→sink
reachability is.

## Inputs

| Input                | Default            | Description                                            |
| -------------------- | ------------------ | ------------------------------------------------------ |
| `path`               | `.`                | Directory to index (the checkout).                     |
| `default-branch`     | `main`             | Branch the baseline represents / is refreshed from.    |
| `threshold`          | (policy)           | Risk floor `0..1` a new path must clear to fail.       |
| `mode`               | `block`            | `block` fails on new gated paths; `warn` only reports. |
| `sarif`              | `entrygraph.sarif` | Where the SARIF 2.1.0 file is written.                 |
| `upload-sarif`       | `true`             | Upload the SARIF to GitHub code scanning.              |
| `entrygraph-version` | `entrygraph`       | pip spec, e.g. `entrygraph==1.2.3` or a `git+…` URL.   |
| `python-version`     | `3.13`             | Python used to run entrygraph.                         |

## Outputs

| Output   | Description                                        |
| -------- | -------------------------------------------------- |
| `status` | `passed` \| `failed` \| `warned` \| `no-baseline`. |

## Notes

- **Permissions.** SARIF upload needs `security-events: write`. Omit it (or set
  `upload-sarif: false`) if you only want the pass/fail check.
- **Pin the version.** Prefer `uses: brettbergin/entrygraph@v<x.y.z>` and a fixed
  `entrygraph-version` for reproducible gating.
- **Monorepos.** Point `path` at the subproject you want to gate, or run the job
  in a matrix over several `path` values.
- **Tuning.** Start in `mode: warn` for a sprint to see what the gate would block,
  then switch to `block`. Raise `threshold` to gate only the riskiest paths.
