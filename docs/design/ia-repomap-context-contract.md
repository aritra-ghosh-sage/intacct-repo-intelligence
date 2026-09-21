# `ia_repomap` context contract

Date: 2026-09-18

Scope: Intacct repositories using the `app/source` repository-map slice.

This document is the canonical contract for the configured repository profile,
external Ripwire state, and the PR-context adapter. It records the assumptions
that make map output useful to an agent without presenting navigation evidence
as proof of impact.

## Purpose and boundaries

`ia_repomap` supplies ranked, task-shaped repository context to an agent or
coding harness. It is a navigation layer. Source, tests, build configuration,
and CI remain authoritative for consequential conclusions.

The first Intacct path uses the supported Ripwire PHP grammar for files under
`app/source`, including Intacct PHP-family suffixes. The approved repository
profile is configuration input. Generated indexes, caches, and context bundles
remain outside tracked source.

This contract does not define an MCP server, editor integration, LLM prompt,
GitHub write operation, or test execution. It does define the public
self-hosted `setup` and `review` commands and local Python module interface
below.

## Repository profile

The configured repository profile has this v1 shape:

```toml
schema_version = 1
engine = "ripwire"
scope = ["app/source"]
token_budget = 4000

php_family_extensions = [
  ".php", ".phtml", ".cls", ".ent", ".inc", ".cqry", ".rpt",
  ".menu", ".pol", ".wfl", ".shortcuts", ".qry", ".bin", ".map",
]

map_php_scope = ["app/source"]
```

The repository-map `token_budget` is a separate 4,000-token context budget.
The Strands model response ceiling is 2,048 tokens.

| Field | Type | Contract |
| --- | --- | --- |
| `schema_version` | integer | Must be `1`. |
| `engine` | string | Must be the supported engine, currently `ripwire`. |
| `scope` | array of strings | Currently exactly `["app/source"]`. |
| `token_budget` | positive integer | Default context budget for a request. |
| `php_family_extensions` | array of strings | Lowercase suffixes routed through the PHP grammar. |
| `map_php_scope` | array of strings | Directories in which `.map` is treated as PHP-family source. |

Validation rules are deliberately narrow:

- the only supported PR-context scope is `app/source`, which must exist;
- extensions must be lowercase suffixes beginning with `.`;
- `.map` is eligible only when its path is beneath `app/source`;
- unknown keys and unsupported engines are errors;
- the declaration contains no generated map data or source excerpts.

The self-hosted review command supplies the approved profile during setup. This
contract does not require users to copy internal setup, generated map, or agent
guidance files into a target repository.

## External Ripwire state

Ripwire indexes, caches, raw evidence, and report bundles are external state.
The caller supplies an external workspace, or the self-hosted `review` command
uses its local user-cache default. State is usable only for the exact clean PR
checkout and configured profile being reviewed. Missing, stale, dirty, or
incompatible state is `unavailable`; the workflow does not silently substitute
another revision or engine.

The review command prepares this state automatically and reuses a retained
state directory when it remains valid. It retains the detached PR worktree and
reports so a user can rerun or inspect the review. Operators should retain
state for active reviews and keep it outside the target repository. The
contract intentionally does not expose the internal artifact naming or
identity mechanics.

The self-hosted prerequisites are authenticated `gh`, an external
`RIPWIRE_SKILLS_DIR` containing the allowlisted files
`ripwire-change-check/SKILL.md`, `ripwire-write-tests/SKILL.md`, and
`ripwire-orient/SKILL.md`, and Bedrock settings consisting of
`AWS_REGION` or `AWS_DEFAULT_REGION`, `BEDROCK_MODEL_ID`, and credentials from
the standard boto3 chain or optional `AWS_PROFILE`. The three external files
are converted to compact internal profiles; they are not the profile format
and are not copied into the target repository.

## PR-context request and result

The Python adapter accepts an explicit request equivalent to:

```text
repo_root       repository checkout
artifact_root   external map-artifact root
base_ref       explicit PR base reference
token_budget    positive output budget
limit, offset   deterministic pagination
```

It verifies the readiness conditions, obtains the authoritative Git changed
file set, and invokes Ripwire with the prepared cache and
`--pr-context=<base_ref>`.

