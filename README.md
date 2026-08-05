# Intacct Repository Intelligence

## Overview

Intacct Repository Intelligence is a code intelligence and knowledge graph platform for the Sage Intacct codebase.

The project transforms source code, metadata, configuration assets, and extracted relationships into a structured, queryable representation of the system.

Its purpose is to help engineers and AI systems understand:

- Business entities
- Code structure
- Cross-module dependencies
- APIs
- Configuration artifacts
- Workflows
- Impact relationships

without relying on tribal knowledge or manual repository exploration.

The repository is designed to provide an evidence-based understanding of the Intacct codebase. Every fact in the graph should be traceable back to source artifacts.

---

## Why This Exists

The Intacct codebase contains a large amount of business logic spread across multiple technologies and repositories.

Examples include:

- PHP
- JavaScript
- SQL
- XML
- XSLT
- OpenAPI specifications
- YAML configurations
- Entity definition files
- Domain service repositories

Understanding a business concept such as:

- APBill
- Vendor
- Customer
- GLBatch

typically requires navigating many files, modules, and technologies.

Traditional approaches depend on:

- tribal knowledge
- manual searches
- knowledge transfer
- historical context

These approaches do not scale.

This project creates a machine-readable representation of the codebase that can be queried by both humans and AI systems.

---

## Repository Goals

The project has three goals, in priority order.

### Goal 1: Build a Searchable Code Catalog

Create a complete inventory of repository assets.

Examples:

- files
- symbols
- relationships
- metadata
- entity mappings

---

### Goal 2: Build a Knowledge Graph

Transform extracted information into a graph containing:

- business entities
- code symbols
- dependencies
- workflows
- integrations
- APIs
- configuration artifacts

The graph becomes the primary navigation layer over the codebase.

---

### Goal 3: Enable AI-Assisted Understanding

Allow AI systems to answer questions such as:

- What impacts APBill?
- Where is Vendor approval implemented?
- Which APIs expose this entity?
- What modules consume this workflow?
- What breaks if this field changes?

using evidence instead of assumptions.

---

## Core Philosophy

### Evidence First

Every node, edge, mapping, or relationship should answer:

> Where did this come from?

If provenance cannot be established, the graph should not contain it.

---

### Deterministic Over Heuristic

Prefer extraction techniques that produce repeatable results.

Avoid relying solely on assumptions, naming conventions, or inferred file locations.

---

### Precision Before Recall

A missing relationship is preferable to an incorrect relationship.

The graph should prioritize correctness over completeness.

Recall can be improved incrementally.

Incorrect data damages trust.

---

## Sources of Truth

The graph is assembled from multiple verified sources.

No single source is authoritative for the entire system.

Current sources include:

- Entity definition files (`*.ent`)
- PHP source code
- JavaScript source code
- SQL
- XML
- XSLT
- OpenAPI specifications
- YAML configuration files
- Extracted code relationships

Entities may derive information from multiple sources simultaneously.

Contributors should avoid assuming that any single artifact fully describes an entity.

---

## High-Level Architecture

```text
Repository Sources
        │
        ▼
 File Discovery
        │
        ▼
   File Catalog
        │
        ▼
Symbol Extraction
        │
        ▼
Relationship Extraction
        │
        ▼
 Entity Construction
        │
        ▼
 Configuration Mapping
        │
        ▼
 Knowledge Graph
        │
        ▼
 Query Layer
        │
        ▼
 Human & AI Consumers
```

---

 ## Storage Architecture

The project uses two complementary storage systems.

### SQLite

SQLite is the authoritative source of extracted facts.

It stores files, symbols, relationships, entity mappings, provenance
information, and validation metrics.

SQLite is optimized for deterministic extraction, validation,
auditing, and reproducibility.

### Ladybug

Ladybug is the rebuildable graph traversal projection.

Data from SQLite is projected into Ladybug to support graph traversal,
dependency analysis, impact analysis, and AI-assisted navigation.

### Design Principle

SQLite answers:

"What facts do we know?"

Ladybug answers:

"How are those facts connected?"

AI systems should use Ladybug for traversal and SQLite for evidence and
provenance.

### Graph Projection Workflow

The rebuild flow captures a consistent SQLite backup, builds a uniquely named
candidate Ladybug graph from that snapshot, and validates every projected node,
directed edge, and property against the same snapshot. Only a validated candidate
is atomically promoted. Build or validation failures leave the active graph
available, and successful promotion retains the prior graph as
`catalog/graph.lbug.previous`.

