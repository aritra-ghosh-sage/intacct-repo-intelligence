# Greenfield Four-Phase Flow

## Entry Point

```bash
PYTHONPATH=. ./.venv/bin/python scripts/run_greenfield.py \
  --source-root "$HOME/projects/main" \
  --output-dir <immutable-bundle> \
  --pr <number> \
  --mode analyze
```

The resolved source root for the current workspace convention is
`/Users/aritra.ghosh/projects/main`.

The greenfield runners load a repo-local `.env` file at startup if it exists.
Existing shell values win over file values. Use the shared `.env` for:

- Strands AWS runtime settings such as `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  optional `AWS_SESSION_TOKEN`, `AWS_PROFILE`, and `AWS_REGION`
- NexAU planner settings such as `LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL`

NexAU is the default Analyze orchestrator. Startup writes redacted capability
diagnostics to `telemetry.jsonl`; Analyze and Publish fall back to direct
Strands with an explicit `nexau_planner_unavailable` gap, while Draft fails
closed. Credentials never enter command output or artifacts.

## Phases

### Capture

Writes `run-context.json`. It binds the source PR and revisions, manifest,
explicit-first candidate scope, supplied evidence and handbook fingerprints,
locally inspected candidate revisions, and read-only tool budgets.

### Analyze

NexAU creates a bounded investigation plan, Strands executes each task through
the shared revision-bound toolbox, and the planner performs mandatory
challenge and synthesis tasks before `analysis-report.json` is finalized.
`planning-report.json` is audit provenance, not impact evidence; every
confirmed or strong-candidate claim remains bound to recorded toolbox results.

### Remediate And Validate

An eligible `update_existing_test` or `add_missing_test` action is converted into
the retained Step 6 patch contract. Exact target evidence, bounded existing
paths, clean application, central profiles, and Step 7 checks remain mandatory.
Owner approval is not a draft-creation prerequisite; the Step 8 human gate
remains required before ready-for-review or merge.

### Publish

`analyze` writes local artifacts only. `publish` writes `publication.json` and
creates or updates one canonical GitHub Check and one marker-bound PR comment.
`draft` requires complete planning, strict evidence, validation, and the
authorization boundary before the same idempotent publication writes. Humans
remain responsible for ready-for-review and merge.

## Compatibility Artifacts

Step 1-8 reports remain inspectable for replay and debugging, but callers should
not assemble them manually. `scripts/run_greenfield.py` is the supported
operator entry point and `scripts/run_greenfield_strands.py` is its internal
implementation.
