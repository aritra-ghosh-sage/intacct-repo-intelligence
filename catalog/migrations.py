"""Catalog migrations that need SQLite table reconstruction.

SQL-only migrations are retained as operator-visible markers in ``migrations``.
This module owns the one migration that must preserve existing ``files.id``
values while changing the legacy global path uniqueness constraint.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable
from pathlib import Path

MULTI_REPO_MIGRATION = "019_multi_repo"
REST_AUTOMATION_COVERAGE_MIGRATION = "020_rest_automation_coverage"
ENTITY_SEMANTICS_MIGRATION = "021_entity_semantics"
ENTITY_SEMANTICS_REPO_SCOPE_MIGRATION = "022_entity_semantics_repo_scope"
DELTA_REFRESH_MIGRATION = "023_delta_refresh"
REFRESH_CONTRACTS_MIGRATION = "024_refresh_contracts"
LEGACY_REPO_KEY = "ia-main"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def _add_columns(
    conn: sqlite3.Connection, table: str, definitions: Iterable[str]
) -> None:
    if not _table_exists(conn, table):
        return
    existing = _columns(conn, table)
    for definition in definitions:
        name = definition.split()[0]
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _ensure_registry_columns(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "repos"):
        conn.execute(
            """CREATE TABLE repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_key TEXT NOT NULL UNIQUE,
                name TEXT, kind TEXT, language TEXT, remote_url TEXT,
                local_root TEXT NOT NULL, tracked_branch TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, profile TEXT,
                effective_builders_json TEXT NOT NULL DEFAULT '[]',
                indexed_commit_sha TEXT, last_scanned_at TEXT, last_built_at TEXT,
                index_status TEXT NOT NULL DEFAULT 'never_indexed', diagnostic_error TEXT,
                last_attempt_status TEXT NOT NULL DEFAULT 'never_attempted',
                last_attempted_at TEXT, last_attempt_error TEXT
            )"""
        )
        return
    _add_columns(
        conn,
        "repos",
        (
            "repo_key TEXT",
            "remote_url TEXT",
            "local_root TEXT",
            "tracked_branch TEXT",
            "enabled INTEGER NOT NULL DEFAULT 1",
            "profile TEXT",
            "effective_builders_json TEXT NOT NULL DEFAULT '[]'",
            "indexed_commit_sha TEXT",
            "last_scanned_at TEXT",
            "last_built_at TEXT",
            "index_status TEXT NOT NULL DEFAULT 'never_indexed'",
            "diagnostic_error TEXT",
            "last_attempt_status TEXT NOT NULL DEFAULT 'never_attempted'",
            "last_attempted_at TEXT",
            "last_attempt_error TEXT",
        ),
    )


def _legacy_repo_id(
    conn: sqlite3.Connection, *, local_root: str, tracked_branch: str
) -> int:
    _ensure_registry_columns(conn)
    row = conn.execute(
        "SELECT id FROM repos WHERE repo_key = ?", (LEGACY_REPO_KEY,)
    ).fetchone()
    if row is not None:
        return int(row[0])
    # A legacy repos table was an unused placeholder.  Preserve its first row's
    # descriptive fields, if present, but make the stable key authoritative.
    legacy = conn.execute(
        "SELECT id, name, kind, language FROM repos ORDER BY id LIMIT 1"
    ).fetchone()
    if legacy is not None:
        conn.execute(
            """UPDATE repos SET repo_key = ?, local_root = ?, tracked_branch = ?,
               enabled = COALESCE(enabled, 1),
               effective_builders_json = COALESCE(effective_builders_json, '[]'),
               index_status = COALESCE(index_status, 'never_indexed') WHERE id = ?""",
            (LEGACY_REPO_KEY, local_root, tracked_branch, legacy[0]),
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_repos_repo_key ON repos(repo_key)"
        )
        return int(legacy[0])
    cursor = conn.execute(
        """INSERT INTO repos(repo_key, name, local_root, tracked_branch, enabled,
                              effective_builders_json, index_status)
           VALUES (?, ?, ?, ?, 1, '[]', 'never_indexed')""",
        (LEGACY_REPO_KEY, "Intacct Main", local_root, tracked_branch),
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_repos_repo_key ON repos(repo_key)"
    )
    return int(cursor.lastrowid)


def _rebuild_files(conn: sqlite3.Connection, repo_id: int) -> None:
    """Rebuild files with composite uniqueness, retaining original IDs."""

    if not _table_exists(conn, "files"):
        return
    columns = _columns(conn, "files")
    if "repo_id" in columns:
        conn.execute("UPDATE files SET repo_id = ? WHERE repo_id IS NULL", (repo_id,))
        return
    conn.execute("DROP INDEX IF EXISTS idx_files_path")
    conn.execute("DROP INDEX IF EXISTS idx_files_language")

    def source(column: str) -> str:
        return column if column in columns else f"NULL AS {column}"

    conn.execute(
        """CREATE TABLE files_multi_repo_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            language TEXT, size_bytes INTEGER, sha1 TEXT, last_modified TEXT,
            last_indexed TEXT, last_symbols_extracted TEXT,
            last_relationships_extracted TEXT,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            UNIQUE(repo_id, path)
        )"""
    )
    select_columns = ", ".join(
        source(column)
        for column in (
            "id",
            "path",
            "language",
            "size_bytes",
            "sha1",
            "last_modified",
            "last_indexed",
            "last_symbols_extracted",
            "last_relationships_extracted",
        )
    )
    conn.execute(
        """INSERT INTO files_multi_repo_new(
            id, repo_id, path, language, size_bytes, sha1, last_modified,
            last_indexed, last_symbols_extracted, last_relationships_extracted
        ) SELECT id, ?, path, language, size_bytes, sha1, last_modified,
                 last_indexed, last_symbols_extracted, last_relationships_extracted
           FROM (SELECT """
        + select_columns
        + " FROM files)",
        (repo_id,),
    )
    conn.execute("DROP TABLE files")
    conn.execute("ALTER TABLE files_multi_repo_new RENAME TO files")
    conn.execute("CREATE INDEX idx_files_repo_path ON files(repo_id, path)")
    conn.execute("CREATE INDEX idx_files_repo_language ON files(repo_id, language)")


def _add_repo_ownership(conn: sqlite3.Connection, repo_id: int) -> None:
    # These tables own source-derived records directly.  Child tables inherit
    # ownership through their parent and deliberately do not duplicate repo_id.
    for table in (
        "relationships",
        "entity_mappings",
        "entity_roots",
        "workflows",
        "openapi_file_ref_edges",
        "rest_endpoints",
        "openapispec_index",
        "security_operations",
        "security_policies",
        "security_menus",
        "dbschema_tables",
        "entity_access_links",
    ):
        if not _table_exists(conn, table):
            continue
        _add_columns(conn, table, ("repo_id INTEGER",))
        conn.execute(
            f"UPDATE {table} SET repo_id = ? WHERE repo_id IS NULL", (repo_id,)
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_repo_id ON {table}(repo_id)"
        )


def _rebuild_workflows(conn: sqlite3.Connection, repo_id: int) -> None:
    """Replace the legacy global workflow identity with a repo-local one.

    Workflow child tables retain their workflow IDs.  With foreign-key checks
    disabled by the enclosing migration, dropping and recreating the parent
    under the same name preserves those references and ``id`` values.
    """

    if not _table_exists(conn, "workflows"):
        return
    if "repo_id" in _columns(conn, "workflows"):
        # A current-schema database has the correct composite identity already.
        # Avoid needless reconstruction (and invalidating dependent views).
        return
    conn.execute(
        """CREATE TABLE workflows_multi_repo_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_file TEXT,
            file_id INTEGER,
            source_symbol_id INTEGER,
            confidence REAL DEFAULT 1.0,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(repo_id, entity_id, name, workflow_type, source_file),
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(source_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL
        )"""
    )
    available = _columns(conn, "workflows")
    required = (
        "id",
        "repo_id",
        "entity_id",
        "name",
        "workflow_type",
        "source_kind",
        "source_file",
        "file_id",
        "source_symbol_id",
        "confidence",
        "reason",
        "created_at",
    )
    values = ", ".join(
        "? AS repo_id"
        if column == "repo_id"
        else column
        if column in available
        else f"NULL AS {column}"
        for column in required
    )
    conn.execute(
        "INSERT INTO workflows_multi_repo_new("
        + ", ".join(required)
        + ") SELECT "
        + values
        + " FROM workflows",
        (repo_id,),
    )
    conn.execute("DROP INDEX IF EXISTS idx_workflows_entity")
    conn.execute("DROP INDEX IF EXISTS idx_workflows_type")
    conn.execute("DROP INDEX IF EXISTS idx_workflows_source")
    conn.execute("DROP INDEX IF EXISTS idx_workflows_file_id")
    conn.execute("DROP TABLE workflows")
    conn.execute("ALTER TABLE workflows_multi_repo_new RENAME TO workflows")
    for statement in (
        "CREATE INDEX idx_workflows_entity ON workflows(entity_id)",
        "CREATE INDEX idx_workflows_type ON workflows(workflow_type)",
        "CREATE INDEX idx_workflows_source ON workflows(source_kind)",
        "CREATE INDEX idx_workflows_file_id ON workflows(file_id)",
    ):
        conn.execute(statement)


_EMPTY_REBUILD_FAMILIES = (
    # Parents are listed after their children so DROP TABLE works with foreign
    # key enforcement temporarily disabled and leaves no old child constraint.
    "security_operation_allowops",
    "security_policy_eops",
    "security_policy_values",
    "security_menu_op_links",
    "security_menu_items",
    "dbschema_fields",
    "security_operations",
    "security_policies",
    "security_menus",
    "dbschema_tables",
    "relationships",
    "entity_roots",
    "openapi_file_ref_edges",
    "openapispec_index",
    "rest_endpoints",
)


def _reject_populated_unsafe_legacy_families(conn: sqlite3.Connection) -> None:
    """Fail closed rather than silently retain an old global UNIQUE contract.

    A complete table rebuild for each of these parent/child families belongs in
    a follow-up migration.  Until then an existing populated catalog must be
    rebuilt from source instead of accepting a second repository and merging
    records under its legacy uniqueness constraint.
    """

    populated = [
        table
        for table in _EMPTY_REBUILD_FAMILIES
        if _table_exists(conn, table)
        and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
    ]
    if populated:
        raise RuntimeError(
            "019_multi_repo cannot safely migrate populated legacy unique tables: "
            + ", ".join(populated)
            + ". Rebuild this catalog from source with the new schema, or apply a future "
            "table-family migration that rewrites their uniqueness constraints."
        )


def _rebuild_empty_legacy_families(conn: sqlite3.Connection) -> None:
    """Replace empty legacy source families with the current schema.

    Adding ``repo_id`` to an empty old table is insufficient: SQLite retains
    its old global UNIQUE constraint, while scoped builders use composite
    conflict targets.  Dropping every parent and child in the affected
    families then replaying the canonical schema replaces both tables and
    indexes without fabricating provenance.
    """

    existing = [
        table for table in _EMPTY_REBUILD_FAMILIES if _table_exists(conn, table)
    ]
    for table in existing:
        conn.execute(f"DROP TABLE {table}")
    if existing:
        # Obtain only the affected canonical DDL from a fresh schema.  Replaying
        # the whole file against a legacy catalog would also try to build
        # indexes/views that reference legacy columns outside this migration.
        template = sqlite3.connect(":memory:")
        try:
            template.executescript((Path(__file__).with_name("schema.sql")).read_text())
            for table in existing:
                ddl = template.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0]
                conn.execute(ddl)
            for table in existing:
                for (ddl,) in template.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                    (table,),
                ):
                    conn.execute(ddl)
        finally:
            template.close()


def _rebuild_entity_access_links(conn: sqlite3.Connection, repo_id: int) -> None:
    """Replace the legacy global uniqueness contract with repo-scoped identity."""

    if not _table_exists(conn, "entity_access_links"):
        return
    columns = _columns(conn, "entity_access_links")
    if "repo_id" in columns:
        return
    conn.execute(
        """CREATE TABLE entity_access_links_multi_repo_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            surface TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            evidence_file_id INTEGER,
            evidence_symbol_id INTEGER,
            confidence_mode TEXT NOT NULL DEFAULT 'deterministic_exact',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(evidence_file_id) REFERENCES files(id) ON DELETE SET NULL,
            FOREIGN KEY(evidence_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
            UNIQUE(repo_id, entity_id, surface, record_id, link_type, evidence_file_id, evidence_symbol_id)
        )"""
    )
    available = columns
    required = (
        "id",
        "repo_id",
        "entity_id",
        "surface",
        "record_id",
        "link_type",
        "evidence_file_id",
        "evidence_symbol_id",
        "confidence_mode",
        "notes",
        "created_at",
    )
    values = ", ".join(
        "? AS repo_id"
        if column == "repo_id"
        else column
        if column in available
        else f"NULL AS {column}"
        for column in required
    )
    conn.execute(
        "INSERT INTO entity_access_links_multi_repo_new("
        + ", ".join(required)
        + ") SELECT "
        + values
        + " FROM entity_access_links",
        (repo_id,),
    )
    conn.execute("DROP TABLE entity_access_links")
    conn.execute(
        "ALTER TABLE entity_access_links_multi_repo_new RENAME TO entity_access_links"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_access_links_entity_surface ON entity_access_links(entity_id, surface)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_access_links_surface_record ON entity_access_links(surface, record_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_access_links_evidence_file ON entity_access_links(evidence_file_id)"
    )


def _ensure_entity_occurrences(conn: sqlite3.Connection, repo_id: int | None) -> None:
    """Create occurrences and preserve legacy entity metadata when present."""

    conn.execute(
        """CREATE TABLE IF NOT EXISTS entity_occurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            ent_file TEXT, module TEXT, table_name TEXT, view_name TEXT,
            dummy INTEGER, source_file_id INTEGER, extractor TEXT,
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(repo_id, entity_id),
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_occurrences_entity ON entity_occurrences(entity_id)"
    )
    if repo_id is None or not _table_exists(conn, "entity_nodes"):
        return
    columns = _columns(conn, "entity_nodes")
    # Legacy entity_nodes carried these checkout-local fields.  Copy only
    # fields actually present; a current schema has none and starts empty.
    metadata = (
        "ent_file",
        "module",
        "table_name",
        "view_name",
        "dummy",
        "source_file_id",
        "confidence",
    )
    if not any(column in columns for column in metadata):
        return
    select = ", ".join(
        column if column in columns else f"NULL AS {column}"
        for column in ("id", *metadata)
    )
    conn.execute(
        """INSERT OR IGNORE INTO entity_occurrences(
               repo_id, entity_id, ent_file, module, table_name, view_name,
               dummy, source_file_id, confidence, extractor
           )
           SELECT ?, id, ent_file, module, table_name, view_name, dummy,
                  source_file_id, confidence, '019_legacy_entity_backfill'
           FROM (SELECT """
        + select
        + " FROM entity_nodes)",
        (repo_id,),
    )


