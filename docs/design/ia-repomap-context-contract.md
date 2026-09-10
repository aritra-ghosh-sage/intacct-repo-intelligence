# `ia_repomap` context contract

Date: 2026-09-08

Scope: Intacct repositories using the `app/source` repository-map slice.

This document is the canonical contract for the repository declaration,
revision-bound map artifacts, and the PR-context adapter. It records
the assumptions that make map output useful to an agent without presenting
navigation evidence as proof of impact.

## Purpose and boundaries

`ia_repomap` supplies ranked, task-shaped repository context to an agent or
coding harness. It is a navigation layer. Source, tests, build configuration,
and CI remain authoritative for consequential conclusions.

The first Intacct path uses the patched Ripwire PHP grammar for files under
`app/source`, including Intacct PHP-family suffixes. The declaration and agent
guidance are committed to a participating repository. Generated indexes,
caches, and context bundles remain outside tracked source.

This contract does not define an MCP server, editor integration, installable
public CLI, LLM prompt, or GitHub write operation. It does define the local
Python module interface used by developers and coding harnesses below.

## Repository declaration

Each participating repository has a root `.ia-repomap.toml` with this v1
shape:

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

The root `AGENTS.md` should include the repository-context discovery section.
It tells agents to consult this marker, request task-shaped context, and
verify returned relationships in source. Markdown guidance is advisory;
the TOML marker is the machine-readable discovery point.

## Revision-bound artifacts

The readiness step prepares Ripwire's external index with `--index-out`. The
artifact root is supplied by the caller and is not inside the target checkout:

```text
<artifact-root>/<repository-id>/<head-sha>/<configuration-id>/<engine-id>/
  index.lean.ripwirecache
  index.rich.ripwirecache
  manifest.json
```

The manifest is JSON with this minimum shape:

```json
{
  "schema": "ia-repomap.manifest/v1",
  "repository": {
    "id": "<stable-repository-id>",
    "revision": "<40-character-head-sha>",
    "dirty": false
  },
  "configuration": {
    "scope": ["app/source"],
    "digest": "<64-character-SHA-256>"
  },
  "engine": {
    "name": "ripwire",
    "version": "<reported-version>",
    "patch": "ripwire-v0.4.0-intacct-repomap.patch",
    "patch_sha256": "<patch-digest>",
    "binary_sha256": "<executable-digest>",
    "id": "<engine-identity>",
    "extensions": [".php", ".phtml", ".cls", "...", ".map"]
  },
  "artifacts": {
    "lean_cache": "index.lean.ripwirecache",
    "rich_cache": "index.rich.ripwirecache"
  }
}
```

The exact clean `HEAD`, scope, configuration digest, engine version,
executable digest, and parser/patch identity must match before an artifact is
used. The engine identity is the SHA-256 of the version, patch digest, and
executable digest joined by newlines. Consequently, rebuilding different
executable bytes cannot reuse an existing artifact directory even when the
reported version is unchanged. A stale, missing, or dirty artifact is
unavailable; the PR adapter does not silently perform a cold build.

Retention: retain artifacts for the current active HEAD and the two immediately preceding SHAs per repository. Purge on branch deletion. Operator must not delete the artifact for an in-flight PR readiness check.

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

### Local module interface

The package exposes two explicit module commands. Preparation and analysis are
separate so PR-context retrieval never builds or refreshes an index implicitly:
run them from the builder repository root, or make this package available on
`PYTHONPATH`.

