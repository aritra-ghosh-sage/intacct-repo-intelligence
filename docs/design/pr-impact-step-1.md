# PR Impact Step 1

Step 1 is a read-only, repo-v1-native direct-impact trace over a validated
Step 0 revision pair. It emits a separate JSON report and never edits the Step
0 YAML fixture.

## Contract

Inputs are exactly `--fixture`, `--manifest`, `--active-db`, and
`--repo-key`. `--manifest` defaults to `config/workspace_repos.yaml`; the
checkout root is resolved from the selected manifest entry. The fixture's `base_revision` and `target_revision` are
authoritative. Step 1 validates the exact Git diff between those revisions
before reading catalog facts. Git diff validation only; no catalog delta processing.

`catalog.delta.collect_changed_paths` may be reused only as the Git raw-diff/path-status
parser. It must not be used for catalog change-set processing, delta planning,
delta refresh, or delta builder execution.

The active database is opened with SQLite `mode=ro`, `query_only=ON`, and a
read transaction. Its repo-v1 schema, integrity, foreign keys, active build,
single `ia-main` repository, and target SHA are blocking preconditions. No
refresh, promotion, migration, graph, MCP, external checkout, or multi-repo
extraction is performed.

## Output

The report has schema version `0.1`, analysis kind `pr_impact_step_1`, and
top-level status `complete`, `partial`, or `blocked`. Direct surfaces use
`available`, `empty`, `unavailable`, `unresolved`, `ambiguous`, `stale`, or
`deferred`. `empty` means the repo-v1 table was queried and returned no direct
rows; it includes a warning and is never proof of no impact. Unsupported
database-consumer, permission, workflow, and test surfaces are explicitly
`unavailable`. External onboarding feasibility is manifest-only and
`deferred`; it does not downgrade the current repo-v1 report status.

`changed_files` is required and must be non-empty. Its exact path/status set
must equal the raw Git diff. Missing, empty, malformed, or mismatched fixture
paths block with `changed_path_mismatch`.

For supported surfaces, `stale` takes precedence over `ambiguous`, which takes
precedence over `unresolved`, which takes precedence over `available`. A fact
is stale when its catalog source revision is absent or differs from the fixture
target revision. Relationship facts are unresolved when their resolution class
is not `project_resolved`, and ambiguous when their resolution reason is
`ambiguous_project_symbol`.

`complete` requires all supported direct surfaces to be `available`. Expected
`unavailable` surfaces and deferred onboarding are excluded from the
top-level completeness calculation. `partial` is returned for supported
`empty`, `unresolved`, `ambiguous`, or `stale` surfaces.

A materialized `complete` report must contain exactly one direct trace for every
expected surface: `files`, `symbols`, `outgoing_relationships`,
`incoming_relationships`, `entity_occurrences`, `openapi_documents`,
`openapi_entity_links`, `rest_endpoints`, `actionui`, `actionui_artifacts`,
`actionui_fields`, `actionui_events`, `actionui_includes`, `nextgen`,
`nextgen_artifacts`, `source_diagnostics`, `database_consumers`, `permissions`,
`workflows`, and `tests`. The supported surfaces must be `available`; the
unsupported surfaces must be `unavailable`. Missing or unexpected direct
traces make the materialized report invalid.

Catalog preflight compares repo-v1 table, column, foreign-key, and index
contracts through SQLite PRAGMAs. CHECK constraints and partial-index
predicates, which PRAGMAs do not expose, are compared from normalized
`sqlite_master` definitions whenever either the expected or active table/index
contains the relevant constraint. SQLite internal tables and auto-generated
indexes are excluded.

An empty Git diff is invalid and returns `{"status":"blocked","error":{"code":"empty_diff"}}`.
Changed paths are exact repository-relative paths from Git; basename, same-name,
directory, symbol-name, and inferred cross-repository/entity mappings are not
used. Facts retain catalog record ID, source path, target revision, location
when present, evidence, and extractor identity. For a changed entity file,
OpenAPI links are traced through the exact entity occurrence to the linked
OpenAPI document. Ordering is deterministic.
