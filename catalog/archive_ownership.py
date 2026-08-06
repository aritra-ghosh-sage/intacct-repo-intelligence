"""Explicit ownership contract for repository archival.

This is deliberately a reviewed static list rather than a dynamic broad delete.
Schema preflight makes a newly introduced repository-owned table fail closed
until its archive behavior is explicitly classified here.
"""

from __future__ import annotations

import sqlite3


class ArchiveOwnershipError(RuntimeError):
    """The candidate schema has ownership evidence the archive cannot prove safe."""


# Every table with a direct ``repo_id`` column except the retained repository
# identity itself.  Child-only evidence is removed by the foreign-key cascades
# rooted in these tables.  Keep the list ordered so it doubles as the deletion
# plan and code-review inventory.
ARCHIVE_OWNED_REPO_TABLES: tuple[str, ...] = (
    "api_registry_entries",
    "api_registry_entry_links",
    "api_registry_issues",
    "api_version_compatibility",
    "dbschema_tables",
    "entity_access_links",
    "entity_extraction_coverage",
    "entity_mappings",
    "entity_occurrences",
    "entity_operation_facts",
    "entity_relationship_facts",
    "entity_roots",
    "entity_schema_components",
    "entity_semantic_conflicts",
    "files",
    "openapi_file_ref_edges",
    "openapispec_index",
    "relationships",
    "repo_builder_hydrations",
    "repo_change_sets",
    "repo_error_bundle_carry_forwards",
    "repo_error_bundles",
    "repo_evidence_fingerprints",
    "repo_index_runs",
    "repo_stale_evidence",
    "rest_endpoints",
    "security_menus",
    "security_operations",
    "security_policies",
    "test_cases",
    "test_coverage_build_state",
    "test_diagnostics",
    "ui_artifact_includes",
    "ui_artifacts",
    "ui_entity_references",
    "ui_event_calls",
    "ui_events",
    "ui_fields",
    "ui_resolution_issues",
    "ui_script_dependencies",
    "ui_surfaces",
    "ui_source_diagnostics",
    "workflows",
)

# Repositories are retained as lifecycle/admin identities.  No other direct
# repository ownership is implicit.
RETAINED_REPO_TABLES = frozenset({"repos"})


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def validate_archive_ownership_schema(conn: sqlite3.Connection) -> None:
    """Fail closed if this known schema cannot be archived with this registry."""

    tables = _tables(conn)
    missing = sorted(set(ARCHIVE_OWNED_REPO_TABLES) - tables)
    if missing:
        raise ArchiveOwnershipError("archive ownership tables missing: " + ", ".join(missing))
    direct_repo_tables = {
        table for table in tables if "repo_id" in _columns(conn, table)
    }
    known = set(ARCHIVE_OWNED_REPO_TABLES) | set(RETAINED_REPO_TABLES)
    unknown = sorted(direct_repo_tables - known)
    if unknown:
        raise ArchiveOwnershipError(
            "unclassified direct repository evidence tables: " + ", ".join(unknown)
        )
    invalid = sorted(
        table
        for table in ARCHIVE_OWNED_REPO_TABLES
        if "repo_id" not in _columns(conn, table)
    )
    if invalid:
        raise ArchiveOwnershipError(
            "archive ownership tables lost repo_id: " + ", ".join(invalid)
        )


def target_row_counts(conn: sqlite3.Connection, repo_id: int) -> dict[str, int]:
    validate_archive_ownership_schema(conn)
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE repo_id=?", (repo_id,)).fetchone()[0])
        for table in ARCHIVE_OWNED_REPO_TABLES
    }


def assert_target_evidence_absent(conn: sqlite3.Connection, repo_id: int) -> None:
    residual = {name: count for name, count in target_row_counts(conn, repo_id).items() if count}
    if residual:
        rendered = ", ".join(f"{table}={count}" for table, count in sorted(residual.items()))
        raise ArchiveOwnershipError("archive candidate retains target evidence: " + rendered)


def purge_target_owned_evidence(conn: sqlite3.Connection, repo_id: int) -> dict[str, int]:
    """Remove the target's direct evidence rows; FK cascades remove children."""

    before = target_row_counts(conn, repo_id)
    # Delete rows that point at other target roots before their parents.  The
    # direct row ownership predicate ensures an active row is never selected.
    for table in ARCHIVE_OWNED_REPO_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE repo_id=?", (repo_id,))
    assert_target_evidence_absent(conn, repo_id)
    return {table: count for table, count in before.items() if count}


def target_entity_ids(conn: sqlite3.Connection, repo_id: int) -> set[int]:
    return {
        int(row[0])
        for row in conn.execute(
            "SELECT DISTINCT entity_id FROM entity_occurrences WHERE repo_id=?", (repo_id,)
        )
    }
