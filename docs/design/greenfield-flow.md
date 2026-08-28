# Greenfield Four-Phase Flow

## Entry Point

```bash
PYTHONPATH=. ./.venv/bin/python scripts/run_greenfield.py \
  --source-root "$HOME/projects/main" \
  --output-dir <immutable-bundle> \
  --pr <number>
```

The resolved source root for the current workspace convention is
`/Users/aritra.ghosh/projects/main`.

## Phases

### Capture

Writes `run-context.json`. It binds the source PR and revisions, manifest,
explicit-first candidate scope, supplied evidence and handbook fingerprints,
locally inspected candidate revisions, and read-only tool budgets.

### Analyze

Strands uses repository handbooks and bounded tools to inspect explicit
contracts first and then screen all enabled `discovery_eligible` repositories.
It writes `analysis-report.json`. Confirmed and strong-candidate rows require
recorded source or tool evidence.

`--planner-mode shadow` optionally records a NexAU `planning-report.json` and
`analysis-report.nexau.json` beside the authoritative Strands report. Shadow
planning is read-only and cannot reach remediation, publication, or GitHub.
`--planner-mode active` is reserved for a separately accepted rollout and still
uses the same captured-scope and analysis-report validators.

### Remediate And Validate

An eligible `update_existing_test` or `add_missing_test` action is converted into
the retained Step 6 patch contract. Exact target evidence, bounded existing
paths, clean application, central profiles, and Step 7 checks remain mandatory.
Owner approval is not a draft-creation prerequisite.

### Publish

Writes `publication.json`, optionally creates the validated draft test PR, and
creates or updates one canonical GitHub Check and one marker-bound PR comment.
Humans remain responsible for ready-for-review and merge.

## Compatibility Artifacts

Step 1-8 reports remain inspectable for replay and debugging, but callers should
not assemble them manually. `scripts/run_greenfield_strands.py` implements the
flow; `scripts/run_greenfield_codex.py` is only a deprecated shim.