```shell
./.venv/bin/python -m ia_repomap_builder prepare \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts

./.venv/bin/python -m ia_repomap_builder pr-context \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts \
  --base origin/main \
  [--output /safe/external/pr-context.json]
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

`result` preserves the existing adapter result shape, including raw XML. An
optional `--output` path receives the byte-identical JSON envelope and must be
outside the target checkout; an existing path is not overwritten. Exit code
`0` represents `ok`, `3` represents `unavailable`, and `2` represents invalid
input or an execution error. Unavailable results include actionable
remediation while preserving the underlying status and diagnostics. The clean
checked-out `HEAD` is the PR head and `--base` is an explicit local Git ref;
the interface does not accept a GitHub PR number or perform remote PR lookup.
Callers must select or create the desired PR checkout before invoking it. A
PR number such as `50176` is descriptive metadata only: a test for that PR must
use its exact checked-out head and intended base commit as the command inputs.

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
blast-radius conclusions. The root aggregate impact, affected tests, owners,
and co-change rows remain available only in canonical XML; symbol-scoped impact
is the separate on-demand expansion described below.

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

The result identity binds the query to the exact clean `HEAD` and prepared
engine/cache artifact. A candidate contains `path`, `name`, positive `line`,
optional `kind`, and `confidence="candidate"`. A missing or stale index is
`unavailable`; malformed input, XML, or invocation failures are `error`.

An absolute `root` attribute in retained Ripwire XML may reflect the worktree
used when the external cache was prepared. Normalized paths are repository-
relative and authoritative for this adapter; the XML absolute root is
non-authoritative metadata until cache path portability is addressed.

## Execution status

This record is append-only. Each completed slice adds a numbered row using the
same format; future work is not recorded as complete until its validation has
run. The entries describe this repository's implementation only. They do not
mean that a target `ia-app` checkout has been onboarded.

| Step | Status | Completed outcome | Validation |
| --- | --- | --- | --- |
| 1 | `complete` | Repository declaration and the `ia_repomap` contract are documented. | Contract, template, README, and repository guidance cross-reference the same schema. |
| 2 | `complete` | The Ripwire Intacct PHP-family extension patch is established, including `.map` only within `app/source`. | Patch identity and extension routing are covered by focused tests and the contract. |
| 3 | `complete` | External, revision-bound Ripwire index preparation is implemented. | Preparation verifies clean `HEAD`, staged cache files, and writes the manifest only after verification. |
| 4 | `complete` | Exact-head readiness and named-cache validation are implemented. | Readiness checks identity, base/merge-base resolution, and Ripwire `--doctor` cache use. |
| 5 | `complete` | PR-context invocation returns normalized changed-file and candidate-symbol seeds. | Focused tests cover Git status normalization, command construction, and XML symbol parsing. |
| 6 | `complete` | Explicit statuses, gaps, raw XML retention, and deterministic normalized evidence are implemented. | Tests cover unavailable/error paths, truncation and ambiguity gaps, raw XML, and repeatability; runtime metrics are excluded from deterministic comparisons. |
| 7 | `complete` | Lightweight validation and the P1/P2 regressions are complete. | The test suite passes with 40 tests (3 opt-in skips), including doctor-cache exit handling, and `git diff --check` passes. |
| 8 | `complete` | A clean local `ia-app` onboarding commit tracks the repository declaration and root agent guidance; review and merge remain pending. | The maintained declaration loads successfully, target files match their templates, and target commit `658face817ce6b474c481ad96bc42333ebf7dc05` is clean at its expected parent. |
| 9 | `complete` | Hunk-to-enclosing-symbol attribution narrows file-wide Ripwire definitions while retaining explicit fallback gaps and canonical XML. | Focused tests cover hunk parsing, inferred spans, boundary cases, missing lines, deletion/rename behavior, and Git failures. An isolated PR #50176 rerun reduced 14 candidates to `buildTemplateFilters` at line 126 and attributed all four hunks. |
| 10 | `complete` | A local module interface performs explicit index preparation and readiness-gated PR-context retrieval with JSON results and remediation. | CLI-focused tests cover command construction, status/exit mapping, output safety, raw JSON preservation, and the no-implicit-preparation boundary; the complete suite passes. |
| 11 | `complete` | Direct caller rows are normalized only for hunk-selected symbols as `direct-callers-v1`, with explicit malformed, out-of-scope, and truncation gaps. | Focused and complete suites pass; two isolated PR #50176 runs returned `buildTemplateFilters` with `validateSingleRequest` at line 30, `4 → 1` caller filtering, byte-identical XML, and deterministic normalized output. |
| 12 | `complete` | On-demand `symbol-impact-v1` expands one hunk-selected symbol through Ripwire's symbol-scoped impact query while retaining lower-bound and graph uncertainty evidence. | Focused and complete suites pass; two isolated PR #50176 runs returned `validateSingleRequest` at line 30 for `buildTemplateFilters`, with `defs=1`, `reaches=1`, `radius_tested=0`, `radius_untested=1`, byte-identical XML, and deterministic normalized output. |

## Acceptance criteria

The map is ready for PR context only when:

1. the checkout is clean and its exact `HEAD` is known;
2. the explicit base reference and merge base resolve;
3. the external manifest matches repository, revision, scope, configuration,
   engine, and parser/patch identity;
4. Ripwire `--doctor` reports the named lean cache as `source="cache-flag"`
   and `lean="ok"`; a cache that would self-heal to a cold parse is
   unavailable. An unrelated nonzero doctor exit remains a warning when this
   cache row is valid and does not make the named cache unavailable;
5. the patched Ripwire binary recognizes all configured Intacct extensions and
   does not regress ordinary `.php` or `.phtml` files;
6. repeated requests over identical clean inputs produce byte-identical raw XML
   and deterministic normalized evidence after runtime metrics such as
   `elapsed_ms` are excluded from comparison;
7. output remains within the configured token budget and discloses pagination
   or truncation;
8. source excerpts and generated artifacts remain outside tracked source.

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
- Generated artifacts are regenerable and keyed by revision and configuration.
- MCP, editor, harness, and installable public CLI integration are deferred; the
  local Python module interface is implemented.

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
