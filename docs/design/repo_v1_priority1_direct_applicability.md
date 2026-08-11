# Repo-v1 Priority 1: Direct Database Applicability

Status: implementation and operations contract

## Purpose

This document defines the smallest Priority 1 catalog-inspection surface for
direct entity applicability. It is a read-only inspection contract over facts
already persisted by the repo-v1 database extractor. It does not add an
extractor, schema object, SQL view, migration, CLI, or production code.

The scope is database-only. A returned applicability row means that the
repo-v1 extractor persisted a direct link from one entity occurrence to a
database table or database field. The catalog must not manufacture a link when
that persisted fact is absent.

The queries below are written for SQLite and require an exact `:repo_id`
parameter. They use repository-owned persisted IDs for every relationship
between facts. `entity_nodes.id` is the canonical entity identity; the
repository-local occurrence is identified by `entity_occurrences.id`.

## Authoritative tables and statuses

The inspection contract uses these existing repo-v1 tables:

- `entity_nodes`: canonical entity name and ID.
- `entity_occurrences`: repository-local entity occurrence and its source
  provenance.
- `entity_db_table_links`: direct entity-occurrence-to-database-table facts.
- `entity_db_field_links`: direct entity-occurrence-to-database-field facts.
- `dbschema_tables`: database table facts extracted from `dbschema.inc`.
- `dbschema_fields`: database field facts, linked to `dbschema_tables` by
  `(repo_id, table_id)`.
- `repos` and `catalog_builds`: repository target and active-build provenance.

For the current direct applicability extractor, the link rows exposed by this
contract have only these statuses:

- `resolved`: the persisted target ID is present and joins to a database fact.
- `unresolved`: the source declared a direct target, but no persisted target ID
  was asserted.

The schema permits `ambiguous` and `unsupported` for wider database-fact
families. They are not current statuses emitted by the direct applicability
link extractor. Inspection must preserve any status stored in a row and must
not relabel it as resolved.

An entity occurrence with no persisted row in the relevant link table returns
no applicability rows. An entity name that has no occurrence in the requested
repository also returns no rows. This absence is not a guessed unresolved
mapping.

## Provenance contract

Every result must retain both levels of provenance:

1. Entity/source provenance comes from `entity_occurrences`:
   `occurrence_id`, `source_file_id`, `source_key`, `source_commit_sha`,
   `evidence`, and `extractor`.
2. Applicability-fact provenance comes from the applicable link row:
   `id`, `source_file_id`, `source_path`, `source_commit_sha`, `source_hash`,
   `source_pointer`, `start_line`, `end_line`, `evidence`, `extractor`, and
   `extractor_version`.

The repository target is read from `repos.target_commit_sha`. The build that
owns the repository is joined through `repos.build_id = catalog_builds.id`.
For the active build, `catalog_builds.status = 'active'` and
`catalog_builds.source_revisions_json` contains the source-revision object
keyed by repository key, currently for example
`{"ia-main":"<target-commit-sha>"}`. Both the scalar repository target and
the build JSON must be retained in inspection output so a consumer can verify
that the source and build revisions agree.

## Table applicability query

This query returns only persisted entity-to-table link facts. The `LEFT JOIN`
to `dbschema_tables` intentionally keeps an `unresolved` link visible while
returning null database-table columns. The join is still repository-scoped and
ID-based; it does not resolve a target by text.

```sql
SELECT
    en.id                         AS entity_id,
    en.name                       AS entity_name,
    eo.id                         AS occurrence_id,
    eo.source_file_id             AS entity_source_file_id,
    eo.source_key                 AS entity_source_key,
    eo.source_commit_sha          AS entity_source_commit_sha,
    eo.evidence                   AS entity_evidence,
    eo.extractor                  AS entity_extractor,
    l.id                          AS applicability_id,
    l.entity_table                AS declared_entity_table,
    l.link_type                   AS link_type,
    l.resolution_status           AS resolution_status,
    l.db_table_id                 AS db_table_id,
    t.table_name                  AS db_table_name,
    l.source_file_id              AS applicability_source_file_id,
    l.source_path                 AS applicability_source_path,
    l.source_commit_sha           AS applicability_source_commit_sha,
    l.source_hash                 AS applicability_source_hash,
    l.source_pointer              AS applicability_source_pointer,
    l.start_line                  AS applicability_start_line,
    l.end_line                    AS applicability_end_line,
    l.evidence                    AS applicability_evidence,
    l.extractor                   AS applicability_extractor,
    l.extractor_version           AS applicability_extractor_version,
    r.id                          AS repo_id,
    r.repo_key                    AS repo_key,
    r.target_commit_sha           AS repo_target_commit_sha,
    cb.id                         AS active_build_id,
    cb.build_token                AS active_build_token,
    cb.source_revisions_json      AS active_source_revisions_json
FROM entity_occurrences AS eo
JOIN entity_nodes AS en
  ON en.id = eo.entity_id
JOIN entity_db_table_links AS l
  ON l.repo_id = eo.repo_id
 AND l.occurrence_id = eo.id
JOIN repos AS r
  ON r.id = eo.repo_id
JOIN catalog_builds AS cb
  ON cb.id = r.build_id
 AND cb.status = 'active'
LEFT JOIN dbschema_tables AS t
  ON t.repo_id = l.repo_id
 AND t.id = l.db_table_id
WHERE eo.repo_id = :repo_id
  AND (:entity_name IS NULL OR en.name = :entity_name)
ORDER BY en.name, eo.id, l.id;
```

