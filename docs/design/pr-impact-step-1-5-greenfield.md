# Greenfield PR Impact Step 1.5

Step 1.5 is the active Strands source-impact analysis stage. It consumes a
validated Greenfield Step 1 report and reads only exact target-revision source
blobs. Strands returns a structured trace containing affected symbols, exact
calls, behaviors, analyzed surfaces, and explicit unresolved or unavailable
findings.

The repository validates the response before it becomes evidence. Every edge
must use an exact changed/source path and target revision. AI output cannot
establish a cross-repository relationship, executed test coverage, ownership,
or a no-impact result.

Raw provider responses are normalized at the Step 1.5 trust boundary. The
provider may return surface records with `surface` and `status` fields, but the
validated and persisted trace always stores `surfaces` as an object mapping
surface names to status values. Provider-only metadata such as paths, line
ranges, and notes is not trusted as part of the canonical surface evidence.

The Strands runtime uses the standard AWS credential provider chain. Operators
may set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, optional
`AWS_SESSION_TOKEN`, `AWS_PROFILE`, and `AWS_REGION`, or rely on shared AWS
configuration and role-based credentials. Repository config must store only
non-secret defaults such as region, model, and timeout; see
`config/greenfield_strands.example.yaml` for the expected shape.

Greenfield runners also load a repo-local `.env` file when present. That file
can supply the same AWS variables above and the NexAU `LLM_*` values used by the
optional planner path. The checked-in example is
`config/greenfield_llm.example.env`.

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
PYTHONPATH=. ./.venv/bin/python scripts/run_greenfield_strands.py \
  --step1-report step1.json \
  --source-root /Users/aritra.ghosh/projects/main \
  --manifest config/workspace_repos.yaml \
  --output-dir artifacts/greenfield/run
```

Run the one-call flow as far as the evidence gates allow:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/run_greenfield_strands.py \
  --step1-report step1.json \
  --source-root /Users/aritra.ghosh/projects/main \
  --manifest config/workspace_repos.yaml \
  --output-dir artifacts/greenfield/run
```

When Step 6, Step 7, or Step 8 inputs are missing, the runner writes explicit
handoff artifacts such as `step6.handoff.json`, `step7.handoff.json`, and
`step8.handoff.json` with the reason and required next inputs. Supplying a
strict Step 6 request, Step 7 profiles, a target checkout, and Step 8 base
branch allows the same command to continue through local validation and no-write
Step 8 preparation.

The runner derives downstream repository candidates from explicit
`pr_impact_contracts` entries. It writes and reuses repository inventory
artifacts, attaches an optional explainable evidence-strength score to Step 2
candidates, and validates the existing Step 2–5 reports.

The wrapper writes `flow.handoff.json` atomically after every completed stage.
It binds each stage's named input and output files to SHA-256 digests, records
the exact source PR identity, and marks the terminal stage if the wrapper
fails. This handoff is orchestration provenance only: it does not upgrade
impact, test coverage, runtime validation, or Step 6/7 eligibility.

The evidence score is versioned and is not a probability. It does not replace
`confirmed`, `candidate`, `unresolved`, or `unavailable` classifications.
