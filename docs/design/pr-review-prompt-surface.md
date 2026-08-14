# PR review prompt surface

`catalog.pr_review_prompt` and `scripts/generate_pr_review_prompt.py` provide a
read-only orchestration surface for generating a complete LLM PR-review prompt.

```bash
PYTHONPATH=. ./.venv/bin/python scripts/generate_pr_review_prompt.py \
  --pr 48480 \
  --request "Review this PR for correctness and regressions."
```

The command fetches PR files, reviews, inline comments, issue comments, and
check runs through the existing `gh`-first metadata intake. It builds and
validates Step 0 in memory, obtains the exact PR head SHA, discovers or builds
an isolated repo-v1 catalog for that SHA in the internal `.cache/pr-review`
area, runs the current Step 1, Step 2, and Step 3 analyzers in bounded task
order, and prints a JSON envelope containing `prompt_text`, task contracts,
and the generated reports. `--prompt-only` prints only the prompt text.

The cache location is internal and is not a CLI/query parameter or prompt
field. A cached catalog is accepted only when its repo-v1 schema, active-build
ownership, integrity, source provenance, and `ia-main` target SHA exactly
match the PR head SHA. If the configured checkout lacks the exact base/head
objects, the command fetches them into an isolated internal bare Git cache;
the normal source checkout and canonical catalog are not changed.

Comments are included in the LLM prompt as analysis context, with their
revision and source metadata where available. The prompt explicitly forbids
copying comment text into the final review Markdown. A valid review with no
body remains available as comment metadata with unavailable text; it does not
block analysis. The final LLM response must use
`docs/review/pr-review-template.md` exactly.

Missing required CLI values, PR metadata, changed files, source revisions,
Git objects, comments/check-run collections, or exact catalog prerequisites
stop the command before analysis. Errors identify the failed prerequisite and
give a concrete remediation. Evidence gaps discovered after successful
resolution remain explicit in the Step 1--3 reports and prompt.

## Repo-v1 MCP surface

The repo-v1 MCP server is separate from the legacy `intacct_mcp` server:

```bash
PYTHONPATH=. ./.venv/bin/python -m repo_v1_mcp.server
```

The invoking agent supplies only a PR number and request to
`pr_review_prepare`. The server returns an opaque `analysis_id`, exact target
and catalog revisions, bounded counts, and Step 1--3 statuses. It does not
return private cache, manifest, checkout, or database paths.

The agent retrieves bounded evidence through `pr_review_evidence` using the
sections `summary`, `step0`, `comments`, `step1`, `step2`, and `step3`. Results
use opaque cursors and a maximum page size of 100. Empty report lists are
returned explicitly as `{"field": "…", "value": []}`, never omitted.
Comment bodies are returned as explicitly untrusted, normalized data; null,
empty, and whitespace-only bodies have unavailable text. The `pr_review` MCP prompt
defines the evidence-first workflow, and the
`repo-v1://review/pr-template` resource provides the canonical final-review
format.

PR preparation has one server-side deadline that starts before backend
execution and also bounds provenance validation, comment extraction,
redaction, and handle storage. Backend work runs in a terminating worker
process; a preparation timeout never returns or retains an analysis handle.
Git and GitHub operations also have bounded operation timeouts. Evidence
pagination starts its 10-second budget before in-memory lock acquisition and
uses that same budget for flattening and cursor storage. The review template is
loaded once at server startup so resource reads do not perform unbounded I/O.
Timeouts, missing inputs, unavailable exact revisions, invalid handles, and
catalog failures return structured errors with remediation. The in-memory
analysis handle expires; callers can rerun `pr_review_prepare` after expiry or
server restart.
