# Phase 2D1 Remediation and Acceptance Boundaries

This document records the fail-closed quality constraints used by the current
catalog refresh. It is operational guidance; SQLite evidence and validators are
authoritative when prose and data disagree.

## Evidence boundary

- Compare base and target revisions with
  `git diff --raw -z -M --no-abbrev <base-sha> <target-sha> --`.
- Accept only regular `100644` and `100755` blobs. Reject symlinks, gitlinks,
  unsupported statuses or modes, malformed paths, missing objects, and
  wrong-type objects.
- Materialize target evidence from `git ls-tree -r -z -l` and
  `git cat-file --batch`. Mutable checkout bytes, filters, export attributes,
  ignored files, and untracked files are not extraction evidence.
- Preserve source paths, blob IDs, and stable diagnostic identities. Unknown
  source paths are null, never a fabricated placeholder.

## Builder boundary

- Exact delta is limited to scan, symbols, and relationships.
- Reset-style builders run in full only when a declared direct input or upstream
  evidence edge is invalidated.
- Gherkin coverage depends on scan and its exact manifest-configured feature,
  property, and object-mapping paths.
- Integration-link extraction is unsupported. Migration 025 removes legacy
  rows, and integrity validation rejects any reintroduced row.

## Repository and promotion boundary

- Repository scope is operator-controlled through explicit `depends_on`.
  Prerequisites are expanded dependency-first; reverse dependents are not.
- A main-only refresh that would rebuild REST endpoint evidence is rejected
  before candidate creation when enabled automation coverage depends on main.
- One closure produces one SQLite candidate and one atomic promotion.
- Promotion requires builder completion, blob/source verification, repository
  validation, `integrity_check`, `foreign_key_check`, migration-025 validation,
  semantic quality, manifest-root restoration, logical fingerprinting, final
  source-SHA verification, and parent-generation CAS.
- The refresh lock covers all terminal paths. Failed preparation or promotion
  preserves both active and previous SQLite generations.
- Ladybug graph construction and promotion are excluded.

## Semantic diagnostic boundary

Counts and structured diagnostics remain audit evidence. Parser diagnostics
are non-blocking when their file provenance is exact; semantic-resolution and
catalog-integrity diagnostics remain promotion-blocking.

Do not repair incompatibility by editing stored fingerprints or migration
markers. Use a supported full refresh, validate the candidate, and retain
operator-reviewed backups for rollback.
