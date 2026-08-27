# Greenfield Behavior Handbook V1

## Purpose

The Greenfield behavior handbook is a deterministic, read-only navigation view
over the retained Step 1.5 through Step 5 artifacts. It organizes exact evidence
as a run summary, behavior register, implementation locators, impact, coverage,
and actions.

The handbook is not an evidence source. The retained Greenfield artifacts and
revision-bound source remain authoritative, and the handbook cannot affect
Step 6 eligibility or the Step 7 and Step 8 write boundaries.

## Inputs And Outputs

The builder consumes `step1.5.contract.json` and the Step 2, Step 3, Step 4, and
Step 5 JSON reports for one source repository and target revision. It rejects
schema, repository identity, revision, or changed-path mismatches.

It emits:

- `behavior-handbook.json`, the machine-readable V1 report.
- `behavior-handbook.md`, the progressively disclosed human view.

The JSON report uses `schema_version: "0.1"` and
`analysis_kind: "greenfield_behavior_handbook"`. Its top-level fields are
`status`, `input`, `summary`, `register`, `behaviors`, `unassigned_evidence`,
`gaps`, `warnings`, and `provenance`.

## Evidence Rules

An active generated `behavior_contract` relation supplies the behavior ID,
description, entry symbols, and source paths. Revision-bound generated contract
edges supply line locators. A source path without an exact edge line remains
explicitly path-only.

Downstream evidence is attached only when its interface ID equals the behavior
ID or a revision-bound Step 2 source anchor explicitly contains both IDs. Path
overlap, names, token similarity, and repository proximity do not create links.
Rows without an exact join remain in `unassigned_evidence`.
Repository-level and related-pull-request Step 3 rows have no behavior interface
key in V1, so they are retained there rather than inferred onto a behavior.

The projection preserves upstream classifications and statuses, including
`candidate`, `unresolved`, `stale`, `unavailable`, `unknown`, `missing`,
`not_modelled`, and `not_run`. It does not promote evidence.

## Status And Provenance

A handbook is complete only when all upstream reports are complete, no gaps or
unassigned rows remain, and every behavior is complete. All other valid reports
are partial.

Provenance records canonical SHA-256 values for all five inputs, the handbook
rule-set version, and the required non-writing boundary:

```text
read_only: true
catalog_mutation: none
github_writes: none
```

## Standalone Rendering

```bash
PYTHONPATH=. ./.venv/bin/python scripts/render_greenfield_handbook.py \
  --contract <bundle>/step1.5.contract.json \
  --step2 <bundle>/step2.json \
  --step3 <bundle>/step3.json \
  --step4 <bundle>/step4.json \
  --step5 <bundle>/step5.json \
  --output-json <bundle>/behavior-handbook.json \
  --output-markdown <bundle>/behavior-handbook.md
```

The command returns `0` for a valid complete or partial handbook. Invalid,
stale, or mismatched inputs return `2` before either output is written.
