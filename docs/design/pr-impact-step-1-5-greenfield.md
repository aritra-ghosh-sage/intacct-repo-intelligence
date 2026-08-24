# Greenfield PR Impact Step 1.5

Step 1.5 is the active Codex source-impact analysis stage. It consumes a
validated Greenfield Step 1 report and reads only exact target-revision source
blobs. Codex returns a structured trace containing affected symbols, exact
calls, behaviors, analyzed surfaces, and explicit unresolved or unavailable
findings.

The repository validates the response before it becomes evidence. Every edge
must use an exact changed/source path and target revision. AI output cannot
establish a cross-repository relationship, executed test coverage, ownership,
or a no-impact result.

The validated trace is written as `step1.5.trace.json`. The existing behavior
contract generator then writes `step1.5.contract.json`; Step 2 consumes that
contract through its existing `--generated-contract` option.

## CLI

Run Step 1.5 directly:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/trace_greenfield_step1_5.py \
  --step1-report step1.json \
  --source-root /Users/aritra.ghosh/projects/main \
  --trace-output step1.5.trace.json \
  --contract-output step1.5.contract.json
```

Run Step 1.5 through Step 5:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/run_greenfield_codex.py \
  --step1-report step1.json \
  --source-root /Users/aritra.ghosh/projects/main \
  --manifest config/workspace_repos.yaml \
  --output-dir artifacts/greenfield/run
```

The runner derives downstream repository candidates from explicit
`pr_impact_contracts` entries. It writes and reuses repository inventory
artifacts, attaches an optional explainable evidence-strength score to Step 2
candidates, and validates the existing Step 2–5 reports.

The evidence score is versioned and is not a probability. It does not replace
`confirmed`, `candidate`, `unresolved`, or `unavailable` classifications.
