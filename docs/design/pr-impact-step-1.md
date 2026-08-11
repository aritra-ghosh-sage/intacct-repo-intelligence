# PR Impact Step 1

Step 1 is a read-only, repo-v1-native direct-impact trace over a validated
Step 0 revision pair. It emits a separate JSON report and never edits the Step
0 YAML fixture.

The current report schema is `0.3`. It can optionally consume a normalized PR
metadata JSON artifact produced by `scripts/intake_pr_metadata.py`; metadata is
context only and never overrides Git or SQLite evidence.

## P0 direct surfaces

`database_consumers` contains direct `dbschema` tables and fields, proven
entity table and field links, and database-relevant schema mappings.
`entity_metadata` contains direct `.ent` facts for `fieldinfo`, `schema`, `api`,
`dbfilters`, `children`, `nexus`, `ownedobjects`, `publish`, `importOrder`, and
other persisted entity sections. Step 0 database assertions remain contextual
and cannot satisfy either direct surface.

## Contract

Inputs are `--fixture`, `--manifest`, `--active-db`, `--repo-key`, and optional
`--metadata`. `--manifest` defaults to `config/workspace_repos.yaml`; the
checkout root is resolved from the selected manifest entry. The fixture's
`base_revision` and `target_revision` are authoritative. Step 1 validates the
exact Git diff between those revisions before reading catalog facts. Git diff
validation only; no catalog delta processing.

`catalog.delta.collect_changed_paths` may be reused only as the Git raw-diff/path-status
parser. It must not be used for catalog change-set processing, delta planning,
delta refresh, or delta builder execution.

The active database is opened with SQLite `mode=ro`, `query_only=ON`, and a
read transaction. Its repo-v1 schema, integrity, foreign keys, active build,
single `ia-main` repository, and target SHA are blocking preconditions. No
refresh, promotion, migration, graph, MCP, external checkout, or multi-repo
extraction is performed.

## Output

The report has schema version `0.3`, analysis kind `pr_impact_step_1`, and
top-level status `complete`, `partial`, or `blocked`. Direct surfaces use
`available`, `empty`, `unavailable`, `unresolved`, `ambiguous`, `stale`, or
`deferred`. `empty` means the repo-v1 table was queried and returned no direct
rows; it includes a warning and is never proof of no impact. Step 0 assertions
remain deferred context until direct target-revision catalog facts are read;
they cannot establish database evidence or completeness. Every direct fact
retains catalog record ID, source path, target revision, source location,
evidence, and extractor identity.

`changed_files` is required and must be non-empty. Its exact path/status set
must equal the raw Git diff. Missing, empty, malformed, or mismatched fixture
paths block with `changed_path_mismatch`.

For supported surfaces, `stale` takes precedence over `ambiguous`, which takes
precedence over `unresolved`, which takes precedence over `available`.
`complete` requires all supported direct surfaces to be `available`, and an
available database or entity metadata surface must contain direct catalog
facts. `partial` is returned for supported `empty`, `deferred`, `unresolved`,
`ambiguous`, or `stale` surfaces.

A materialized `complete` report must contain exactly one direct trace for every
expected surface, including `database_consumers` and `entity_metadata`, in
addition to the existing files, symbols, relationship, entity, OpenAPI, UI,
NextGen, diagnostics, permissions, workflow, and test surfaces. Missing or
unexpected direct traces make the materialized report invalid.

Facts are built from committed snapshot bytes only. Dynamic, unresolved, and
ambiguous source facts remain classified rather than inferred. `fieldinfo`
`fullname` values are metadata and never create database links.

## Target-revision isolated catalog

Build the target revision into an alternate active database. Do not use the
canonical `catalog/catalog.db` path:

```bash
PR_IMPACT_TMP=$(mktemp -d /private/tmp/repo-v1-pr-impact.XXXXXX)
ISOLATED_DB="$PR_IMPACT_TMP/catalog.db"

PYTHONPATH=. ./.venv/bin/python -m catalog.repo_v1 \
  --manifest config/workspace_repos.yaml \
  --target-sha <fixture-target-sha> \
  --active-db "$ISOLATED_DB" \
  --no-progress
```

The isolated database must record the fixture target SHA, pass SQLite
integrity and foreign-key checks, and leave the canonical database unchanged.

### Observed promotion evidence

The following post-change operator run confirms that the repo-v1 builder can
promote a 23,874-file catalog at commit
`776d1ffe49efb9189d022912e23aaef065bda1a6`:

```json
{"active_db":"/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db","build_token":"ebf4c59b6d214bc39c08ebf642900e77","file_count":23874,"promoted":true,"target_commit_sha":"776d1ffe49efb9189d022912e23aaef065bda1a6"}
```

This is successful repo-v1 promotion evidence, but it is not PR-target
evidence for the golden fixtures: PR 49156 targets
`f914d9892a51c1d34eadfd0e4da89f8418ed2c59`, and PR 48706 targets
`44ff9701e94a69c835063b4fd39e515ff0ae4680`. Because this run used the
canonical `catalog/catalog.db` path, it must not be used as the isolated
database for either PR analysis. A PR analysis requires a promoted database
at the fixture's exact target SHA under an alternate path.

## PR metadata intake

Fetch normalized metadata without changing the Step 0 YAML fixture:

```bash
./.venv/bin/python scripts/intake_pr_metadata.py \
  --manifest config/workspace_repos.yaml \
  --repo-key ia-main \
  --pr <number> \
  --output <metadata.json>
```

The intake prefers `gh api`, falls back to the GitHub HTTP API using
`GH_TOKEN` or `GITHUB_TOKEN`, and fails closed if neither provider is usable.
Step 1 accepts the artifact with `--metadata` and blocks when its repository,
revision pair, or changed paths disagree with the fixture and exact Git diff.