Ripwire XML is the canonical engine evidence and is retained verbatim. The
adapter may additionally expose normalized records in this shape:

```json
{
  "status": "ok",
  "identity": {
    "base_ref": "<base>",
    "head": "<40-character-head-sha>",
    "dirty": false
  },
  "changed_files": [
    {
      "path": "app/source/gl/GLSetupManager.cls",
      "change": "M",
      "symbols": [
        {
          "name": "save",
          "line": 123,
          "confidence": "candidate",
          "callers": [
            {
              "name": "validateSingleRequest",
              "path": "app/source/apar/CustomerPrintTemplateValidator.cls",
              "line": 30,
              "kind": "method",
              "confidence": "candidate"
            }
          ]
        }
      ]
    }
  ],
  "raw_xml": "<pr-context schema=\"ripwire.pr-context/v1\" ... />",
  "gaps": []
}
```

The result status is one of:

- `ok`: evidence was produced and identity checks passed;
- `unavailable`: a prerequisite or optional capability is missing;
- `error`: validation, invocation, or parsing failed.

Every truncation, unresolved relationship, ambiguous resolution, missing
prerequisite, and unavailable capability is represented as a diagnostic or
gap. Confidence follows the research contract: `confirmed`,
`strong_candidate`, `candidate`, `unresolved`, or `unavailable`.
The coordinator's PR-analysis report deliberately narrows model-authored
relationships to `candidate`, `unresolved`, or `unavailable`; `confirmed` is
reserved for host-owned Git and revision identity evidence.

The local Strands coordinator builds on this adapter through the versioned
`PRAnalysisRequestV1` model and `run_pr_analysis()` entry point. It runs
PR-context once. With changed files but no candidate symbols, supplied Bedrock
settings may enable one bounded degraded file/diff invocation; the report
includes `no_candidate_symbols`, uses `assessment="partial"`, and has no
symbol-level blast-radius rows. With no changed files, there is no seed and no
agent invocation. A host may explicitly opt in to a second bounded literal
inspection tool. The coordinator rechecks exact `HEAD` and clean-tree state
after the agent, retains raw XML outside the prompt, and atomically writes an
external `pr-analysis.json`, deterministic `pr-analysis.md`, and sanitized
inspection evidence. Static relationships remain candidate lower-bound
evidence and test areas remain `execution_status="not_run"`.

The self-hosted review command wraps this flow. It resolves the canonical PR
through authenticated `gh`, validates the caller-owned local repository, and
creates or reuses the retained detached worktree and external state before
calling the coordinator. Its JSON envelope reports the report directory and a
user-facing `remediation` list. It does not edit the supplied `--repo`, execute
tests, use MCP, or publish a GitHub review. The supplied source checkout's
files, index, branch, `HEAD`, and working tree remain untouched. Review setup
intentionally mutates only Git metadata needed for acquisition: fetched
objects, `FETCH_HEAD`, the private `refs/ia-repomap/pr/<number>/head` ref, and
the retained worktree registration.

### Diagnostic/host-only module interface

