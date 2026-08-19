# PR Impact Step 1 Federation Artifact

Step 1 consumes an optional, normalized GitHub evidence artifact alongside the
existing revision-pinned Step 0 fixture. The artifact is external JSON and is
not part of the repo-v1 SQLite catalog. This keeps the current read-only
Step 1-to-Step 4 flow unchanged.

## Contract

The current artifact schema is `0.2` and has analysis kind
`pr_impact_metadata`. It records:

- source repository and manifest `repo_key`;
- PR number, URL, metadata, base SHA, and head SHA;
- changed paths, statuses, and rename metadata;
- reviews and issue comments already available from the intake;
- explicit GitHub cross-reference events as `linked_issues`;
- Actions `workflow_runs` and their `workflow_jobs`;
- commit `check_runs` for the exact PR head SHA.

The normalized artifact retains provider endpoints and a canonical
`provenance.evidence_sha256`. The fingerprint excludes only fetch time and the
stored fingerprint itself. Collection order is deterministic.

`evidence_status` records `available`, `empty`, `unavailable`, or
`not_requested` for linked issues, workflow runs, workflow jobs, and check
runs. An empty or unavailable collection is not a claim that no evidence or
impact exists.

## Revision binding

The Git diff remains authoritative for changed paths and statuses. Step 1
requires the artifact repository, PR number, base SHA, head SHA, and changed
path/status set to match the fixture and exact Git diff.

Every workflow run and check-run record must identify the exact PR head SHA.
Every workflow job must reference a workflow run present in the artifact.
Evidence for another revision blocks the metadata-bound Step 1 analysis.

Schema `0.1` artifacts remain readable for compatibility, but they do not
provide the new federation collections or fingerprint guarantees.

## Linked-issue boundary

Only explicit GitHub `cross-referenced` timeline events are normalized. Issue
relationships are context evidence; they do not create impact relationships
or downstream repository conclusions.

## Flow boundary

The artifact is read by the existing optional `--metadata` input. The current
exact Git diff validation, SQLite read-only preflight, direct surfaces,
`downstream_repositories: []`, and Step 2–4 sequencing remain unchanged.
