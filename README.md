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

### Kùzu

Kùzu is the graph query layer.

Data from SQLite is projected into Kùzu to support graph traversal,
dependency analysis, impact analysis, and AI-assisted navigation.

### Design Principle

SQLite answers:

"What facts do we know?"

Kùzu answers:

"How are those facts connected?"

AI systems should use Kùzu for traversal and SQLite for evidence and
provenance.

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