Apply the catalog migrations once to an existing catalog before the first safe
delta refresh or generation-linked graph rebuild:

```bash
./.venv/bin/python -c "from catalog.db import migrate_multi_repo; migrate_multi_repo(db_path='catalog/catalog.db', local_root='/Users/aritra.ghosh/projects/main')"
```

Fresh catalogs initialized through `catalog.db.init_db()` already contain the
post-025 schema. Full Ladybug construction is a lengthy operator-run workflow and must
not be started during an agentic session. Agents should restrict themselves to
read-only parity validation and focused unit tests. SQLite query commands remain
the authoritative evidence path; Ladybug is a rebuildable traversal projection.

`graph_ready_entities` is an advisory triage view for entities with strong root
evidence. It is derived from `entity_nodes` identity plus `entity_roots`
strength signals. It intentionally excludes weak or unrooted entities and must
not be used to filter authoritative queries or the complete graph projection.

### Query JSON Contract v1

This is the canonical machine-readable contract for JSON mode across all
`scripts/query_*.py` commands.

Versioning:

- `contract_version: 1`
- Backward-compatible additions are allowed.
- Breaking field or semantics changes require a new version.

Top-level envelope (required):

- `contract_version`: integer
- `query`: object
- `status`: `ok` or `error`
- `data`: object
- `summary`: object
- `error`: null or object

`query` object:

- `command`: command name (for example `entity`, `stats`, `deps`)
- `args`: normalized command arguments

`error` object:

- `code`: stable machine code
- `message`: human-readable error summary
- `details`: structured context object

Rules:

- Use `snake_case` field names.
- Use `null` for missing scalar values.
- Use empty arrays/objects instead of omitting expected collection fields.
- Keep deterministic ordering.
- JSON mode must not include captured text reports.
- Text mode remains unchanged for human-readable CLI output.

Strict cutover:

- JSON output follows this contract only.
- Legacy ad-hoc JSON roots such as `exit_code`, `report`, or plain string
       `error` values are not part of v1.

Command-specific data schemas:

#### query_entity.py

`entity` command (default):

- `data.entity`: `id`, `name`
- `data.mapped_symbols`: `symbol_id`, `name`, `kind`, `mapping_type`,
       `confidence`, `source_text`, `file_id`
- `summary`: `mapped_symbol_count`, `mapping_type_counts`

`entity --workflow`:

- `data.entity`
- `data.workflows_by_type`: `workflow_id`, `name`, `workflow_type`,
       `source_kind`, `source_file`
- `summary`: `workflow_count`, `workflow_type_counts`

`entity --flow`:

- `data.entity`
- `data.core_roots`: `symbol_id`, `name`, `kind`, `role`, `weight`, `reason`
- `data.db_schema_tables`: `table_name`, `primary_keys`, `field_count`,
       `fields[]` (`field_name`, `field_type`)
- `data.workflows_by_type`
- `summary`: `core_root_count`, `db_table_count`, `workflow_count`

`entity --openapispec`:

- `data.entity`
- `data.openapi_mappings`: `mapping_type`, `source_text`, `file_id`
- `summary`: `openapi_mapping_count`, `mapping_type_counts`

`entity --access`:

- `data.entity`
- `data.access_links`: `surface`, `record_id`, `link_type`, `label`,
       `source_file`, `evidence_file`, `notes`
- `data.dbschema_fields_by_record_id`: `field_name`, `field_type`
- `summary`: `access_link_count`, `surface_counts`, `link_type_counts`

`root-symbols` command:

- `data.entity`
- `data.roots`: `symbol_id`, `name`, `kind`, `role`, `weight`, `reason`
- `summary`: `root_count`, `min_weight`

`direct-impact` command:

- `data.entity`
- `data.seed_symbols`: `symbol_id`, `name`, `kind`, `seed_type`,
       `mapping_type`, `confidence`, `role`, `weight`
- `data.symbol_impacts`: `seed_symbol_id`, `outgoing[]`, `incoming[]`,
       `related_files[]`
- `summary`: `seed_count`, `outgoing_edge_count`, `incoming_edge_count`,
       `related_file_count`

`impact` command:

- `data.entity`
- `data.traversal.nodes`: `symbol_id`, `name`, `kind`, `depth`, `is_seed`
- `data.traversal.edges`: `from_symbol_id`, `to_symbol_id`,
       `relationship_type`, `direction`, `confidence`, `file_path`
- `summary`: `node_count`, `edge_count`, `by_kind`, `by_depth`

`risk` command:

- `data.entity`
- `data.metrics`: `seed_count`, `discovered_count`, `incoming_count`,
       `outgoing_count`
