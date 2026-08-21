# Greenfield PR Impact Step 7

Step 7 validates a Step 6 patch proposal against an exact target base commit.
It is read-only with respect to the caller's checkout and GitHub: it creates a
temporary local clone, applies and validates the patch there, runs declared
checks, and emits a validation artifact. Step 8 is responsible for branch and
draft pull-request creation.

## Inputs

Step 7 consumes:

- a strict Step 6 report with exact target evidence and both owner approvals;
- a Step 7 request tied to the canonical Step 6 report hash;
- a clean local checkout whose `HEAD` equals `target.base_revision`.

The request declares structured, non-shell commands for formatting, lint,
compilation or type checks, targeted tests, integration tests, and regression
tests. All six categories are required for a validated result; compilation or
type validation is represented by the combined `compile_or_type` category.

## Validation gates

The validator:

1. verifies source, target, patch, and Step 6 fingerprints;
2. rejects dirty or wrong-revision target checkouts;
3. verifies every patch before hash against the target base;
4. runs `git apply --check` and applies the patch in a temporary clone;
5. rejects additions, deletions, renames, out-of-scope paths, unrelated files,
   unclassified or disallowed generated-file changes, and excessive diffs;
6. runs all declared commands with `shell=False`, bounded output, and bounded
   timeouts; ignored build/cache outputs are outside the PR diff policy;
7. rejects commands that mutate the expected patched worktree; and
8. records stable generation and validation fingerprints.

Any failed or unavailable gate produces an actionable failure report and sets
`pr_eligible` to false. No PR is created.

## CLI

```bash
PYTHONPATH=. ./.venv/bin/python scripts/validate_greenfield_step7.py \
  --step6-report step6.report.json \
  --request step7.request.json \
  --target-checkout /path/to/target-checkout \
  --output step7.report.json
```

Exit code `0` means validated, `1` means failed or blocked validation, and `2`
means malformed input or validator execution failure.