The package exposes public self-hosted `setup` and `review` commands plus three
lower-level module commands. `setup` prepares or reuses the exact detached
checkout and external prerequisites, then stops before analysis; `review`
performs the same setup or reuse automatically. Preparation and retrieval
remain separate for the lower-level API. The public `setup` and `review`
commands are listed first below; `prepare`, `pr-context`, and `symbol-impact`
are diagnostic/host-only interfaces, not a second self-hosted setup path. The
self-hosted host supplies the bundled process-local marker; users must not copy
a marker or internal setup files into the target repository. Run the lower-level
commands from the builder repository root, or make this package available on
`PYTHONPATH`.
The practical operator runbook is maintained in
[`ia_repomap_builder/README.md`](../../ia_repomap_builder/README.md#running-a-self-hosted-pr-review);
this section defines the command contract.

```shell
gh auth status

./.venv/bin/python -m ia_repomap_builder setup \
  https://github.com/owner/repository/pull/123 \
  --repo /path/to/local/repository \
  --workspace /safe/external/ia-repomap-review

./.venv/bin/python -m ia_repomap_builder review \
  https://github.com/owner/repository/pull/123 \
  --repo /path/to/local/repository \
  --workspace /safe/external/ia-repomap-review

./.venv/bin/python -m ia_repomap_builder prepare \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts

./.venv/bin/python -m ia_repomap_builder pr-context \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts \
  --base origin/main \
  [--output /safe/external/pr-context.json]

./.venv/bin/python -m ia_repomap_builder symbol-impact \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts \
  --symbol-path app/source/apar/CustomerPrintTemplateValidator.cls \
  --symbol-name buildTemplateFilters \
  [--limit 20 --offset 0] \
  [--output /safe/external/symbol-impact.json]
```

Each command emits one JSON envelope with this shape:

```json
{
  "schema": "ia-repomap.command-result/v1",
  "command": "pr-context",
  "status": "ok",
  "request": {},
  "result": {},
  "remediation": []
}
```

`result` preserves the existing adapter result shape, including raw XML. The
`symbol-impact` result contains `candidates`, `gaps`, `metrics`, `identity`,
and the verbatim impact XML; it does not infer a PR base because it is bound to
the prepared checkout's exact clean `HEAD`. An optional `--output` path
receives the byte-identical JSON envelope and must be
outside the target checkout; an existing path is not overwritten. Exit code
`0` represents `ok`, `3` represents `unavailable`, and `2` represents invalid
input or an execution error. Unavailable results include actionable
remediation while preserving the underlying status and diagnostics. The clean
checked-out `HEAD` is the PR head and `--base` is an explicit local Git ref;
the interface does not accept a GitHub PR number or perform remote PR lookup.
Callers must select or create the desired PR checkout before invoking it. A
PR number such as `50176` is descriptive metadata only: a test for that PR must
use its exact checked-out head and intended base commit as the command inputs.
For reproducible automation, pass the exact target-branch SHA as `--base`
rather than a moving branch name such as `origin/main`.

The adapter uses `hunk-enclosing-v1` to narrow Ripwire's file-wide definitions
to candidates whose inferred definition span overlaps a positive-side Git
hunk. Ripwire supplies definition start lines but not end lines, so each span
ends immediately before the next definition starts. The final definition is
extended through the final changed hunk. These inferred enclosing definitions
remain `candidate` navigation evidence rather than confirmed changed symbols.

Git hunk failures retain file-wide candidates and produce a
`hunk_range_unavailable` gap. A successful diff with no current-head lines
produces `hunk_no_head_lines`; hunks before the first line-bearing definition
produce `hunk_symbol_unresolved`; and Ripwire symbols without usable line
numbers produce `hunk_symbol_line_unavailable`. Raw Ripwire XML remains
verbatim canonical evidence in every successful result.

The adapter uses `direct-callers-v1` to normalize only `<caller>` rows attached
to hunk-selected symbols. Caller paths and positive line numbers are required;
malformed, out-of-scope, unresolved, and capped rows remain explicit gaps.
Direct callers are one-hop `candidate` navigation evidence, not confirmed
blast-radius conclusions. Per-file aggregate impact and affected-test paths are
normalized as candidate context with explicit cap and path gaps. Affected tests
become `not_run` report test areas; file-only impact cannot create symbol-level
blast-radius rows. Owners and co-change rows remain available only in canonical
XML; symbol-scoped impact is the separate on-demand expansion described below.

The on-demand `symbol-impact-v1` API accepts one hunk-selected symbol and runs
Ripwire's `--impact=file:symbol` query against the exact prepared index. Its
candidate reachers are normalized only when they have an in-scope path and a
positive line; the verbatim impact XML remains the canonical evidence. Ripwire
impact counts and rows are graph floors, not exhaustive totals or confirmed
runtime relationships. Caps, pagination, ambiguous and unresolved edges,
importer rows, and malformed locations remain explicit gaps. This expansion is
caller-selected; PR-context retrieval does not issue an impact query for every
candidate automatically.

Its request and result are separate from PR-context pagination:

```text
PrImpactRequest(repo_root, artifact_root, symbol_path, symbol_name, limit, offset)
PrImpactResult(status, candidates, raw_xml, gaps, diagnostics, metrics, identity)
```

The result is valid only for the exact clean `HEAD` used for the query. A
candidate contains `path`, `name`, positive `line`,
optional `kind`, and `confidence="candidate"`. A missing or stale index is
`unavailable`; malformed input, XML, or invocation failures are `error`.

The coordinator report uses schema `ia-repomap.pr-analysis/v1`. Its request
contains absolute `repo_root`, `base_ref`, `artifact_root`, and external
`output_dir`; its report contains host-controlled status and phase, exact
identity, changed-file seeds, candidate blast-radius/test rows, explicit gaps,
impacted-file rows, evidence digests, diagnostics, remediation, metrics, and
agent provenance. The model cannot choose paths, refs,
cache preparation, or tools. Source inspection is disabled unless the host
passes `allow_source_inspection=True`; inspection excerpts are in-memory only
and are not written to the report bundle. Model-authored narrative is retained
as candidate analysis, not as a source excerpt or authoritative source.

Normalized paths are repository-relative and authoritative for this adapter;
non-authoritative source-root metadata in raw XML is not part of the public
contract.

## Execution status

This record is append-only. Each completed slice adds a numbered row using the
same format; future work is not recorded as complete until its validation has
run. The entries describe this repository's implementation only. They do not
mean that a target `ia-app` checkout has been onboarded.

| Step | Status | Completed outcome | Validation |
| --- | --- | --- | --- |
| 1 | `complete` | Repository declaration and the `ia_repomap` contract are documented. | Contract, template, README, and repository guidance cross-reference the same schema. |
| 2 | `complete` | The Ripwire Intacct PHP-family extension patch is established, including `.map` only within `app/source`. | Patch identity and extension routing are covered by focused tests and the contract. |
| 3 | `complete` | External, revision-bound Ripwire state preparation is implemented. | Preparation is kept outside the target checkout and is consumed only for the selected review checkout. |
| 4 | `complete` | Exact-head readiness and named-cache validation are implemented. | Readiness checks identity, base/merge-base resolution, and Ripwire `--doctor` cache use. |
| 5 | `complete` | PR-context invocation returns normalized changed-file and candidate-symbol seeds. | Focused tests cover Git status normalization, command construction, and XML symbol parsing. |
| 6 | `complete` | Explicit statuses, gaps, raw XML retention, and deterministic normalized evidence are implemented. | Tests cover unavailable/error paths, truncation and ambiguity gaps, raw XML, and repeatability; runtime metrics are excluded from deterministic comparisons. |
| 7 | `complete` | Lightweight validation and the P1/P2 regressions are complete. | The test suite passes with 40 tests (3 opt-in skips), including doctor-cache exit handling, and `git diff --check` passes. |
| 8 | `complete` | The configured repository profile and agent guidance are available to the self-hosted workflow; target-repository onboarding remains outside this slice. | Configuration and target-repository runtime state are not asserted by this documentation pass. |
| 9 | `complete` | Hunk-to-enclosing-symbol attribution narrows file-wide Ripwire definitions while retaining explicit fallback gaps and canonical XML. | Focused tests cover hunk parsing, inferred spans, boundary cases, missing lines, deletion/rename behavior, and Git failures. An isolated PR #50176 rerun reduced 14 candidates to `buildTemplateFilters` at line 126 and attributed all four hunks. |
| 10 | `complete` | A local module interface performs explicit index preparation and readiness-gated PR-context retrieval with JSON results and remediation. | CLI-focused tests cover command construction, status/exit mapping, output safety, raw JSON preservation, and the no-implicit-preparation boundary; the complete suite passes. |
| 11 | `complete` | Direct caller rows are normalized only for hunk-selected symbols as `direct-callers-v1`, with explicit malformed, out-of-scope, and truncation gaps. | Focused and complete suites pass; two isolated PR #50176 runs returned `buildTemplateFilters` with `validateSingleRequest` at line 30, `4 → 1` caller filtering, byte-identical XML, and deterministic normalized output. |
| 12 | `complete` | On-demand `symbol-impact-v1` expands one hunk-selected symbol through Ripwire's symbol-scoped impact query while retaining lower-bound and graph uncertainty evidence. | Focused and complete suites pass; two isolated PR #50176 runs returned `validateSingleRequest` at line 30 for `buildTemplateFilters`, with `defs=1`, `reaches=1`, `radius_tested=0`, `radius_untested=1`, byte-identical XML, and deterministic normalized output. |
| 13 | `complete` | The local JSON module interface exposes `symbol-impact` for agent-selected, on-demand impact expansion without implicit preparation. | CLI help, argument validation, status/remediation, serialization, and output-safety tests pass; two exact-revision PR #50176 command runs return `validateSingleRequest` at line 30 with byte-identical XML and deterministic envelopes. |
| 14 | `complete` | A local Strands coordinator enforces bounded impact, opt-in inspection, evidence labels, and external report output. | The documentation records the v1 boundary; no live Bedrock or target-repository run is claimed here. |
| 15 | `complete` | Ripwire per-file impact and affected-test rows are bounded candidate context; affected tests are `not_run` report areas. | The documentation records candidate-versus-executed-coverage semantics; no test execution is part of v1. |
| 16 | `documented` | Self-hosted `gh` setup, local `--repo` behavior, automatic setup/reuse, retained worktrees, external reports, allowlisted skill guidance, remediation, and no-MCP/no-test boundaries are documented. | Documentation-only slice; runtime validation is intentionally not claimed. |

## Acceptance criteria

The map is ready for PR context only when:

1. the checkout is clean and its exact `HEAD` is known;
2. the explicit base reference and merge base resolve;
3. external Ripwire state is available for the exact checkout and configured
   profile;
4. the supported Ripwire binary recognizes the configured Intacct extensions;
5. output remains within the configured token budget and discloses pagination
   or truncation;
6. source excerpts and generated artifacts remain outside tracked source;
7. candidate test areas are labeled `execution_status="not_run"` and are not
   presented as executed coverage;
8. the self-hosted review path does not use MCP or publish a GitHub review.

The broader engine bakeoff measures recall, rank, edge precision, latency,
memory, cache size, and maintenance cost separately. Ripwire's published
comparison is vendor-supplied evidence and does not replace the Intacct
evaluation.

## Assumptions and deferred work

- Ripwire is the leading engine for the first Intacct implementation.
- A local pinned binary is an acceptable internal engine boundary.
- Ranked symbols and relationships are navigation evidence, not proof.
- Static analysis may miss dynamic dispatch, reflection, or configuration-
  driven wiring; those cases remain explicit gaps.
- Generated artifacts are external and retained only for the selected review
  checkout and profile.
- MCP, editor, harness, test execution, and GitHub publication are deferred;
  the self-hosted local review command and Python module interface are the v1
  boundary.

## Measurement log

This appendix records empirical seed-over-seeding checks. Evidence remains
external to tracked source.

| Date | PR and revision | Changed file | Seeds | Seeds in diff hunks | Ratio | Decision |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 2026-09-09 | #50176, base `82025f5229a43a67d147897b9da93d9b1a6ac7dd`, isolated head `adf48f320f4f546738cc8520acb00f7947235580` with #50173 metadata applied locally | `app/source/apar/CustomerPrintTemplateValidator.cls` | 14 | 0 | 0.0 | hunk-level extraction needed: yes |

The run returned one in-scope modified file and three out-of-scope metadata
changes. Raw XML was byte-stable across two runs; normalized output was equal
after excluding elapsed time; estimated output was 3,810 of the 4,000-token
budget. Ripwire disclosed the bounded 500-commit history, truncation, and
ambiguous/unresolved graph counts as explicit gaps. The measurement uses the
real PR #50176 source change, while the onboarding metadata from PR #50173 was
applied only in an isolated local worktree; neither PR is represented as
merged by this entry.

### Post-remediation validation

| Date | PR and revision | Candidates before | Candidates after | Selected candidate | Hunks attributed | Result |
| --- | --- | ---: | ---: | --- | ---: | --- |
| 2026-09-09 | #50176, base `82025f5229a43a67d147897b9da93d9b1a6ac7dd`, isolated head `adf48f320f4f546738cc8520acb00f7947235580` | 14 | 1 | `buildTemplateFilters` at line 126 | 4/4 | accepted |

Two runs from the same explicit worktree returned byte-identical raw XML and
equal normalized output after excluding elapsed time. The output used 3,812
of the 4,000-token budget. Evidence is retained outside tracked source. The
result validates local hunk attribution only; onboarding review and merge
remain pending.