- `data.top_expansion_points`: `symbol_name`, `count`
- `summary`: `seed_count`, `discovered_count`, `incoming_count`,
       `outgoing_count`

#### query_graph.py interface

The graph commands use the same JSON v1 envelope above. They open the
Ladybug projection read-only, keep result ordering deterministic, emit text
reports by default, and emit JSON only with `--json`. A graph failure is an
explicit error response, never an empty successful result.

`file-impact <file_path>`:

- `data.file` identifies the catalog file.
- `data.seed_symbols` contains symbols declared in that file.
- `data.direct_entities` contains exact entity mappings from those symbols.
- `data.traversal.nodes` and `data.traversal.edges` contain downstream evidence
  reached through incoming dependency edges.
- `data.affected_entities` and `data.surfaces` contain only exact mappings
  reached by the traversal; the command does not infer entity ownership from a
  file name.
- Default traversal is depth `1` with at most `25` edges per symbol.

Example: if `PricingHelper::calculate()` is changed and `InvoiceService` calls
it, `InvoiceService` is a downstream impact. A dependency that
`PricingHelper::calculate()` itself calls is not labelled as an impacted caller.

`who-uses [symbol_name] --symbol-id <id>`:

- A unique name resolves to one target symbol.
- A non-unique name returns deterministic candidates with symbol ID, kind, file,
  and line range; callers then retry with `--symbol-id`.
- `data.target`, `data.callers`, and `data.referencers` preserve directed
  evidence. If `A` calls `B`, querying `B` reports `A` as a caller.

`entity-context <entity_name>` returns the entity, exact mapped symbols,
evidence-backed surfaces, and matching database schema records.

`security-surface <entity_name>` returns exact security-operation, policy, and
menu links for the entity.

Graph-specific errors use stable codes including `ambiguous_symbol`,
`symbol_not_found`, `entity_not_found`, `file_not_found`, and `graph_query_failed`.
In JSON mode, ambiguity candidates are returned in `error.details.candidates`.

### Multi-repository workspaces

The catalog can index multiple repositories through
[`config/workspace_repos.yaml`](config/workspace_repos.yaml). Each repository
has one explicitly tracked branch and an explicit builder list; repository
ordering is declared with `depends_on` when needed and expanded by
`scripts/refresh_workspace.py`.

Use `depends_on: null` for repositories with no prerequisite repos. Use an
explicit list when a repository must refresh another repository first.

Manifest loading is atomic and strict. Unknown fields, missing required fields,
invalid field types, unsupported profiles or builders, and invalid dependency
graphs reject the entire manifest before registration or refresh. The required
repository fields are `repo_key`, `local_root`, and `tracked_branch`;
`enabled` defaults to `true`, `profile` defaults to `generic`, `builders`
defaults to an empty list, and `depends_on` defaults to `null`. Refresh also
preflights every checkout in the dependency closure, including its root,
configured branch, clean state, and any REST automation evidence paths, before
building the first candidate.

#### Archiving a repository

An archived repository remains in `repos` as lifecycle metadata, but it is not
an evidence source. Its scan, extract, inference, refresh, and
repository-qualified query paths reject the request. Archiving is a
candidate-only SQLite operation: it removes evidence owned by the requested
repository, preserves active-repository evidence or aborts, and leaves the
Ladybug projection stale until an operator runs a full graph rebuild and graph
validation.

For an operator-confirmed archive, use the manual source (no provider or
checkout access is required):

```bash
./.venv/bin/python scripts/archive_repository.py --db catalog/catalog.db \
  archive <repo-key> --source manual --reason "repository archived"
```

For a GitHub-confirmed archive, use `--source github`. The command verifies the
configured GitHub origin, performs a targeted fetch, and requires GitHub to
return the literal archived state before it changes the catalog. `status
<repo-key>` is read-only and reports lifecycle state and target-owned evidence
counts.

`scripts/refresh.sh` remains as a compatibility entry point. It initializes a
missing catalog, applies the workspace migration, and refreshes `ia-main` by
default; it does not delete the active catalog or build a graph:

```bash
bash scripts/refresh.sh
```

For automation and every additional repository, use the explicit workspace
command below. Migrate an existing single-repository catalog once, then
register its manifest:

```bash
./.venv/bin/python -c "from catalog.db import migrate_multi_repo; migrate_multi_repo(local_root='/path/to/main')"
./.venv/bin/python scripts/catalog_repos.py --db catalog/catalog.db register --manifest config/workspace_repos.yaml
```

