# Greenfield PR Impact Step 1

Greenfield Step 1 captures an immutable, repository-neutral source pull-request
evidence artifact. It is separate from repo-v1 Step 1, which performs direct
`ia-main` tracing over an exact-target SQLite catalog. Greenfield Step 1 does
not read or modify SQLite, build a semantic index, resolve impact, or write to
GitHub.

## Contract

The report schema is `0.1` and the analysis kind is
`greenfield_pr_impact_step_1`.

The required `input` fields are:

- `repository` and stable `repo_key`
- positive `pr_number`
- `base_sha` and `head_sha`
- compatibility aliases `base_revision` and `target_revision`
- sorted, unique `changed_paths`

`changed_files` is non-empty for every non-blocked report. Each row contains
the exact `path`/`filename`, a GitHub file status, and optional file metadata.

The report also contains normalized `pr_metadata`, `linked_issues`,
`workflow_runs`, `workflow_jobs`, and `check_runs`. Linked issues are context
only and never establish a repository relationship. Workflow and check records
must bind to the exact source `head_sha`; every job must reference an included
workflow run.

## Evidence states and provenance

The report status is `complete`, `partial`, or `blocked`.

Optional evidence collections use `available`, `empty`, `unavailable`, or
`not_requested`. Empty and unavailable evidence remain explicit and do not
mean that no impact or test exists.

The provenance contains the read-only boundary, provider and endpoint context,
source metadata schema, and `evidence_sha256`. The fingerprint is deterministic
and excludes fetch time and the stored fingerprint itself.

## CLI

```bash
PYTHONPATH=. ./.venv/bin/python scripts/trace_greenfield_step1.py \
  --manifest config/workspace_repos.yaml \
  --repo-key ia-main \
  --pr <number> \
  --output step1.json

PYTHONPATH=. ./.venv/bin/python scripts/validate_greenfield_step1.py \
  --report step1.json
```

The capture CLI uses the existing read-only GitHub metadata provider through a
greenfield adapter and writes the report atomically. Capture failures produce a
stable blocked report and a non-zero exit code. No GitHub write, catalog
refresh, graph build, semantic-index build, or downstream repository mutation
is performed.

## Handoff to Step 2

Greenfield Step 2 consumes this artifact directly. It uses the exact source
repository, target revision, and changed paths from `input` and retains the
complete Step 1 report fingerprint in its provenance. Contracts and CI
evidence must match the Step 1 source revision before they can produce their
stronger classifications.
