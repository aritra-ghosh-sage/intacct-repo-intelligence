# PR Impact Step 2

Step 2 is a read-only evidence-availability audit for PR 49156. It consumes a
newly generated Step 1 report in-process and emits a separate schema `0.1`
report. Step 1 remains frozen at schema `0.4`; Step 2 does not duplicate its
direct facts, re-query SQLite, or infer semantic impact.

The proving slice is restricted to `ia-main` and the exact revision pair:

- Base: `e33954d66e7823303fa24087950d3306c547e0b7`
- Target: `f914d9892a51c1d34eadfd0e4da89f8418ed2c59`
- Changed source: `app/source/purchasing/POProcessTransactions.phtml`

## Operator prerequisite

Build the exact target revision into an isolated database. This is the only
catalog-build operation in the workflow; Step 2 never builds, promotes, or
cleans up a catalog.

```bash
PYTHONPATH=. ./.venv/bin/python -m catalog.repo_v1 \
  --manifest config/workspace_repos.yaml \
  --target-sha f914d9892a51c1d34eadfd0e4da89f8418ed2c59 \
  --active-db /private/tmp/pr-49156/catalog.db \
  --no-progress
```

The exact resolved source repo root must be the `ia-main` root from the
manifest. The active database must pass Step 1's schema, integrity,
foreign-key, ownership, and exact-target preflight. A stale, forward, or
diverged catalog blocks with `catalog_revision_mismatch`.

Run the audit with:

```bash
PYTHONPATH=. ./.venv/bin/python -m scripts.trace_pr_impact_step2 \
  --fixture examples/pr-impact/ia-app-pr-49156.yaml \
  --manifest config/workspace_repos.yaml \
  --active-db /private/tmp/pr-49156/catalog.db \
  --repo-key ia-main
```

Use `--markdown` for a report-only Markdown view. JSON is the default and is
deterministically serialized by the CLI. Validate a materialized report with:

```bash
PYTHONPATH=. ./.venv/bin/python -m scripts.validate_pr_impact_step2 \
  --report step-2-report.json
```

## Report contract

The top-level contract is schema `0.1` and contains `input`, exact `preflight`,
`step1_summary`, the 21-row `surface_audit`, `gaps`, `warnings`, and
`provenance`. Blocked reports additionally contain `error` and have no surface
audit rows. `step1_summary.step1_report_sha256` is the SHA-256 of the canonical
JSON value returned by the in-process Step 1 analyzer.

Each audit row contains only the Step 1 surface name, its Step 1 status, a
fact count, and a disposition. Dispositions are deliberately limited to:

| Step 1 status | Step 2 disposition |
| --- | --- |
| `available` | `covered` |
| `empty` | `defer_no_direct_rows` |
| `deferred` | `defer_missing_target_evidence` |
| `unresolved`, `ambiguous`, `stale` | `needs_review` |
| `unavailable` | `not_modelled` |

An `empty` surface means that Step 1 found no direct rows. It is not evidence
that the change has no impact. Step 2 reports availability and gaps only; it
does not resolve semantic source gaps or add confidence scoring.

Step 2 status is `complete` only when all 21 supported surfaces are
`available`; otherwise it is `partial`. A blocked Step 1 report, malformed
Step 1 report, or non-exact preflight produces a blocked Step 2 report.

## Explicitly deferred scope

MCP-specific tracing, automated-test discovery, downstream repositories,
graph, delta, multi-repository analysis, and any catalog mutation are outside
this proving slice. The source of every Step 2 fact is the generated Step 1
JSON report, identified by its deterministic summary hash.
