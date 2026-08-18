# PR Impact Step 1

Step 1 is a read-only, repo-v1-native direct-impact trace over a validated
Step 0 revision pair. It emits a separate JSON report and never edits the Step
0 YAML fixture.

The current report schema is `0.5`. It can optionally consume a normalized PR
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
checkout root is resolved from the selected `ia-main` manifest entry. The fixture's
`base_revision` and `target_revision` are authoritative. Step 1 validates the
exact Git diff between those revisions before reading catalog facts. Git diff
validation only; no catalog delta processing.

`catalog.delta.collect_changed_paths` may be reused only as the Git raw-diff/path-status
parser. It must not be used for catalog change-set processing, delta planning,
delta refresh, or delta builder execution.

The active database is opened with SQLite `mode=ro`, `query_only=ON`, and a
read transaction. Its repo-v1 schema, integrity, foreign keys, active build,
single `ia-main` repository, and exact target revision are blocking
preconditions. The catalog revision must equal the fixture target revision.
Older, forward, and diverged catalog revisions block with
`catalog_revision_mismatch`. No refresh, promotion, migration, graph, MCP,
external checkout, or multi-repo extraction is performed.

## Output

The report has schema version `0.5`, analysis kind `pr_impact_step_1`, and
top-level status `complete`, `partial`, or `blocked`. The analysis scope is
direct `ia-main` evidence only. Direct surfaces use
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
ambiguous source facts remain classified rather than inferred. The report
retains both the fixture target revision and the catalog revision, with an
exact `revision_relation` and the compatibility evidence used by preflight.
`fieldinfo` `fullname` values are metadata and never create database links.

The schema-compatible `downstream_repositories` section is always an empty
list in this `ia-main`-only scope. Manifest contracts, dependency ordering,
and Step 0 candidate labels do not become downstream impact evidence.

Relationship surfaces retain the exact persisted relationship ID, source and
target symbol IDs/names/kinds, relationship type, confidence, resolution class
and reason, extractor, and the existing source/evidence/revision provenance.
The outgoing-relationship surface also includes deterministic `resolution_counts`
grouped by `(resolution_class, resolution_reason)`; no fallback identity is
introduced. OpenAPI diagnostic facts retain their diagnostic ID/key, code,
severity, phase, message, source path/pointer, raw evidence, extractor, and
source revisions. Their `classification` is `expected`, `actionable`, or
`unclassified`; only the exact history-file pattern already known to omit
`x-mappedTo` is classified as expected. Known invalid, zero-match, and
multiple-match mapping diagnostics are actionable; all other cases remain
unclassified.

The stable report-level `confidence` object is either `not_computed` with a
null score for blocked analysis, or `computed` with an integer score from 0 to
100 and components for direct evidence availability, exact revision freshness,
and direct unresolved gaps. The current deterministic weighting is 50%, 30%,
and 20%, respectively. The downstream contribution is always zero/non-
applicable for schema compatibility. Confidence does not convert deferred or
missing evidence into a positive fact.

## Review Markdown

The existing Step 1 report can be rendered as review Markdown with
`catalog.pr_impact_step1.render_review_markdown(report)`. The renderer uses the
heading and section order from `docs/review/pr-review-template.md` exactly and
does not add facts that are absent from the report. Review metadata not modeled
by Step 1 uses `Not available`, `Unknown`, or `Not computed`; API, database,
and UI coverage is not inferred from filenames or surface names.

The CLI accepts stdout-only `--markdown`, mutually exclusive with `--json`:

```bash
./.venv/bin/python -m scripts.trace_pr_impact_step1 \
  --fixture <step0-fixture.yaml> \
  --manifest config/workspace_repos.yaml \
  --active-db <target-revision-catalog.db> \
  --repo-key ia-main \
  --markdown
```

JSON remains the default and is unchanged when `--json` is selected. Markdown
recommendations are `Request Changes` for a blocked report, `Comment` when the
report contains warnings or gaps, and `Approve` only for a clean complete
report. Direct facts retain their source path, target revision, source
location, catalog identity, extractor, and evidence in the reviewed table.

## Exact target-revision isolated catalog

Build the exact fixture target revision into an alternate active database. Step
1 never modifies the canonical database:

```bash
PR_IMPACT_TMP=$(mktemp -d /private/tmp/repo-v1-pr-impact.XXXXXX)
ISOLATED_DB="$PR_IMPACT_TMP/catalog.db"

PYTHONPATH=. ./.venv/bin/python -m catalog.repo_v1 \
  --manifest config/workspace_repos.yaml \
  --target-sha <fixture-target-sha> \
  --active-db "$ISOLATED_DB" \
  --no-progress
```

The isolated database must record its own exact catalog revision, pass SQLite
integrity and foreign-key checks, and pass the Step 1 exact-revision preflight.

### Observed promotion evidence

The following post-change operator run confirms that the repo-v1 builder can
promote a 23,874-file catalog at commit
`776d1ffe49efb9189d022912e23aaef065bda1a6`:

```json
{"active_db":"/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db","build_token":"ebf4c59b6d214bc39c08ebf642900e77","file_count":23874,"promoted":true,"target_commit_sha":"776d1ffe49efb9189d022912e23aaef065bda1a6"}
```

This is successful repo-v1 promotion evidence, but it is not automatically
PR-target evidence for the golden fixtures: PR 49156 targets
`f914d9892a51c1d34eadfd0e4da89f8418ed2c59`, and PR 48706 targets
`44ff9701e94a69c835063b4fd39e515ff0ae4680`. Because this run used the
canonical `catalog/catalog.db` path, it must pass the exact-revision preflight
before it can be used for either PR analysis. A PR analysis must retain the
exact fixture base-to-target Git diff and exact catalog-target relation.

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

The current metadata artifact is schema `0.2`. In addition to the existing PR,
file, review, comment, and check-run records, it captures explicit linked issue
cross-references, Actions workflow runs, workflow jobs, deterministic
collection status, and an evidence fingerprint. Workflow and check evidence
must target the exact PR head SHA. Schema `0.1` artifacts remain readable but
do not provide the federation collections or fingerprint guarantee; see
[PR Impact Step 1 Federation Artifact](pr-impact-step-1-federation.md).