Refresh one repository dependency closure with a clean, committed checkout.
The effective source root from `config.py` for `ia-main` is
`/Users/aritra.ghosh/projects/main`:

```bash
./.venv/bin/python scripts/refresh_workspace.py \
  --db catalog/catalog.db \
  --manifest config/workspace_repos.yaml \
  --repo ia-main \
  --mode auto
```

`--mode full` runs every selected builder and does not require an incremental
base. `--mode auto` uses exact committed-SHA deltas when the contract can be
proven and falls back to repository-scoped full builders with a recorded reason.
`--mode delta` fails before candidate construction when any repository lacks a
safe base. Checkouts must be clean, attached to the configured branch, and at a
committed revision; the indexed base must exist and be an ancestor of `HEAD`.

Change collection is byte-safe and commit-exact. Refresh runs exactly:

```bash
git diff --raw -z -M --no-abbrev <base-sha> <target-sha> --
```

The raw records retain both rename paths, modes, object IDs, and rename scores.
Only regular `100644` and `100755` blobs are accepted. Symlinks, gitlinks,
unsupported statuses or modes, malformed paths, missing objects, and non-blob
objects fail closed. Changed paths are retained for planning even when they are
outside generic file-scan scope.

Builders never read evidence from mutable checkout bytes. Each repository with
work to run is materialized from the exact target tree using `git ls-tree` and
`git cat-file --batch`; raw blob bytes and executable modes are verified before
builders run. Working-tree changes, ignored files, clean/smudge filters, and
export attributes therefore cannot alter a candidate. Whole-tree snapshots are
intentional in contract v3; partial-tree snapshot optimization is deferred.

Delta compatibility is repository-scoped. Each successful repository run stores
a hash of its normalized manifest entry (excluding operator-local `local_root`)
and its expanded builder plan. Catalog-build manifest and plan hashes are audit
summaries for the complete input manifest and selected closure; they are not
used to certify a later repository delta. This permits independent repository
closures to be refreshed in alternating order without false incompatibility.

Contract v3 fingerprints the evidence-affecting Python runtime and catalog,
parser, migration, scan, link, builder, and integrity-validation sources. A
contract-v2 generation, null fingerprint, or incompatible runtime/manifest/plan
cannot authorize a v3 delta: `auto` records the reason and runs full, while
forced `delta` fails before candidate creation. Git diff, object validation,
and path parsing failures follow the same fallback rule. Never edit stored
fingerprints to bypass this check; recover with a supported full refresh.

Exact delta execution is limited to scan, symbols, and relationships. Scan
touches only changed paths, symbols touch only affected file IDs, and
relationships include the direct files plus symbol-dependent closure. Entities,
entity roots, OpenAPI, workflows, security, REST endpoints, entity semantics,
entity-access links, and Gherkin coverage are reset-style builders: when their
declared source inputs or upstream evidence are invalidated, they run in full.
Unsupported integration-link extraction is rejected, and migration 025 removes
legacy integration-link rows.

The complete dependency closure is built dependency-first in one SQLite
candidate and promoted once. A later repository or validation failure cannot
partially promote an earlier dependency. A closure whose target revisions are
identical to the indexed revisions records a no-op attempt without creating a
catalog generation or invalidating the graph. If a revision advances through
only out-of-scope files, refresh creates a metadata-only delta generation so
repository provenance advances; evidence builders remain skipped and the graph
is marked stale because the projected repository revision changed.

Repository scope remains operator-controlled. Requesting `ia-main` selects only
`ia-main`; requesting `ia-restapi-automation` selects `ia-main` first and then
the automation repository through its explicit `depends_on`. Reverse dependents
are never inferred from Git ancestry, remotes, profiles, or manifest order. A
main-only refresh is blocked before candidate creation when its REST endpoint
stage would run while enabled automation coverage is out of scope. Use the
explicit safe closure only when the automation repository is enabled, has an
extractable lifecycle state, and its configured checkout passes preflight:

```bash
PYTHONPATH=. ./.venv/bin/python -m scripts.refresh_workspace \
  --db catalog/catalog.db \
  --manifest config/workspace_repos.yaml \
  --repo ia-restapi-automation \
  --mode full
```

Refresh holds an exclusive `catalog/catalog.db.refresh.lock` for the complete
operation. Immediately before no-op history or promotion it rechecks every
source SHA and compares the active parent build ID, token, content fingerprint,
source revisions, and database identity. A lock or parent/source compare-and-swap
failure cannot replace either active or previous catalog artifacts.

