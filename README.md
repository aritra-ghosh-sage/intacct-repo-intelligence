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

Apply the graph-build metadata migration once to an existing catalog before the
first safe rebuild:

```bash
python -c "import sqlite3; from pathlib import Path; c=sqlite3.connect('catalog/catalog.db'); [c.executescript(Path(m).read_text()) for m in ('migrations/017_graph_builds.sql', 'migrations/018_graph_build_status_previous.sql')]; c.close()"
```

Fresh catalogs initialized through `catalog.db.init_db()` already contain this
schema. Full Ladybug construction is a lengthy operator-run workflow and must
not be started during an agentic session. Agents should restrict themselves to
read-only parity validation and focused unit tests. SQLite query commands remain
the authoritative evidence path; Ladybug is a rebuildable traversal projection.

### REST Automation Coverage

Apply `migrations/019_versioned_rest_test_coverage.sql` once to an existing
catalog, then register each automation suite before ingesting its Gherkin
evidence. The ingestion reads only feature files, same-stem properties metadata
(`version` and `testObject`), and the suite object mapping.

```bash
sqlite3 catalog/catalog.db ".read migrations/019_versioned_rest_test_coverage.sql"

python scripts/register_rest_automation_suite.py \
  --suite-id ia-restapi-automation \
  --suite-root /Users/aritra.ghosh/projects/ia-restapi-automation \
  --object-mapping /Users/aritra.ghosh/projects/ia-restapi-automation/src/test/resources/object-mapping.json

python scripts/build_gherkin_coverage.py \
  --suite-id ia-restapi-automation \
  --suite-root /Users/aritra.ghosh/projects/ia-restapi-automation \
  --object-mapping /Users/aritra.ghosh/projects/ia-restapi-automation/src/test/resources/object-mapping.json

python scripts/query_rest.py coverage APBill --version s1
```

`graph_ready_entities` is an advisory triage view for entities with strong root
evidence. It intentionally excludes weak or unrooted entities and must not be
used to filter authoritative queries or the complete graph projection.

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
