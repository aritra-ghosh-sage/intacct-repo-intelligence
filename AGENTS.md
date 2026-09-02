# Greenfield Runner Instructions — `feature/step1-contract-evidence-federation`

This policy applies only to the checked-out branch
`feature/step1-contract-evidence-federation` and only to the Greenfield runner
flow. It is intentionally not guidance for the general catalog, repo-v1,
graph, delta, or other repository workflows.

## Mandatory preflight

Before planning, editing, running, or interpreting a Greenfield flow:

1. Run `git branch --show-current`. If it is not
   `feature/step1-contract-evidence-federation`, stop and report that this
   branch-specific policy does not apply.
2. Verify the live runner imports `greenfield.strands_planner` and exposes only
   `analyze`, `publish`, and `draft` modes in
   `scripts/run_greenfield_strands.py`. If either check fails, stop and trace
   the current branch before using this document.
3. Resolve and state the source repository, PR number, base/head revisions,
   output bundle path, and `run-context.json` identity before using an existing
   bundle. Never substitute evidence from another PR or revision.

## Supported entrypoint and modes

The only supported public entrypoint is:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/run_greenfield.py \
  --source-root "$HOME/projects/main" \
  --output-dir <immutable-bundle> \
  --pr <number> \
  --mode analyze
```

`scripts/run_greenfield.py` delegates to
`scripts/run_greenfield_strands.py`.

- `analyze` writes the local, identity-bound artifact bundle and makes no
  GitHub writes.
- `publish` writes the local bundle and idempotently publishes the canonical
  GitHub Check and marker-bound PR comment.
- `draft` additionally permits the guarded Step 8 draft-test-PR path. It fails
  closed unless all planning, target-evidence, validation, authorization, and
  publication gates pass.

Use the repo-local `.env` only through `greenfield.llm_env`; shell values take
precedence. Strands uses the standard AWS credential provider chain. Never put
credentials in artifacts, configuration committed for a run, or logs.

## Live runner flow and artifacts

The runner owns the full flow. Do not manually assemble compatibility reports
or call later stages from unverified files.

1. **Capture and source trace** — Step 1 establishes source identity;
   `run-context.json` records captured scope and tool limits. Step 1.5 uses
   `greenfield.strands_agent` to trace exact source blobs and writes
   `step1.5.trace.json` and `step1.5.contract.json`.
2. **Deterministic evidence** — repository context, impact discovery,
   inventory, and Steps 2–5 produce retained compatibility artifacts. They
   feed the planner; their validity is not proof of complete impact or executed
   coverage.
3. **Plan and analyze** — `greenfield.strands_planner` creates a bounded
   investigation lifecycle; the host uses `GreenfieldToolbox` for
   revision-bound read-only evidence. `planning-report.json` is planner
   provenance, not impact evidence. `analysis-report.json` is built from the
   captured context, deterministic reports, planner result, and retained tool
   ledger. Planner failure or incompleteness must produce explicit gaps and
   block draft eligibility.
4. **Projections** — the runner writes `behavior-impact-report.json`,
   `test-assessment.json`, `test-proposal.json`, and the PR review projection.
   Preserve unmatched, unavailable, unresolved, and no-evidence outcomes.
5. **Remediate and validate** — an automatic Step 6 request is possible only
   for an eligible `update_existing_test` or `add_missing_test` action with
   captured target revision, bounded existing paths, recorded edits, and target
   blob evidence. Otherwise retain the blocked handoff artifact.
6. **Publish** — `publication.json` is the single source for the Check and PR
   comment. It is hash-validated before GitHub publication.

`greenfield.flow_handoff.GreenfieldFlowHandoff` binds the ordered stages
`step1`, `request`, `capture`, `step1_5`, `repository_context`,
`impact_discovery`, `inventory`, `step2`–`step5`, `strands_planning`,
`analyze`, projections, Step 6–8/handoffs, and `publish`. Every completed
stage records exact input/output references and SHA-256 values; changed or
out-of-order handoffs are failures. `latest.json` is only a mutable convenience
pointer—evidence stays in the immutable, identity-bound bundle directory.

## Evidence and claim boundaries

- Use only retained, revision-bound source or toolbox results for factual
  claims. `confirmed` and `strong_candidate` claims require recorded citations.
- Preserve `confirmed`, `strong_candidate`, `candidate`, `unresolved`,
  `unavailable`, and `no_evidence` distinctly.
- Planner text, static/conventional relationships, semantic similarity,
  candidate-repository membership, and an empty search do not prove impact,
  coverage, runtime correctness, or PR eligibility.
- External test repositories remain discovery-only without revision-pinned
  relationship-contract and CI evidence. No relevant test is a valid evidence
  result, not a discovery failure.
- Never infer missing source facts, silently broaden paths, or replace an
  unavailable evidence state with a positive or negative conclusion.

## Step 6–8 and publication gates

- Step 7 requires strict Step 6 target evidence, an enabled central profile,
  a clean target checkout at the captured base revision, and a validated Step 7
  report. A blocked or unavailable handoff is not eligibility.
- Step 8 is the sole draft-PR write boundary. It requires the validated Step 7
  result, exact target/base-branch evidence, bounded edits, service
  authorization, and an idempotent GitHub operation.
- `draft` additionally requires complete Strands planning, Step 7 status
  `validated`, Step 8 status `created` or `reused`, and available handbook
  resynchronization when the captured target handbook requires it.
- Humans alone approve, mark ready for review, or merge. The runner must never
  approve or merge its own PR.

## Focused validation

Run the narrowest relevant test first, then broader Greenfield tests only when
the focused slice passes:

- runner, analysis, publication, and draft gates:
  `tests/test_greenfield_simplified_flow.py`
- SHA-bound stage handoffs: `tests/test_greenfield_flow_handoff.py`
- native planner lifecycle: `tests/test_greenfield_strands_planner.py`
- draft creation and authorization: `tests/test_greenfield_step8.py`
- replay behavior: `tests/test_greenfield_replay.py`
- `.env` precedence/loading: `tests/test_greenfield_llm_env.py`
- deterministic Step 2 candidates/contracts: `tests/test_greenfield_step2.py`

Run `git diff --check` after edits. Preserve existing generated Greenfield
bundle files and unrelated working-tree changes; do not regenerate, edit, or
use them as evidence for a different identity.

## Explicitly out of scope

After the mandatory preflight succeeds, ignore the general/legacy catalog,
repo-v1, graph, delta, and unrelated repository flows for this branch-specific
Greenfield task. This does not invalidate those workflows; it only excludes
them from this document and from Greenfield execution.