`catalog_builds` retains the full logical SQLite generation history, while
`graph_builds` records the separately projected Ladybug generations. A
successful promotion retains the current and immediately previous physical
artifacts as `catalog.db`, `catalog.db.previous`, `graph.lbug`, and
`graph.lbug.previous`; older metadata rows remain in SQLite. Failed candidate
or recovery artifacts may remain separately and must not be treated as active
generations. SQLite promotion marks the prior graph generation stale.
Graph-backed queries remain explicitly unavailable/stale until a graph build
linked to the active catalog fingerprint is promoted.

Ladybug construction and promotion are deliberately excluded from this refresh
workflow. A SQLite promotion may mark an older graph generation stale, but this
command never builds or promotes `graph.lbug`. Full graph construction remains
a separate, lengthy operator-run workflow outside agentic refresh sessions.

Every SQLite candidate must pass physical integrity, foreign-key, logical
ownership-orphan, generation-state, repository-revision, stable-symbol-key, and
logical-fingerprint checks before promotion. Contract v3 additionally verifies
target blobs, migration 025, absence of integration rows, source SHAs, semantic
quality counts, stable diagnostic keys, and parent CAS in that order. The active
catalog can be audited read-only at any time:

```bash
PYTHONPATH=. ./.venv/bin/python validation/validate_catalog_integrity.py \
  --db catalog/catalog.db
```

Parser failures are retained as source-backed UI diagnostics and do not by
themselves block SQLite promotion. A malformed ActionUI form preserves its
last-known-good UI evidence when available; parser coverage loss is reported as
a warning. Refresh-time parser details are written to per-repository files in
the candidate output root (`<repo>/ui_parser_failures.jsonl`, with
corresponding JavaScript and YAML failure logs). Semantic resolution errors
and catalog integrity failures remain promotion-blocking.

Refresh has no baseline approval phase. Structured counts and diagnostics are
audit evidence; SQLite integrity, exact source/provenance validation, semantic
resolution, final source checks, and parent CAS remain promotion gates.

For the current workspace recovery path, use `ia-main`. The checked-in manifest
disables `ia-restapi-automation`, and an archived repository is not an
admissible refresh source.

REST automation coverage is a repository-scoped candidate builder. With the
an enabled, active automation repository and its manifest dependency in place,
refreshing `ia-restapi-automation` will refresh `ia-main` first so its versioned
REST endpoint and entity evidence is present. The checked-in workspace manifest
currently disables `ia-restapi-automation`, and the catalog may retain it as an
archived lifecycle record; such a repository cannot be refreshed until an
operator explicitly reactivates it:

```bash
./.venv/bin/python -m scripts.refresh_workspace \
  --db catalog/catalog.db \
  --manifest config/workspace_repos.yaml \
  --repo ia-restapi-automation \
  --mode full
```

The suite reads only `.feature`, same-stem `.properties`, and the manifest
configured `object-mapping.json`. It creates entity coverage only through an
exact or explicitly compatible versioned REST endpoint link; Java support code
is cataloged separately by the generic builders, and Java files are not parsed
for Gherkin coverage.

Coverage evidence is stored and queried in SQLite, not projected into Ladybug.
The CLI report is available with:

```bash
./.venv/bin/python scripts/query_rest.py coverage GLAccount --json
```

The read-only MCP server exposes the same report through the `rest_coverage`
tool. Pass the canonical entity name and, when needed, an endpoint source
version such as `s1`. The result includes each endpoint's coverage status,
matching Gherkin cases, linked resolution/compatibility evidence, and directly
attributable parser diagnostics. The CLI and MCP use the same query helper so
their endpoint, case, and diagnostic results cannot drift independently.

Coverage is intentionally not a Ladybug graph edge today: the SQLite rows are
the authoritative evidence and retain the detailed test-case provenance. Use
the graph for code/entity traversal and SQLite or `rest_coverage` for coverage
auditing. A stale graph does not invalidate a SQLite coverage report, but a
catalog refresh does require the normal graph rebuild before graph queries.

Refresh runs against a candidate SQLite copy and promotes it only after
validation. The result records the exact indexed commit SHA. A failed attempt
does not replace the last active revision; it is retained separately as the
repository's latest-attempt status and diagnostic. Migration rebuilds empty
legacy source-table families into repository-qualified tables. A populated
legacy family that cannot preserve its IDs and child references fails closed;
rebuild that catalog from source before adding another repository. A catalog
promotion invalidates the prior graph projection; build a new Ladybug graph
through the normal operator workflow before issuing graph queries.
