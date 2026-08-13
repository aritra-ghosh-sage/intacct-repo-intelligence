# Repo-v1 Current Contract

**Status:** current operating contract
**Branch:** `repo-v1`
**Implementation reference:** `4346ea3` (`Step 4 PR sign off`)
**Last reviewed:** 2026-08-13

This document is the short current-status reference for repo-v1. Phase
closure records, PR-specific sign-offs, and validation reports retain their
historical evidence and must not be treated as current status unless their
target revision and date are explicitly checked.

## Objective

Provide deterministic, source-grounded PR impact and test-gap analysis:

```text
PR or changed files
    -> exact repository and source revisions
    -> changed symbols, entities, APIs, UI, permissions, and data
    -> bounded direct and incoming impact
    -> reviewed cross-repository contracts
    -> existing tests and coverage
    -> impact, obligations, gaps, diagnostics, and confidence
```

The first proving slice is `ia-main` only. Cross-repository results are empty
or deferred until an explicit reviewed contract and target-revision evidence
are available.

## Current repo-v1 substrate

Repo-v1 builds committed Git tree/blob bytes into an isolated SQLite
candidate, validates provenance, ownership, integrity, and foreign keys, and
atomically promotes only a valid candidate. It is full-rebuild-only. It does
not require the legacy refresh path, delta processing, Ladybug, or MCP.

The accepted fact families include:

- files, symbols, relationships, and parser diagnostics;
- entity occurrences and entity metadata;
- OpenAPI documents, entity links, and REST endpoints;
- ActionUI and NextGen UI facts;
- workflow and security facts;
- database tables, fields, entity metadata, and direct database links.

## Current PR-impact slices

- **Step 0:** exact PR/revision fixture and review context.
- **Step 1:** read-only direct `ia-main` tracing over an exact target-revision
  SQLite catalog; report schema `0.4`.
- **Step 2:** read-only availability audit over the Step 1 report; report
  schema `0.1`; it does not re-query SQLite or infer impact.
- **Step 3:** read-only incoming `CALLS`/`STATIC_CALLS` traversal for one or
  two hops; report schema `0.1`.
- **Step 4:** current sign-off documentation, not a separate analysis engine.

Steps 1–3 remain separately testable, but the intended product output is one
composed review containing direct facts, bounded callers, entity context,
test evidence, downstream obligations, and explicit gaps.

## Immediate gaps

These are the next repo-v1 implementation slices:

1. Add an explicit, provenance-backed `symbol -> entity occurrence` mapping.
   Same-file, same-name, filename, module, and semantic similarity do not
   establish ownership.
2. Improve Step 3 seed precision by mapping changed diff hunks to symbol
   declaration ranges, with an explicit file-level fallback.
3. Compose Steps 1–3 into one deterministic PR-review result.

## Deferred follow-up

- repository-local test discovery and target-revision coverage;
- reviewed cross-repository contracts and downstream test obligations;
- API, workflow, UI, permission, and data propagation beyond direct facts;
- broader semantic business-impact interpretation;
- graph/Ladybug, MCP compatibility, delta refresh, and legacy orchestration.

## Evidence rules

- Read source evidence from a known committed Git revision.
- Preserve repository, build, commit, file, symbol, source-location, hash, and
  extractor provenance.
- Preserve unresolved, ambiguous, dynamic, stale, unsupported, and unavailable
  states; do not turn missing evidence into no impact.
- Use explicit typed mappings for cross-repository relationships.
- Treat historical counts, hashes, commits, and test totals as historical
  evidence unless freshly rerun.

## Authoritative references

- [Full-rebuild design and KISS/YAGNI boundary](kiss_full_rebuild_plan.md)
- [Repo-v1 phase closure evidence](repo_v1_phase_closure.md)
- [PR Impact Step 0](pr-impact-step-0.md)
- [PR Impact Step 1](pr-impact-step-1.md)
- [PR Impact Step 2](pr-impact-step-2.md)
- [PR Impact Step 3](pr-impact-step-3.md)
- [PR review template](../review/pr-review-template.md)
