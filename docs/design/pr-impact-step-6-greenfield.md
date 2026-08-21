# Greenfield PR Impact Step 6

Step 6 generates a deterministic test-patch proposal and a handoff artifact
for a downstream AI agent. It does not capture target evidence, modify a
checkout, call GitHub, create branches, or create pull requests.

## Inputs

Step 6 consumes validated Step 1, Step 3, Step 4, and Step 5 reports, plus a
separate request containing:

- source PR identity, revisions, changed paths, and diff fingerprint;
- exact report fingerprints and Step 5 action identity;
- target repository and exact target base revision;
- target file contents and SHA-256 hashes;
- exact allowed paths and old/new edit fragments;
- a registered template and validation plan.

The target/edit evidence package is an upstream prerequisite. Missing or
inconsistent evidence blocks generation; Step 6 does not repair or infer it.

## v1 templates

The application-owned template registry provides update-only templates:

- `gwdata_gl_existing_case_update_v1`: one CSV test-definition file and its
  explicitly paired request/response XML files under `testdefinitions/` and
  `testscripts/`.
- `restapi_existing_case_update_v1`: one Gherkin feature and explicitly
  referenced JSON fixtures under `features/`.

Templates apply exact old/new fragments exactly once. They preserve unrelated
content and do not create files, infer pairings, or reformat complete files.
New-test actions, rename/removal actions, and compatibility-failure actions are
not generated in v1.

## Output

The report is schema `0.1` with analysis kind
`greenfield_pr_impact_step_6`. Its status is one of:

- `ready_for_ai_pr`;
- `blocked`;
- `not_generated`;
- `generation_failed`.

A ready report contains the modified files, before/after hashes, unified diff,
template identity, deterministic PR title/body sections, validation plan,
allowed paths, agent restrictions, provenance, patch fingerprint, proposal ID,
and idempotency key.

Only a ready report may be consumed by a later AI PR agent. The agent must use
the target base revision and allowed paths and must not merge or approve.

## CLI

```bash
PYTHONPATH=. ./.venv/bin/python scripts/trace_greenfield_step6.py \
  --request step6.request.json \
  --step1-report step1.json \
  --step3-report step3.json \
  --step4-report step4.json \
  --step5-report step5.json \
  --output step6.json

PYTHONPATH=. ./.venv/bin/python scripts/validate_greenfield_step6.py step6.json
```