Example parameters:

```text
:repo_id = 1
:entity_name = 'BS_PriceList'
```

A resolved result has a non-null `db_table_id` and `db_table_name`; an
unresolved result has the direct link's declared value and provenance but null
database-table columns. A name such as `APBill` with no persisted table-link
row produces zero rows, even if a similarly named `dbschema_tables` row exists.

## Field applicability query

This query returns only persisted entity-to-field link facts. It reaches the
database field and its parent table through persisted `db_field_id` and
`dbschema_fields.table_id` IDs. The parent table join is repository-scoped.

```sql
SELECT
    en.id                         AS entity_id,
    en.name                       AS entity_name,
    eo.id                         AS occurrence_id,
    eo.source_file_id             AS entity_source_file_id,
    eo.source_key                 AS entity_source_key,
    eo.source_commit_sha          AS entity_source_commit_sha,
    eo.evidence                   AS entity_evidence,
    eo.extractor                  AS entity_extractor,
    l.id                          AS applicability_id,
    l.schema_mapping_id           AS schema_mapping_id,
    l.entity_field                AS entity_field,
    l.target_field                AS declared_target_field,
    l.link_type                   AS link_type,
    l.resolution_status            AS resolution_status,
    l.db_field_id                 AS db_field_id,
    f.table_id                    AS db_table_id,
    f.table_name                  AS db_table_name,
    f.field_name                  AS db_field_name,
    f.field_type                  AS db_field_type,
    l.source_file_id              AS applicability_source_file_id,
    l.source_path                 AS applicability_source_path,
    l.source_commit_sha           AS applicability_source_commit_sha,
    l.source_hash                 AS applicability_source_hash,
    l.source_pointer              AS applicability_source_pointer,
    l.start_line                  AS applicability_start_line,
    l.end_line                    AS applicability_end_line,
    l.evidence                    AS applicability_evidence,
    l.extractor                   AS applicability_extractor,
    l.extractor_version           AS applicability_extractor_version,
    r.id                          AS repo_id,
    r.repo_key                    AS repo_key,
    r.target_commit_sha           AS repo_target_commit_sha,
    cb.id                         AS active_build_id,
    cb.build_token                AS active_build_token,
    cb.source_revisions_json      AS active_source_revisions_json
FROM entity_occurrences AS eo
JOIN entity_nodes AS en
  ON en.id = eo.entity_id
JOIN entity_db_field_links AS l
  ON l.repo_id = eo.repo_id
 AND l.occurrence_id = eo.id
JOIN repos AS r
  ON r.id = eo.repo_id
JOIN catalog_builds AS cb
  ON cb.id = r.build_id
 AND cb.status = 'active'
LEFT JOIN dbschema_fields AS f
  ON f.repo_id = l.repo_id
 AND f.id = l.db_field_id
WHERE eo.repo_id = :repo_id
  AND (:entity_name IS NULL OR en.name = :entity_name)
ORDER BY en.name, eo.id, l.id;
```

Example parameters:

```text
:repo_id = 1
:entity_name = 'BS_PriceList'
```

For a resolved field row, `db_field_id`, `db_table_id`, `db_table_name`, and
`db_field_name` are populated by persisted ID joins. For an unresolved row,
the link remains visible with its declared entity/target field and provenance;
the database-fact columns are null.

## Prohibited resolution behavior

These queries and any future catalog-inspection consumer must not infer
applicability using:

- basename or filename matching;
- source-path matching or path proximity;
- case folding, spelling similarity, prefixes, or naming conventions;
- ORM aliases, model conventions, or framework knowledge;
- text matching between entity names, table names, and field names;
- transitive relationships, API names, workflow names, security names, or UI
  names.

Only persisted foreign-key IDs and the direct link rows shown above establish
applicability. A missing or unresolved persisted target remains missing or
unresolved.

## Scope boundaries and deferred work

This Priority 1 contract does not change or consume the PR-impact report
contract. PR-impact validation and bounded evidence work remain a later phase.

The following are explicitly deferred and must not be added as part of this
document:

- PR-impact analysis;
- API or OpenAPI applicability;
- workflow applicability;
- security, permissions, or role applicability;
- actionUI or NextGen UI applicability;
- transitive or cross-repository closure;
- graph or Ladybug construction;
- MCP or query-surface compatibility;
- delta planning, delta refresh, or catalog change-set processing;
- legacy catalog builders, schemas, or refreshes;
- schema changes, views, migrations, or new CLI commands;
- changes to the canonical `catalog/catalog.db`.

The active repo-v1 database remains an input for read-only inspection. Any
candidate or active database used operationally must still pass the existing
repo-v1 ownership, provenance, integrity, and foreign-key validation.
