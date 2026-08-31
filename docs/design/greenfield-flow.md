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

## Unattended Runtime Setup

The automated runner must use a dedicated environment for the Greenfield
runtime. NexAU 0.4.1 requires a newer OpenAI client than the legacy `dev`
group, so these dependency sets must not be installed into the same environment.
From the repository root, bootstrap the runtime with the committed lockfile:

```bash
scripts/bootstrap_greenfield_runtime.sh
```

The helper provisions `.venv-greenfield` by running
`uv sync --locked --extra nexau-planner --no-group dev`. Set `GREENFIELD_VENV`
when the worker image uses a different environment location.

The job must invoke `.venv-greenfield/bin/python` (or the equivalent path in
the worker image), and must not depend on a developer's existing `.venv`.
`LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, AWS credentials, and GitHub
credentials belong in the deployment secret store or injected environment;
they must never be placed in the lockfile, configuration artifacts, or logs.

E2B is not required for `analyze` or `publish`; NexAU uses its local sandbox
by default. The runner records E2B availability as an optional capability. E2B
must be provisioned separately only for a Step 7 profile that explicitly
requires a production sandbox, using the `e2b` package (not
`e2b-code-interpreter`) and an injected `E2B_API_KEY`. The sandbox worker
image should provision it with uv's installer, for example:

```bash
uv pip install --python .venv-greenfield 'e2b>=2.12,<3'
```

That optional sandbox dependency must be baked into the worker image or its
own locked runtime profile; it must not be installed opportunistically during
a PR run. If that profile is selected and E2B is unavailable, `draft` must
fail before any GitHub write.

The deployment must verify that the configured source checkout resolves to
`/Users/aritra.ghosh/projects/main` for the current workspace convention, or
fail capture with a diagnostic rather than silently substituting another path.

NexAU is the mandatory Analyze orchestrator. Startup writes redacted capability
diagnostics to `telemetry.jsonl`. When NexAU is unavailable or incomplete,
Analyze writes an explicit degraded evidence report with the applicable NexAU
gap and no replacement direct-Strands analysis; Draft fails closed until NexAU
planning completes. Credentials never enter command output or artifacts.

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
