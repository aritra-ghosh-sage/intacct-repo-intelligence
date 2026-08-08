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
`deferred`.

An empty Git diff is invalid and returns `{"status":"blocked","error":{"code":"empty_diff"}}`.
Changed paths are exact repository-relative paths from Git; basename, same-name,
directory, symbol-name, and inferred cross-repository/entity mappings are not
used. Facts retain catalog record ID, source path, target revision, location
when present, evidence, and extractor identity. For a changed entity file,
OpenAPI links are traced through the exact entity occurrence to the linked
OpenAPI document. Ordering is deterministic.
