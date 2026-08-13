# Repo-v1 PR-impact Session 4 sign-off

> Target-specific acceptance record for PR 49156. This document records a
> completed evidence run; current repo-v1 scope and open implementation gaps
> are maintained in [repo_v1_current_contract.md](repo_v1_current_contract.md).

## Decision and scope

Session 4 was executed for `intacct/ia-app` PR 49156 using the existing
repo-v1 Steps 0–3 contracts. The result is accepted as deterministic evidence
coverage for the exact target revision, with explicit partial and deferred
surfaces. It is not a claim of semantic business-impact completeness.

No semantic layer, schema, mapper, risk engine, graph, MCP integration, delta
processing, legacy refresh, cross-repository traversal, or source-code change
was added.

## Fixed inputs and isolated evidence

| Item | Value |
| --- | --- |
| PR | `intacct/ia-app#49156` |
| Base revision | `e33954d66e7823303fa24087950d3306c547e0b7` |
| Target revision | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` |
| Resolved source root | `/Users/aritra.ghosh/projects/main` |
| Repository key | `ia-main` |
| Evidence directory | `/private/tmp/repo-v1-pr-impact.uv6VeA` |
| Build token | `7aefdf0dfd134f42b73b09ea1af037eb` |
| Build id | `1` |
| Catalog file count | `23,874` |
| Catalog SHA-256 | `c87dba9b895d200a09e99076637899a7029aa4ec285a7a0160cb1868454fbe99` |

The catalog was built only with the exact target revision into the isolated
database. Its active repository row records `ia-main` and the exact target
SHA; `source_revisions_json` is `{"ia-main":"f914d9892a51c1d34eadfd0e4da89f8418ed2c59"}`.

External artifacts:

- `catalog.db`
- `step0-validation.json`
- `step1.json`
- `step2.json`
- `step3.json`
- `checksums.sha256`
- `commands.txt`

## Execution results

| Step | Report status | Validator | Result |
| --- | --- | --- | --- |
| 0 | `pass` | `validate_pr_impact_step0` | exit 0; no errors |
| 1 | `partial` | `validate_pr_impact_step1` | exit 0; `valid` |
| 2 | `partial` | `validate_pr_impact_step2` | exit 0; `valid` |
| 3 | `complete` | `validate_pr_impact_step3` | exit 0; `valid` |

Step 0 warnings are preserved, not promoted to errors. They identify the
fixture's unavailable permissions evidence, unknown related-repository
coverage, unresolved assessment items, and incomplete external review
references/revision freshness.

The isolated SQLite checks passed:

- exactly one active build;
- repository `ia-main`;
- catalog target revision equals the fixture target revision;
- `PRAGMA integrity_check` returned `ok`;
- `PRAGMA foreign_key_check` returned zero rows.

Report SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| `step0-validation.json` | `8e19b981b95abaacd309faca1d0cd460dec98928847d096b430227b991850b35` |
| `step1.json` | `4a1e38ca9af7b3e44047e31ff76a099720bd84356a8267f38ecdf4849574bc9b` |
| `step2.json` | `58178733c77fb0bad80d4e45a7a28a496a622db201bd12c476fd3a45e59eb6b7` |
| `step3.json` | `02e57dd6c1bbc9395f8167e3a5fefde9f072f0846f6cafe676474f0a84762ade` |

Step 2's `step1_report_sha256` is
`6eefef7cbb37d81a126919f8efa93de61f69d0e96fca1e818b602b2396584957`.
Recomputing the canonical JSON hash of the materialized `step1.json` produced
the same value.

## Step 1 and Step 2 surface audit

The following is the complete parity check between Step 1 direct traces and
Step 2 `surface_audit`.

| Surface | Step 1 status | Fact count | Step 2 disposition |
| --- | --- | ---: | --- |
| `files` | available | 1 | covered |
| `symbols` | available | 2 | covered |
| `outgoing_relationships` | available | 24 | covered |
| `incoming_relationships` | empty | 0 | defer_no_direct_rows |
| `entity_occurrences` | unavailable | 0 | not_modelled |
| `openapi_documents` | empty | 0 | defer_no_direct_rows |
| `openapi_entity_links` | empty | 0 | defer_no_direct_rows |
| `rest_endpoints` | empty | 0 | defer_no_direct_rows |
| `actionui` | empty | 0 | defer_no_direct_rows |
| `actionui_artifacts` | empty | 0 | defer_no_direct_rows |
| `actionui_fields` | empty | 0 | defer_no_direct_rows |
| `actionui_events` | empty | 0 | defer_no_direct_rows |
| `actionui_includes` | empty | 0 | defer_no_direct_rows |
| `nextgen` | empty | 0 | defer_no_direct_rows |
| `nextgen_artifacts` | empty | 0 | defer_no_direct_rows |
| `source_diagnostics` | empty | 0 | defer_no_direct_rows |
| `database_consumers` | empty | 0 | defer_no_direct_rows |
| `entity_metadata` | empty | 0 | defer_no_direct_rows |
| `permissions` | empty | 0 | defer_no_direct_rows |
| `workflows` | empty | 0 | defer_no_direct_rows |
| `tests` | deferred | 10 | defer_missing_target_evidence |

Step 1 reported 37 direct facts across 3 available surfaces and computed a
deterministic evidence-coverage score of 37/100. The score reflects evidence
availability and exact revision freshness; it is not a business-risk score.
Empty means no direct repo-v1 rows were found and never means no impact.

## Bounded manual spot-checks

1. Git diff and target blob

   The exact diff contains one modified file:
   `app/source/purchasing/POProcessTransactions.phtml`. At target line 1382,
   `GetFilterParams` assigns:

   ```php
   $_filterparams['ismcpenabled']   = IsMCPEnabled('po');
   ```

   The base blob assigns `Request::$r->_mcpEnabled` at the same line. The
   fixture assertion identifies the same path, method, parameter, and
   request-value-to-server-calculation change. The target blob SHA is
   `b0a33f1939bb57c65eb57d9481c845c21e75e2e9`.

2. Changed-file and first-symbol evidence

   Step 1's changed-file fact has catalog record/file id `21264`, the exact
   path, and target revision. SQLite row `files.id=21264` has the same path,
   blob SHA, size `61425`, and `source_commit_sha` equal to the target SHA.

   The first symbol ordered by catalog id is `GetFilterParams`, symbol id
   `144503`, function range 1376–1394. SQLite row `symbols.id=144503` matches
   the report's name, kind, file id, language, and declaration range. The
   second target symbol is `LookupAppPrecision`, id `144504`, range 1399–1404.

3. First outgoing relationship and unresolved check

   The first outgoing relationship ordered by catalog id is SQLite row
   `relationships.id=152864`: source symbol `144503` (`GetFilterParams`),
   target symbol `144451` (`POProcessTransactions`), relationship type `USES`,
   evidence `new POProcessTransactions(`, resolution class
   `project_resolved`, reason `target_symbol_id_present`, confidence `0.75`,
   and extractor `phase2_regex_mvp`. The Step 1 fact retains the same catalog
   id, path, evidence, resolution, extractor, and target revision.

   A target-symbol query for unresolved or ambiguous outgoing rows returned no
   rows. No unresolved or ambiguous relationship was relabeled or inferred.

4. Step 3 bounded incoming-call review

   Step 3 used `target_file_all_symbols` with `max_hops=2`. Its first seed is
   `GetFilterParams` id `144503`; the seed row records the target blob, file
   id `21264`, declaration range 1376–1394, and exact catalog/fixture target
   revisions. The second seed is id `144504`.

   No persisted incoming `CALLS` or `STATIC_CALLS` edge was traversed, and no
   skipped edge or skip reason was observed. Therefore there was no edge to
   inspect at a hop and no skipped-edge sample to verify. This is bounded
   caller evidence, not proof of no business impact.

5. Step 3 fixed boundary

   The report preserves:

   ```json
   "entity_context": {
     "status": "unavailable",
     "reason": "repo_v1_symbol_entity_mapping_not_modelled"
   },
   "business_impact": {
     "status": "deferred"
   }
   ```

## Systematic-gap register

All rows below refer to target revision
`f914d9892a51c1d34eadfd0e4da89f8418ed2c59`.

| Gap | Status | Evidence source | Target revision | Scope reason | Classification |
| --- | --- | --- | --- | --- | --- |
| Symbol-to-entity context | unavailable | `step3.json.entity_context`; gap `repo_v1_symbol_entity_mapping_not_modelled` | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | Current Step 3 contract does not model arbitrary symbol ownership | Intentional boundary |
| Business impact | deferred | `step3.json.business_impact` | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | Verified caller rows are code evidence, not semantic business mappings | Intentional boundary |
| Entity occurrences | unavailable; `not_modelled` | Step 1 `entity_occurrences`; Step 2 audit | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | This direct surface is not modeled for this trace | Intentional boundary; no catalog defect established |
| Incoming relationships | empty; `defer_no_direct_rows` | Step 1/2 surface audit, 0 facts | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | No direct target-revision rows matched the changed symbols | Intentional boundary; no catalog defect established |
| OpenAPI documents and entity links | empty; `defer_no_direct_rows` | Step 1/2 surface audit, 0 facts each | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | No direct rows matched this changed file/symbol set | Intentional boundary; no catalog defect established |
| REST endpoints | empty; `defer_no_direct_rows` | Step 1/2 surface audit, 0 facts | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | No direct repo-v1 endpoint row matched | Intentional boundary; no catalog defect established |
| ActionUI surfaces, artifacts, fields, events, and includes | empty; `defer_no_direct_rows` | Step 1/2 surface audit, 0 facts each | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | No direct rows matched | Intentional boundary; no catalog defect established |
| NextGen surfaces and artifacts | empty; `defer_no_direct_rows` | Step 1/2 surface audit, 0 facts each | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | No direct rows matched | Intentional boundary; no catalog defect established |
| Source diagnostics | empty; `defer_no_direct_rows` | Step 1/2 surface audit, 0 facts | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | No direct diagnostic row matched | Intentional boundary; no catalog defect established |
| Database consumers and entity metadata | empty; `defer_no_direct_rows` | Step 1/2 surface audit, 0 facts each | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | No direct target-revision database/entity metadata row matched | Intentional boundary; no catalog defect established |
| Permissions and workflows | empty; `defer_no_direct_rows` | Step 1/2 surface audit, 0 facts each | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | No direct row matched | Intentional boundary; no catalog defect established |
| Exact target-revision test evidence | deferred; `defer_missing_target_evidence` | Step 1/2 `tests`: 10 fixture-context facts, warning exact target test evidence unavailable | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | Automated-test discovery is outside this proving slice | Intentional boundary |
| Downstream repositories | deferred; always `[]` in this scope | Step 1 report and Step 2 deferred provenance | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | `ia-main` only; no downstream materialization | Intentional boundary |
| Graph and MCP analysis | deferred | Step 2 provenance | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | Explicitly excluded from Steps 0–3 | Intentional boundary |
| Delta processing and legacy workflow | deferred | Step 1/2 provenance and execution commands | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | Repo-v1 exact snapshot only | Intentional boundary |
| Cross-repository traversal | deferred | Step 2 provenance | `f914d9892a51c1d34eadfd0e4da89f8418ed2c59` | The run is restricted to `ia-main` | Intentional boundary |

No parser-failed, missing-source, unresolved, ambiguous, stale, or skipped-edge
case was observed in this run. Those statuses remain valid contract states and
would require documentation if a future target produces them; they were not
invented for this sign-off.

## Verification gate

The required focused test command passed:

```text
104 passed in 33.62s
```

`git diff --check` and final worktree status are part of the acceptance check.
The intended final worktree contains only this document as the approved
repository change. The catalog, reports, checksums, and command log remain
outside the repository under `/private/tmp/repo-v1-pr-impact.uv6VeA`.

## Confidence and recommendation

Confidence in the execution evidence is high: the exact target build,
revision, SQLite integrity, report validators, canonical Step 1 hash, Git
diff, target blob, and focused tests all agree. The Step 1 deterministic
evidence-coverage score is 37/100 because only 3 of 21 direct surfaces are
available; it must not be interpreted as a risk or business-impact score.

Recommendation: sign off this run as a valid, revision-pinned deterministic
evidence-coverage artifact with a `partial` direct-surface result. Retain the
documented gaps and require human or later bounded analysis for semantic
business impact, entity ownership, downstream tests, and any empty surface.
Do not relabel this result as complete business-impact analysis or no impact.
