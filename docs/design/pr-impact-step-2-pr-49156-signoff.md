# PR 49156 Step 2 Sign-off Record

> Historical, target-specific acceptance artifact for PR 49156. The counts
> below describe the original run. The current exact-target repo-v1 resolver
> now resolves the seven formerly ambiguous qualified calls when each has one
> exact candidate; ambiguity remains fail-closed for duplicate candidates.

## Disposition

**Accepted as a partial, read-only Step 2 direct-surface audit.**

This record does not claim semantic completeness or no impact. Step 2 remains
an audit of generated Step 1 evidence. Relationship resolution is performed
by the repo-v1 build before Step 1; Step 2 reports the resulting
`entity_symbol_links` and relationship classifications without re-querying
SQLite.

## Exact-target preflight

- Repository: `ia-main`
- Source root: `/Users/aritra.ghosh/projects/main`
- Base: `e33954d66e7823303fa24087950d3306c547e0b7`
- Target: `f914d9892a51c1d34eadfd0e4da89f8418ed2c59`
- Changed source: `app/source/purchasing/POProcessTransactions.phtml`
- Catalog revision: exact target revision
- SQLite integrity: `ok`
- SQLite foreign-key check: `ok`
- Step 1 validator: `valid`
- Step 2 validator: `valid`

The operator evidence bundle used for this review is:

`/private/tmp/pr-49156.R8cJIr/`

| Artifact | SHA-256 |
| --- | --- |
| `catalog.db` | `23e8d02f9305bfe380edb69712b4d1c3ed57d215196d9a186865b61f232716c3` |
| `step1.json` file bytes | `a566da6b9802f7fb40d536e8489a91e9a8b564b714aa0c0274dc69d0cc46963c` |
| `step2.json` file bytes | `85ef77c46d286fd0b0196c45bef9db55096a893486bc8cb44e6cafa2efa626ff` |

The Step 2 report also records the canonical JSON hash of the generated Step
1 value:

`b35aa7daeff227238353d0a80a754a3889a74f8fb001dbd61604d0cca673a0b6`

## Direct-surface result

- Step 2 status: `partial`
- Audited surfaces: 21
- Covered surfaces: 2 (`files`, `symbols`)
- Total Step 1 facts represented in the summary: 37
- Deferred or review-required surfaces: 19

Empty surfaces retain the Step 1 warning that no direct rows are not proof of
no impact. Test facts remain deferred because automated-test discovery is out
of scope. `entity_occurrences` is unavailable for this changed non-`.ent`
path under the current direct-surface contract.

## Outgoing relationship review

The surface contains 24 catalog rows from `phase2_regex_mvp`:

- 17 rows are `project_resolved` with `target_symbol_id_present`:
  catalog record IDs `152864` through `152880`.
- The historical seven rows were `project_unresolved` with
  `ambiguous_project_symbol`; on the revalidated exact target they are
  `project_resolved` with `target_symbol_id_present` when the qualified target
  has one exact candidate.

The seven historical evidence strings were:

- `CsrfUtils::generateCsrfTokenInput(`
- `Session::getKey(`
- `I18N::getSingleToken(`
- `I18N::getTokensForArray(`
- `POSetupManager::isPriceConversionEnabled(`
- `QXCommon::isQuixote(`
- `IALayoutManager::prefersCSS(`

All 24 rows have `source_location: null`. This review confirms persisted
catalog classifications and provenance fields only. It does not infer a
callsite or select among duplicate candidates.

## Scope review

Confirmed absent from this Step 2 result and workflow:

- catalog build, promotion, cleanup, or mutation;
- heuristic symbol-to-entity ownership inference;
- MCP-specific tracing;
- automated-test discovery;
- downstream or multi-repository evidence;
- graph construction or queries;
- delta processing; and
- inferred semantic or transitive impact.

The bounded Step 2 acceptance decision is therefore: **sign off as a valid
partial direct-surface audit, with exact relationship resolution,
`entity_symbol_links` contract status, and all listed deferred/empty surfaces
retained as explicit evidence and follow-up limitations.**
