"""Catalog migrations that need SQLite table reconstruction.

SQL-only migrations are retained as operator-visible markers in ``migrations``.
This module owns the one migration that must preserve existing ``files.id``
values while changing the legacy global path uniqueness constraint.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path


MULTI_REPO_MIGRATION = "019_multi_repo"
REST_AUTOMATION_COVERAGE_MIGRATION = "020_rest_automation_coverage"
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
