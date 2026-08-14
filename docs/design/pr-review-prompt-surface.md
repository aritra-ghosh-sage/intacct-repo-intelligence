# PR review prompt surface

`catalog.pr_review_prompt` and `scripts/generate_pr_review_prompt.py` provide a
read-only orchestration surface for generating a complete LLM PR-review prompt.

```bash
PYTHONPATH=. ./.venv/bin/python scripts/generate_pr_review_prompt.py \
  --pr 48480 \
  --request "Review this PR for correctness and regressions."
```

Use `--progress` for long-running operator runs. Progress is written to stderr;
JSON output remains clean on stdout:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/generate_pr_review_prompt.py \
  --pr 48480 \
  --request "Review this PR for correctness and regressions." \
  --progress > /tmp/pr-review.json
```

## CLI input/output contract

Required inputs are `--pr` (a PR number or GitHub URL) and `--request`. The
optional bounded controls are `--manifest`, `--repo-key`, `--max-hops` (`1` or
`2`), and `--min-confidence`; `--prompt-only` changes the output mode and
`--progress` changes only diagnostics routing.

Without `--prompt-only`, successful execution writes one JSON envelope to
stdout. The envelope contains the normalized input, Step 0 and Step 0
validation, Step 1--3 reports, task contracts, provenance, and `status`.
`status` may be `ready`, `partial`, or `blocked`; `partial` and `blocked` are
valid evidence-preserving results and still return exit code `0` when the
required source/catalog prerequisites were resolved. With `--prompt-only`,
stdout contains only `prompt_text` followed by a newline.

Progress and operator diagnostics are written to stderr, including exact-SHA
resolution, isolated-catalog build, and cache-hit milestones. Prerequisite
failures return exit code `1` with a stable coded error and remediation; they do
not emit a misleading review envelope.

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
The isolated bare cache reference-links the configured checkout's Git object
store, so an exact PR fetch transfers only objects absent from that checkout;
the configured checkout and cache validate the remote identity and exact
base/head commit IDs before catalog construction. Stale `tmp_pack_*` files are
removed only inside the target cache before reuse. A timed-out Git operation
terminates its child process group and uses bounded cleanup so partial SSH or
index-pack processes are not retained.

Resolution is reported in the JSON envelope without exposing internal paths:

- `catalog_resolution`: `built` or `cache_hit`;
- `source_resolution`: `configured_checkout`, `internal_fetch`, or
  `internal_cache`;
- `input.target_revision` and `provenance.catalog_revision`: the exact PR head
  SHA used for all repo-v1 facts.

The CLI stops before analysis for metadata, source, or catalog prerequisite
failures and returns a structured error with a remediation. Once exact source
and catalog resolution succeeds, Step 1--3 extraction failures are retained
as `blocked`, while unavailable or incomplete evidence is retained as
`partial`; neither state is presented as no impact. The process exit status is
therefore separate from the analysis status in the JSON envelope.

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

## CLI validation snapshot

The following is dated evidence, not a current catalog count. On 2026-08-14,
PR 49359 (`63075a64939f305ceb73721a436c56236878bf25`) completed the CLI flow:

- isolated catalog build: `205.39s`, `internal_fetch`;
- repeat cache hit: `9.05s`, `internal_cache`;
- Step 0 validation: pass; overall result: `partial` with explicit Step 1--3
  gaps;
- cached SQLite: `PRAGMA integrity_check=ok`, zero foreign-key violations;
- cached catalog counts: 23,711 files, 165,287 symbols, 173,160 relationships,
  1,848 entities, 3,736 OpenAPI documents, 2,799 REST endpoints, and 579 UI
  surfaces.
- focused PR-review tests: `33 passed`; relevant repo-v1/PR-impact/MCP
  regression slice: `163 passed`; final orchestrator QA score: `100/100`.

The canonical catalog remained unchanged. A fresh live retry is dependent on
GitHub API connectivity; metadata intake fails closed when `gh api` is
unavailable.

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