def _create_multi_repo_tables(conn: sqlite3.Connection) -> None:
    script = """
        CREATE TABLE IF NOT EXISTS repo_index_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            tracked_branch TEXT NOT NULL, commit_sha TEXT, manifest_hash TEXT,
            builder_plan_hash TEXT, catalog_fingerprint TEXT,
            status TEXT NOT NULL CHECK(status IN ('building','validated','active','failed')),
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT,
            validation_summary TEXT, diagnostic_error TEXT,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_repo_index_runs_repo_started
            ON repo_index_runs(repo_id, started_at DESC);
        CREATE TABLE IF NOT EXISTS repo_index_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
            builder_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','skipped')),
            started_at TEXT, completed_at TEXT, record_count INTEGER, diagnostic_error TEXT,
            UNIQUE(run_id, builder_name),
            FOREIGN KEY(run_id) REFERENCES repo_index_runs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS integration_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_repo_id INTEGER NOT NULL,
            target_repo_id INTEGER, source_file_id INTEGER, target_file_id INTEGER,
            source_symbol_id INTEGER, target_symbol_id INTEGER,
            relation_type TEXT NOT NULL,
            resolution_status TEXT NOT NULL CHECK(resolution_status IN ('resolved','unresolved','ambiguous','invalid')),
            external_identifier TEXT, evidence TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0, source_commit_sha TEXT,
            target_commit_sha TEXT, extractor TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, validated_at TEXT,
            FOREIGN KEY(source_repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(target_repo_id) REFERENCES repos(id) ON DELETE SET NULL,
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            FOREIGN KEY(target_file_id) REFERENCES files(id) ON DELETE SET NULL,
            FOREIGN KEY(source_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
            FOREIGN KEY(target_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_integration_links_source_repo
            ON integration_links(source_repo_id, resolution_status);
        CREATE INDEX IF NOT EXISTS idx_integration_links_target_repo
            ON integration_links(target_repo_id, resolution_status);
        """
    # ``executescript`` commits an open transaction in sqlite3.  The migration
    # must remain atomic, so execute the DDL statements individually.
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _create_rest_automation_coverage_tables(conn: sqlite3.Connection) -> None:
    """Install additive Gherkin coverage tables using the canonical repo registry."""
    _add_columns(conn, "rest_endpoints", ("source_version TEXT",))
    script = """
        CREATE TABLE IF NOT EXISTS api_version_compatibility (
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_version TEXT NOT NULL,
            endpoint_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','deprecated','disabled')),
            rationale TEXT NOT NULL, evidence TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(test_version, endpoint_version)
        );
        CREATE TABLE IF NOT EXISTS test_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL, feature_name TEXT NOT NULL,
            scenario_name TEXT NOT NULL, case_name TEXT NOT NULL, example_row INTEGER,
            feature_line INTEGER NOT NULL, scenario_line INTEGER NOT NULL,
            eligibility TEXT NOT NULL DEFAULT 'active'
                CHECK(eligibility IN ('active','known_issue','ci_only','conditional')),
            tags_json TEXT NOT NULL DEFAULT '[]', jira_refs_json TEXT NOT NULL DEFAULT '[]',
            source_hash TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
            UNIQUE(repo_id, file_id, scenario_line, example_row)
        );
        CREATE INDEX IF NOT EXISTS idx_test_cases_repo ON test_cases(repo_id);
        CREATE INDEX IF NOT EXISTS idx_test_cases_file ON test_cases(file_id);
        CREATE INDEX IF NOT EXISTS idx_test_cases_eligibility ON test_cases(eligibility);
        CREATE TABLE IF NOT EXISTS test_case_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_case_id INTEGER NOT NULL,
            version_label TEXT NOT NULL, source_kind TEXT NOT NULL
                CHECK(source_kind IN ('feature_tag','properties','request_override')),
            source_file_id INTEGER, source_line INTEGER, raw_value TEXT NOT NULL,
            FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(test_case_id, version_label, source_kind, source_file_id, source_line)
        );
        CREATE INDEX IF NOT EXISTS idx_test_case_versions_case ON test_case_versions(test_case_id);
        CREATE INDEX IF NOT EXISTS idx_test_case_versions_label ON test_case_versions(version_label);
        CREATE TABLE IF NOT EXISTS test_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_case_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL, step_line INTEGER NOT NULL, method TEXT,
            object_token TEXT, raw_path TEXT, normalized_path TEXT, request_version TEXT,
            expected_status INTEGER, operation_kind TEXT NOT NULL DEFAULT 'unknown'
                CHECK(operation_kind IN ('collection','item','child','workflow','custom','unknown')),
            FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
            UNIQUE(test_case_id, ordinal)
        );
        CREATE INDEX IF NOT EXISTS idx_test_requests_case ON test_requests(test_case_id);
        CREATE INDEX IF NOT EXISTS idx_test_requests_route ON test_requests(method, normalized_path, request_version);
        CREATE TABLE IF NOT EXISTS test_endpoint_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_request_id INTEGER NOT NULL,
            rest_endpoint_id INTEGER NOT NULL, compatibility_id INTEGER,
            resolution_kind TEXT NOT NULL CHECK(resolution_kind IN ('exact_version','compatible_version')),
            FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE,
            FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE CASCADE,
            FOREIGN KEY(compatibility_id) REFERENCES api_version_compatibility(id) ON DELETE SET NULL,
            UNIQUE(test_request_id, rest_endpoint_id)
        );
        CREATE INDEX IF NOT EXISTS idx_test_endpoint_links_endpoint ON test_endpoint_links(rest_endpoint_id);
        CREATE TABLE IF NOT EXISTS test_entity_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_request_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL, rest_endpoint_id INTEGER NOT NULL,
            FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE,
            FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE CASCADE,
            UNIQUE(test_request_id, entity_id, rest_endpoint_id)
        );
        CREATE INDEX IF NOT EXISTS idx_test_entity_links_entity ON test_entity_links(entity_id);
        CREATE TABLE IF NOT EXISTS test_diagnostics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            file_id INTEGER, test_case_id INTEGER, test_request_id INTEGER,
            kind TEXT NOT NULL, message TEXT NOT NULL, source_line INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL,
            FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
            FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_test_diagnostics_repo_kind ON test_diagnostics(repo_id, kind);
    """
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _create_entity_semantics_tables(conn: sqlite3.Connection) -> None:
    """Create the additive 021 semantic-evidence table family."""
    script = """
        CREATE TABLE IF NOT EXISTS entity_schema_components (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            occurrence_id INTEGER NOT NULL, component_kind TEXT NOT NULL,
            component_path TEXT NOT NULL, declared_name TEXT, target_literal TEXT,
            data_type TEXT, cardinality TEXT, writeability TEXT,
            properties_json TEXT NOT NULL DEFAULT '{}', source_file_id INTEGER,
            source_path TEXT NOT NULL, start_line INTEGER, end_line INTEGER,
            evidence_text TEXT NOT NULL, evidence_hash TEXT NOT NULL,
            extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id, occurrence_id, component_kind, component_path, evidence_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_schema_components_occurrence
            ON entity_schema_components(repo_id, occurrence_id, component_kind);
        CREATE INDEX IF NOT EXISTS idx_entity_schema_components_source
            ON entity_schema_components(repo_id, source_path);
        CREATE TABLE IF NOT EXISTS entity_relationship_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            source_occurrence_id INTEGER NOT NULL, source_component_id INTEGER,
            axis TEXT NOT NULL CHECK(axis IN ('A','B','C','D','E')),
            relation_kind TEXT NOT NULL, fact_key TEXT NOT NULL,
            target_occurrence_id INTEGER, target_entity_name TEXT,
            target_component_id INTEGER, target_literal TEXT, cardinality TEXT,
            assertion_status TEXT NOT NULL CHECK(assertion_status IN
                ('VERIFIED','CORROBORATED','UNRESOLVED','CONFLICTING')),
            qualifiers_json TEXT NOT NULL DEFAULT '{}', source_file_id INTEGER,
            source_path TEXT NOT NULL, start_line INTEGER, end_line INTEGER,
            evidence_text TEXT NOT NULL, evidence_hash TEXT NOT NULL,
            extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(source_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
            FOREIGN KEY(source_component_id) REFERENCES entity_schema_components(id) ON DELETE SET NULL,
            FOREIGN KEY(target_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE SET NULL,
            FOREIGN KEY(target_component_id) REFERENCES entity_schema_components(id) ON DELETE SET NULL,
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id, fact_key, evidence_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_occurrence
            ON entity_relationship_facts(repo_id, source_occurrence_id, axis);
        CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_target
            ON entity_relationship_facts(repo_id, target_occurrence_id, axis);
        CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_status
            ON entity_relationship_facts(repo_id, assertion_status);
        CREATE TABLE IF NOT EXISTS entity_operation_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            occurrence_id INTEGER NOT NULL,
            axis TEXT NOT NULL CHECK(axis IN ('A','B','C','D','E')),
            operation TEXT NOT NULL CHECK(operation IN
                ('create','read','update','delete','approve','submit','decline')),
            surface_kind TEXT NOT NULL, rest_endpoint_id INTEGER,
            security_operation_id INTEGER, symbol_id INTEGER,
            availability TEXT NOT NULL CHECK(availability IN
                ('allowed','denied','not_declared','unresolved')),
            invocation_context TEXT NOT NULL CHECK(invocation_context IN
                ('root','child','both','any','unresolved')),
            persistence_scope TEXT NOT NULL CHECK(persistence_scope IN
                ('root','entity','shared','entity_override','unresolved')),
            standalone INTEGER CHECK(standalone IN (0,1)), parent_occurrence_id INTEGER,
            qualifiers_json TEXT NOT NULL DEFAULT '{}', source_file_id INTEGER,
            source_path TEXT NOT NULL, start_line INTEGER, end_line INTEGER,
            evidence_text TEXT NOT NULL, evidence_hash TEXT NOT NULL,
            extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
            FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE SET NULL,
            FOREIGN KEY(security_operation_id) REFERENCES security_operations(id) ON DELETE SET NULL,
            FOREIGN KEY(symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
            FOREIGN KEY(parent_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE SET NULL,
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id, occurrence_id, operation, surface_kind, evidence_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_operation_facts_occurrence
            ON entity_operation_facts(repo_id, occurrence_id, operation);
        CREATE TABLE IF NOT EXISTS entity_extraction_coverage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            occurrence_id INTEGER NOT NULL, source_file_id INTEGER,
            source_path TEXT NOT NULL, extractor TEXT NOT NULL,
            extractor_version TEXT NOT NULL,
            declaration_family TEXT NOT NULL CHECK(declaration_family IN
                ('A','B','C','D','E','components','operations')), source_hash TEXT,
            status TEXT NOT NULL CHECK(status IN ('complete','partial','failed','not_applicable')),
            component_count INTEGER NOT NULL DEFAULT 0, fact_count INTEGER NOT NULL DEFAULT 0,
            diagnostic TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id, occurrence_id, extractor, extractor_version,
                declaration_family, source_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_extraction_coverage_occurrence
            ON entity_extraction_coverage(repo_id, occurrence_id, declaration_family);
        CREATE TABLE IF NOT EXISTS entity_semantic_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            fact_key TEXT NOT NULL, left_fact_id INTEGER NOT NULL,
            right_fact_id INTEGER NOT NULL, conflict_kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open','resolved','accepted_unresolved')),
            reason TEXT NOT NULL, resolution_evidence TEXT, source_file_id INTEGER,
            source_path TEXT, confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(left_fact_id) REFERENCES entity_relationship_facts(id) ON DELETE CASCADE,
            FOREIGN KEY(right_fact_id) REFERENCES entity_relationship_facts(id) ON DELETE CASCADE,
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id, fact_key, left_fact_id, right_fact_id)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_semantic_conflicts_fact_key
            ON entity_semantic_conflicts(repo_id, fact_key, status);
    """
    # ``executescript`` commits SQLite's active transaction.  The 019/021
    # migration must remain atomic, so execute statements individually.
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _semantic_repo_scope_violations(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Return repository ownership mismatches that ordinary single-column FKs miss."""
    checks = (
        (
            "component_occurrence",
            "SELECT COUNT(*) FROM entity_schema_components c "
            "JOIN entity_occurrences o ON o.id=c.occurrence_id "
            "WHERE c.repo_id<>o.repo_id",
        ),
        (
            "relationship_source_occurrence",
            "SELECT COUNT(*) FROM entity_relationship_facts f "
            "JOIN entity_occurrences o ON o.id=f.source_occurrence_id "
            "WHERE f.repo_id<>o.repo_id",
        ),
        (
            "relationship_target_occurrence",
            "SELECT COUNT(*) FROM entity_relationship_facts f "
            "JOIN entity_occurrences o ON o.id=f.target_occurrence_id "
            "WHERE f.target_occurrence_id IS NOT NULL AND f.repo_id<>o.repo_id",
        ),
        (
            "relationship_source_component",
            "SELECT COUNT(*) FROM entity_relationship_facts f "
            "JOIN entity_schema_components c ON c.id=f.source_component_id "
            "WHERE f.source_component_id IS NOT NULL AND f.repo_id<>c.repo_id",
        ),
        (
            "relationship_target_component",
            "SELECT COUNT(*) FROM entity_relationship_facts f "
            "JOIN entity_schema_components c ON c.id=f.target_component_id "
            "WHERE f.target_component_id IS NOT NULL AND f.repo_id<>c.repo_id",
        ),
        (
            "operation_occurrence",
            "SELECT COUNT(*) FROM entity_operation_facts f "
            "JOIN entity_occurrences o ON o.id=f.occurrence_id "
            "WHERE f.repo_id<>o.repo_id",
        ),
        (
            "operation_parent_occurrence",
            "SELECT COUNT(*) FROM entity_operation_facts f "
            "JOIN entity_occurrences o ON o.id=f.parent_occurrence_id "
            "WHERE f.parent_occurrence_id IS NOT NULL AND f.repo_id<>o.repo_id",
        ),
        (
            "coverage_occurrence",
            "SELECT COUNT(*) FROM entity_extraction_coverage c "
            "JOIN entity_occurrences o ON o.id=c.occurrence_id "
            "WHERE c.repo_id<>o.repo_id",
        ),
        (
            "conflict_left_fact",
            "SELECT COUNT(*) FROM entity_semantic_conflicts c "
            "JOIN entity_relationship_facts f ON f.id=c.left_fact_id "
            "WHERE c.repo_id<>f.repo_id",
        ),
        (
            "conflict_right_fact",
            "SELECT COUNT(*) FROM entity_semantic_conflicts c "
            "JOIN entity_relationship_facts f ON f.id=c.right_fact_id "
            "WHERE c.repo_id<>f.repo_id",
        ),
    )
    violations: list[tuple[str, int]] = []
    for name, query in checks:
        count = int(conn.execute(query).fetchone()[0])
        if count:
            violations.append((name, count))
    return violations


def _create_repo_scoped_entity_semantics_tables(conn: sqlite3.Connection) -> None:
    """Create the 022 semantic tables with composite repository ownership FKs."""
    script = """
        CREATE TABLE entity_schema_components (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            occurrence_id INTEGER NOT NULL, component_kind TEXT NOT NULL,
            component_path TEXT NOT NULL, declared_name TEXT, target_literal TEXT,
            data_type TEXT, cardinality TEXT, writeability TEXT,
            properties_json TEXT NOT NULL DEFAULT '{}', source_file_id INTEGER,
            source_path TEXT NOT NULL, start_line INTEGER, end_line INTEGER,
            evidence_text TEXT NOT NULL, evidence_hash TEXT NOT NULL,
            extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id,repo_id) REFERENCES entity_occurrences(id,repo_id),
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id,occurrence_id,component_kind,component_path,evidence_hash)
        );
        CREATE TABLE entity_relationship_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            source_occurrence_id INTEGER NOT NULL, source_component_id INTEGER,
            axis TEXT NOT NULL CHECK(axis IN ('A','B','C','D','E')),
            relation_kind TEXT NOT NULL, fact_key TEXT NOT NULL,
            target_occurrence_id INTEGER, target_entity_name TEXT,
            target_component_id INTEGER, target_literal TEXT, cardinality TEXT,
            assertion_status TEXT NOT NULL CHECK(assertion_status IN
                ('VERIFIED','CORROBORATED','UNRESOLVED','CONFLICTING')),
            qualifiers_json TEXT NOT NULL DEFAULT '{}', source_file_id INTEGER,
            source_path TEXT NOT NULL, start_line INTEGER, end_line INTEGER,
            evidence_text TEXT NOT NULL, evidence_hash TEXT NOT NULL,
            extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(source_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
            FOREIGN KEY(source_occurrence_id,repo_id) REFERENCES entity_occurrences(id,repo_id),
            FOREIGN KEY(source_component_id) REFERENCES entity_schema_components(id) ON DELETE SET NULL,
            FOREIGN KEY(source_component_id,repo_id) REFERENCES entity_schema_components(id,repo_id),
            FOREIGN KEY(target_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE SET NULL,
            FOREIGN KEY(target_occurrence_id,repo_id) REFERENCES entity_occurrences(id,repo_id),
            FOREIGN KEY(target_component_id) REFERENCES entity_schema_components(id) ON DELETE SET NULL,
            FOREIGN KEY(target_component_id,repo_id) REFERENCES entity_schema_components(id,repo_id),
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id,fact_key,evidence_hash)
        );
        CREATE TABLE entity_operation_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            occurrence_id INTEGER NOT NULL,
            axis TEXT NOT NULL CHECK(axis IN ('A','B','C','D','E')),
            operation TEXT NOT NULL CHECK(operation IN
                ('create','read','update','delete','approve','submit','decline')),
            surface_kind TEXT NOT NULL, rest_endpoint_id INTEGER,
            security_operation_id INTEGER, symbol_id INTEGER,
            availability TEXT NOT NULL CHECK(availability IN
                ('allowed','denied','not_declared','unresolved')),
            invocation_context TEXT NOT NULL CHECK(invocation_context IN
                ('root','child','both','any','unresolved')),
            persistence_scope TEXT NOT NULL CHECK(persistence_scope IN
                ('root','entity','shared','entity_override','unresolved')),
            standalone INTEGER CHECK(standalone IN (0,1)), parent_occurrence_id INTEGER,
            qualifiers_json TEXT NOT NULL DEFAULT '{}', source_file_id INTEGER,
            source_path TEXT NOT NULL, start_line INTEGER, end_line INTEGER,
            evidence_text TEXT NOT NULL, evidence_hash TEXT NOT NULL,
            extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id,repo_id) REFERENCES entity_occurrences(id,repo_id),
            FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE SET NULL,
            FOREIGN KEY(security_operation_id) REFERENCES security_operations(id) ON DELETE SET NULL,
            FOREIGN KEY(symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
            FOREIGN KEY(parent_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE SET NULL,
            FOREIGN KEY(parent_occurrence_id,repo_id) REFERENCES entity_occurrences(id,repo_id),
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id,occurrence_id,operation,surface_kind,evidence_hash)
        );
        CREATE TABLE entity_extraction_coverage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            occurrence_id INTEGER NOT NULL, source_file_id INTEGER,
            source_path TEXT NOT NULL, extractor TEXT NOT NULL,
            extractor_version TEXT NOT NULL,
            declaration_family TEXT NOT NULL CHECK(declaration_family IN
                ('A','B','C','D','E','components','operations')), source_hash TEXT,
            status TEXT NOT NULL CHECK(status IN
                ('complete','partial','failed','not_applicable')),
            component_count INTEGER NOT NULL DEFAULT 0,
            fact_count INTEGER NOT NULL DEFAULT 0, diagnostic TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
            FOREIGN KEY(occurrence_id,repo_id) REFERENCES entity_occurrences(id,repo_id),
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id,occurrence_id,extractor,extractor_version,
                declaration_family,source_hash)
        );
        CREATE TABLE entity_semantic_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
            fact_key TEXT NOT NULL, left_fact_id INTEGER NOT NULL,
            right_fact_id INTEGER NOT NULL, conflict_kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('open','resolved','accepted_unresolved')),
            reason TEXT NOT NULL, resolution_evidence TEXT, source_file_id INTEGER,
            source_path TEXT, confidence REAL NOT NULL
                CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
            FOREIGN KEY(left_fact_id) REFERENCES entity_relationship_facts(id) ON DELETE CASCADE,
            FOREIGN KEY(left_fact_id,repo_id) REFERENCES entity_relationship_facts(id,repo_id),
            FOREIGN KEY(right_fact_id) REFERENCES entity_relationship_facts(id) ON DELETE CASCADE,
            FOREIGN KEY(right_fact_id,repo_id) REFERENCES entity_relationship_facts(id,repo_id),
            FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
            UNIQUE(repo_id,fact_key,left_fact_id,right_fact_id)
        )
    """
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _apply_entity_semantics_repo_scope_migration(conn: sqlite3.Connection) -> None:
    violations = _semantic_repo_scope_violations(conn)
    if violations:
        raise RuntimeError(
            "entity semantic repository ownership violations before migration 022: "
            f"{violations}"
        )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_occurrences_id_repo "
        "ON entity_occurrences(id,repo_id)"
    )
    for table in (
        "entity_semantic_conflicts",
        "entity_operation_facts",
        "entity_extraction_coverage",
        "entity_relationship_facts",
        "entity_schema_components",
    ):
        conn.execute(f"ALTER TABLE {table} RENAME TO _021_{table}")

    _create_repo_scoped_entity_semantics_tables(conn)
    # Composite child FKs require their parent keys before rows are copied.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_schema_components_id_repo "
        "ON entity_schema_components(id,repo_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_relationship_facts_id_repo "
        "ON entity_relationship_facts(id,repo_id)"
    )
    for table in (
        "entity_schema_components",
        "entity_relationship_facts",
        "entity_operation_facts",
        "entity_extraction_coverage",
        "entity_semantic_conflicts",
    ):
        columns = ",".join(
            row[1] for row in conn.execute(f"PRAGMA table_info({table})")
        )
        conn.execute(
            f"INSERT INTO {table}({columns}) SELECT {columns} FROM _021_{table}"
        )
    for table in (
        "entity_semantic_conflicts",
        "entity_operation_facts",
        "entity_extraction_coverage",
        "entity_relationship_facts",
        "entity_schema_components",
    ):
        conn.execute(f"DROP TABLE _021_{table}")

    index_script = """
        CREATE INDEX IF NOT EXISTS idx_entity_schema_components_occurrence
            ON entity_schema_components(repo_id,occurrence_id,component_kind);
        CREATE INDEX IF NOT EXISTS idx_entity_schema_components_source
            ON entity_schema_components(repo_id,source_path);
        CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_occurrence
            ON entity_relationship_facts(repo_id,source_occurrence_id,axis);
        CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_target
            ON entity_relationship_facts(repo_id,target_occurrence_id,axis);
        CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_status
            ON entity_relationship_facts(repo_id,assertion_status);
        CREATE INDEX IF NOT EXISTS idx_entity_operation_facts_occurrence
            ON entity_operation_facts(repo_id,occurrence_id,operation);
        CREATE INDEX IF NOT EXISTS idx_entity_extraction_coverage_occurrence
            ON entity_extraction_coverage(repo_id,occurrence_id,declaration_family);
        CREATE INDEX IF NOT EXISTS idx_entity_semantic_conflicts_fact_key
            ON entity_semantic_conflicts(repo_id,fact_key,status)
    """
    for statement in index_script.split(";"):
        if statement.strip():
            conn.execute(statement)

    violations = _semantic_repo_scope_violations(conn)
    if violations:
        raise RuntimeError(
            "entity semantic repository ownership violations after migration 022: "
            f"{violations}"
        )


def _entity_semantics_repo_scope_is_current(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "entity_relationship_facts"):
        return False
    groups: dict[int, set[str]] = {}
    for row in conn.execute("PRAGMA foreign_key_list(entity_relationship_facts)"):
        groups.setdefault(int(row[0]), set()).add(str(row[3]))
    required_indexes = {
        "uq_entity_occurrences_id_repo",
        "uq_entity_schema_components_id_repo",
        "uq_entity_relationship_facts_id_repo",
    }
    present_indexes = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    return {
        "source_occurrence_id",
        "repo_id",
    } in groups.values() and required_indexes.issubset(present_indexes)


def symbol_stable_key(
    *,
    kind: str | None,
    name: str | None,
    parent_symbol: str | None,
    signature: str | None,
    duplicate_ordinal: int,
) -> str:
    """Return the repository-extraction identity for one symbol.

    Line numbers are intentionally absent so IDs survive movement-only edits.
    The ordinal distinguishes identical extractor records in the same file.
    """

    payload = json.dumps(
        [
            kind or "",
            name or "",
            parent_symbol or "",
            signature or "",
            duplicate_ordinal,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _canonical_schema_objects(
    conn: sqlite3.Connection, tables: tuple[str, ...]
) -> None:
    """Install selected tables and their explicit indexes from schema.sql."""

    template = sqlite3.connect(":memory:")
    try:
        template.executescript((Path(__file__).with_name("schema.sql")).read_text())
        for table in tables:
            if _table_exists(conn, table):
                continue
            ddl = template.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if ddl is None:
                raise RuntimeError(f"canonical schema is missing {table}")
            conn.execute(str(ddl[0]))
            for (index_ddl,) in template.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            ):
                conn.execute(str(index_ddl))
    finally:
        template.close()


def _populate_symbol_stable_keys(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "symbols"):
        return
    _add_columns(conn, "symbols", ("stable_key TEXT",))
    columns = _columns(conn, "symbols")
    selected = ["id", "file_id", "kind", "name", "parent_symbol", "signature"]
    expressions = [
        column if column in columns else f"NULL AS {column}" for column in selected
    ]
    rows = conn.execute(
        "SELECT " + ",".join(expressions) + " FROM symbols ORDER BY file_id,id"
    ).fetchall()
    ordinals: dict[tuple[object, ...], int] = {}
    for row in rows:
        group = tuple(row[index] for index in range(1, 6))
        ordinal = ordinals.get(group, 0)
        ordinals[group] = ordinal + 1
        stable_key = symbol_stable_key(
            kind=row[2],
            name=row[3],
            parent_symbol=row[4],
            signature=row[5],
            duplicate_ordinal=ordinal,
        )
        conn.execute(
            "UPDATE symbols SET stable_key=? WHERE id=?", (stable_key, int(row[0]))
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_symbols_file_stable_key "
        "ON symbols(file_id,stable_key)"
    )


def _rebuild_graph_builds_023(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "graph_builds"):
        _canonical_schema_objects(conn, ("graph_builds",))
        return
    columns = _columns(conn, "graph_builds")
    if {
        "catalog_build_id",
        "base_graph_build_id",
        "build_mode",
        "projection_version",
        "source_revisions_json",
    }.issubset(columns):
        return
    conn.execute("DROP INDEX IF EXISTS idx_graph_builds_status_started")
    conn.execute("DROP INDEX IF EXISTS idx_graph_builds_catalog")
    conn.execute("DROP INDEX IF EXISTS uq_graph_builds_active_path")
    conn.execute("ALTER TABLE graph_builds RENAME TO graph_builds_legacy_023")
    _canonical_schema_objects(conn, ("graph_builds",))
    legacy_columns = _columns(conn, "graph_builds_legacy_023")

    def legacy(column: str, fallback: str = "NULL") -> str:
        return column if column in legacy_columns else fallback

    conn.execute(
        """INSERT INTO graph_builds(
               id,graph_path,source_db,status,source_fingerprint,build_mode,
               started_at,completed_at,validation_summary,error
           )
           SELECT id,graph_path,source_db,
                  CASE WHEN status IN ('building','validated','active','previous','failed')
                       THEN status ELSE 'failed' END,
                  COALESCE(source_fingerprint,'legacy-unknown'),'full',
                  started_at,completed_at,validation_summary,error
           FROM graph_builds_legacy_023"""
    )
    conn.execute("DROP TABLE graph_builds_legacy_023")


def _seed_baseline_catalog_build(conn: sqlite3.Connection) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM catalog_builds WHERE status='active' LIMIT 1"
        ).fetchone()
        is not None
    ):
        return
    from catalog.content_fingerprint import logical_content_fingerprint
    from catalog.delta import DELTA_CONTRACT_VERSION

    revisions = {
        str(row[0]): row[1]
        for row in conn.execute(
            "SELECT repo_key,indexed_commit_sha FROM repos ORDER BY repo_key"
        )
    }
    database_path = next(
        (
            str(row[2])
            for row in conn.execute("PRAGMA database_list")
            if row[1] == "main"
        ),
        "",
    )
    fingerprint = logical_content_fingerprint(conn)
    conn.execute(
        """INSERT INTO catalog_builds(
               build_token,catalog_path,requested_mode,effective_mode,status,
               source_revisions_json,delta_contract_version,content_fingerprint,
               completed_at,validation_summary
           ) VALUES (?,?, 'full','full','active',?,?,?,CURRENT_TIMESTAMP,?)""",
        (
            str(uuid.uuid4()),
            database_path or ":memory:",
            json.dumps(revisions, sort_keys=True, separators=(",", ":")),
            DELTA_CONTRACT_VERSION,
            fingerprint,
            json.dumps({"baseline": True}, separators=(",", ":")),
        ),
    )


def _apply_delta_refresh_migration(conn: sqlite3.Connection) -> None:
    _canonical_schema_objects(
        conn, ("catalog_builds", "repo_change_sets", "repo_changed_paths")
    )
    _add_columns(
        conn,
        "repo_index_stages",
        (
            "execution_mode TEXT CHECK(execution_mode IN ('full','delta','skipped'))",
            "invalidation_reason TEXT",
            "affected_record_count INTEGER CHECK(affected_record_count IS NULL OR affected_record_count >= 0)",
        ),
    )
    _populate_symbol_stable_keys(conn)
    _rebuild_graph_builds_023(conn)
    _seed_baseline_catalog_build(conn)


def _apply_refresh_contracts_migration(conn: sqlite3.Connection) -> None:
    """Widen failed-build modes while preserving generation and child IDs."""

    required = {
        "id",
        "build_token",
        "parent_catalog_build_id",
        "catalog_path",
        "requested_mode",
        "effective_mode",
        "status",
        "source_revisions_json",
        "manifest_hash",
        "builder_plan_hash",
        "delta_contract_version",
        "content_fingerprint",
        "started_at",
        "completed_at",
        "validation_summary",
        "diagnostic_error",
    }
    present = _columns(conn, "catalog_builds")
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(
            "refresh metadata unavailable: catalog_builds is missing core columns: "
            + ", ".join(missing)
        )

    conn.execute("DROP INDEX IF EXISTS uq_catalog_builds_active")
    conn.execute("DROP INDEX IF EXISTS idx_catalog_builds_status_started")
    prior_legacy = int(conn.execute("PRAGMA legacy_alter_table").fetchone()[0])
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        conn.execute("ALTER TABLE catalog_builds RENAME TO catalog_builds_legacy_024")
        _canonical_schema_objects(conn, ("catalog_builds",))
        columns = ",".join(
            row[1] for row in conn.execute("PRAGMA table_info(catalog_builds)")
        )
        conn.execute(
            f"INSERT INTO catalog_builds({columns}) "
            f"SELECT {columns} FROM catalog_builds_legacy_024"
        )
        from catalog.delta import DELTA_CONTRACT_VERSION

        conn.execute(
            """UPDATE catalog_builds SET delta_contract_version=?
               WHERE status='active' AND manifest_hash IS NULL
                 AND builder_plan_hash IS NULL
                 AND validation_summary='{"baseline":true}'""",
            (DELTA_CONTRACT_VERSION,),
        )
        conn.execute("DROP TABLE catalog_builds_legacy_024")
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={prior_legacy}")


def apply_delta_refresh_migration(conn: sqlite3.Connection) -> None:
    """Apply refresh migrations 023 and 024 to a repository-scoped catalog."""

    if conn.in_transaction:
        raise RuntimeError(
            "apply_delta_refresh_migration requires a connection without an open transaction"
        )
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name=?", (DELTA_REFRESH_MIGRATION,)
        ).fetchone()
        if applied is None:
            _apply_delta_refresh_migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (DELTA_REFRESH_MIGRATION,),
            )
        contracts_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name=?",
            (REFRESH_CONTRACTS_MIGRATION,),
        ).fetchone()
        if contracts_applied is None:
            _apply_refresh_contracts_migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (REFRESH_CONTRACTS_MIGRATION,),
            )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"foreign key check failed before {DELTA_REFRESH_MIGRATION} commit: {violations[:3]}"
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def apply_multi_repo_migration(
    conn: sqlite3.Connection, *, local_root: str, tracked_branch: str = "main"
) -> None:
    """Apply the 019 migration transactionally and preserve legacy file IDs.

    ``conn`` must not have an open transaction.  SQLite requires foreign key
    enforcement to be disabled before a referenced table can be rebuilt; it is
    restored before integrity validation and commit.
    """

    if conn.in_transaction:
        raise RuntimeError(
            "apply_multi_repo_migration requires a connection without an open transaction"
        )
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        _ensure_registry_columns(conn)
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (MULTI_REPO_MIGRATION,)
        ).fetchone()
        if applied is None:
            repo_id = _legacy_repo_id(
                conn, local_root=local_root, tracked_branch=tracked_branch
            )
            # Check before changing anything: populated parent/child families
            # cannot be rebuilt without a provenance-preserving migration.
            _reject_populated_unsafe_legacy_families(conn)
            _rebuild_files(conn, repo_id)
            _rebuild_empty_legacy_families(conn)
            _rebuild_workflows(conn, repo_id)
            _rebuild_entity_access_links(conn, repo_id)
            _add_repo_ownership(conn, repo_id)
            _create_multi_repo_tables(conn)
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (MULTI_REPO_MIGRATION,),
            )
        coverage_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?",
            (REST_AUTOMATION_COVERAGE_MIGRATION,),
        ).fetchone()
        if coverage_applied is None:
            _create_rest_automation_coverage_tables(conn)
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (REST_AUTOMATION_COVERAGE_MIGRATION,),
            )
        legacy = conn.execute(
            "SELECT id FROM repos WHERE repo_key = ?", (LEGACY_REPO_KEY,)
        ).fetchone()
        _ensure_entity_occurrences(conn, int(legacy[0]) if legacy is not None else None)
        semantics_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?",
            (ENTITY_SEMANTICS_MIGRATION,),
        ).fetchone()
        if semantics_applied is None:
            _create_entity_semantics_tables(conn)
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (ENTITY_SEMANTICS_MIGRATION,),
            )
        semantics_scope_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?",
            (ENTITY_SEMANTICS_REPO_SCOPE_MIGRATION,),
        ).fetchone()
        if semantics_scope_applied is None:
            if not _entity_semantics_repo_scope_is_current(conn):
                _apply_entity_semantics_repo_scope_migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (ENTITY_SEMANTICS_REPO_SCOPE_MIGRATION,),
            )
        delta_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?",
            (DELTA_REFRESH_MIGRATION,),
        ).fetchone()
        if delta_applied is None:
            _apply_delta_refresh_migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (DELTA_REFRESH_MIGRATION,),
            )
        contracts_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?",
            (REFRESH_CONTRACTS_MIGRATION,),
        ).fetchone()
        if contracts_applied is None:
            _apply_refresh_contracts_migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (REFRESH_CONTRACTS_MIGRATION,),
            )
        # FK enforcement is necessarily off while legacy parent tables are
        # rebuilt, but foreign_key_check still validates the candidate before
        # anything is made durable.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"foreign key check failed before {MULTI_REPO_MIGRATION} commit: "
                f"{violations[:3]}"
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"foreign key check failed after {MULTI_REPO_MIGRATION}: {violations[:3]}"
        )
