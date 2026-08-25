# Greenfield PR Impact Step 7

Step 7 validates a Step 6 patch proposal against an exact target base commit.
It is read-only with respect to the caller's checkout, GitHub, and the catalog:
it creates a temporary local clone, applies and validates the patch there, runs
centrally approved checks through an injected runner, and emits a validation
artifact. Step 8 remains responsible for branch and draft pull-request creation.

## Operational sequence

1. Produce a strict Step 6 report with exact target evidence, both owner
   approvals, and `eligibility_profile: step7`.
2. Select an enabled repository profile from
   `config/greenfield_step7_profiles.yaml` and prepare a schema `0.2` request.
3. Supply a clean target checkout whose GitHub `origin` and `HEAD` exactly match
   the target repository and base SHA.
4. Execute Step 7 through a runner. The local runner is development-only;
   sandbox metadata is retained as execution evidence, but Step 7 cannot
   authenticate an external sandbox attestation or authorize a PR.
5. Retain the Step 7 report for the trusted Step 8 handoff documented in
   [Greenfield PR Impact Step 8](pr-impact-step-8-greenfield.md).

The central profile, not the caller or AI, owns commands, timeouts, diff limits,
output limits, and source/generated path classifications. Every patch path must
have exactly one classification. Disabled or missing profiles produce explicit
blocked preparation artifacts.

## Validation gates

The validator:

1. verifies Step 6, request, profile, runner, source, target, and patch
   fingerprints;
2. rejects missing, non-GitHub, or mismatched origins and dirty or wrong-SHA
   target checkouts;
3. permits only tracked regular blobs with mode `100644` or `100755` and rejects
   symlinks, gitlinks, additions, deletions, renames, and binary diffs;
4. verifies every before hash, runs `git apply --check`, applies the patch in a
   temporary clone, and verifies every after hash;
5. enforces approved paths, exact source/generated classification, generated
   allowlists, and diff-size limits;
6. runs non-shell format, lint, compile/type, targeted, integration, and
   regression commands with bounded time and output;
7. rejects command-induced changes outside the expected patch and records stable
   generation, validation, runner-attestation, and report fingerprints.

Any failed or unavailable gate produces an actionable report. Every Step 7
report has `pr_eligible: false`, including a successful sandbox execution. A
trusted Step 8/orchestrator boundary independently verifies a production
sandbox attestation before it can authorize PR creation. Step 7 itself remains
non-PR-eligible.

Validation proves integrity of the declared patch and declared checks. It does
not prove complete change impact, complete test discovery, or business coverage.

## CLIs

Prepare a request from the exact Step 6 report and central profile registry:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/prepare_greenfield_step7.py \
  --step6-report step6.report.json \
  --profiles config/greenfield_step7_profiles.yaml \
  --output step7.request-or-blocked.json
```

Run development-only local validation:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/validate_greenfield_step7.py \
  --step6-report step6.report.json \
  --request step7.request.json \
  --profiles config/greenfield_step7_profiles.yaml \
  --runner local \
  --target-checkout /path/to/target-checkout \
  --output step7.report.json
```

Preparation exits `0` for a ready request, `1` for a blocked handoff, and `2`
for malformed input or execution failure. Validation exits `1` for every
completed Step 7 validation and `2` for malformed input or validator execution
failure.

`examples/greenfield/step7-validation-profiles.example.yaml` documents the
enabled profile shape, and `examples/greenfield/step7-artifact-shapes.example.json`
documents the request/report envelopes. Neither is an approved production
artifact or policy. The two current
Intacct target profiles remain disabled until repository owners supply and
approve their exact commands, limits, and path classifications.

## Infrastructure boundary

This repository defines the `Step7Runner` interface and a local subprocess
implementation. Production orchestration must inject a sandbox adapter that
controls checkout mounting, resources, network access, credentials, and durable
artifact retention. A local runner attestation can never authorize Step 8.

Step 7 performs no branch, commit, GitHub, pull-request, catalog, SQLite, or
Ladybug writes.
