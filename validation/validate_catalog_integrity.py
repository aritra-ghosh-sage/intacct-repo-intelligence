"""Read-only structural and generation-state validation for catalog SQLite DBs."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from catalog.content_fingerprint import logical_content_fingerprint
from catalog.delta import DELTA_CONTRACT_VERSION
from catalog.refresh_quality import (
    RefreshQualityError,
    resolve_reference_quality_run,
    validate_quality_run,
)
from catalog.source_revisions import active_source_revisions


class CatalogIntegrityError(RuntimeError):
    """The catalog cannot be promoted or trusted as an active generation."""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def _logical_orphan_checks(conn: sqlite3.Connection) -> dict[str, int]:
    checks = {
        "entity_roots_without_mapping": """
            SELECT COUNT(*) FROM entity_roots er
            LEFT JOIN entity_mappings em
              ON em.repo_id=er.repo_id AND em.entity_id=er.entity_id
             AND em.symbol_id=er.symbol_id
            WHERE em.id IS NULL
        """,
        "workflow_nodes_without_workflow": """
            SELECT COUNT(*) FROM workflow_nodes wn
            LEFT JOIN workflows w ON w.id=wn.workflow_id WHERE w.id IS NULL
        """,
        "workflow_edges_without_nodes": """
            SELECT COUNT(*) FROM workflow_edges we
            LEFT JOIN workflow_nodes src ON src.id=we.from_node_id
            LEFT JOIN workflow_nodes dst ON dst.id=we.to_node_id
            WHERE src.id IS NULL OR dst.id IS NULL
        """,
        "dbschema_fields_without_table": """
            SELECT COUNT(*) FROM dbschema_fields df
            LEFT JOIN dbschema_tables dt ON dt.id=df.dbschema_table_id
            WHERE dt.id IS NULL
        """,
        "security_values_without_policy": """
            SELECT COUNT(*) FROM security_policy_values spv
            LEFT JOIN security_policies sp ON sp.id=spv.policy_id WHERE sp.id IS NULL
        """,
        "security_menu_items_without_menu": """
            SELECT COUNT(*) FROM security_menu_items smi
            LEFT JOIN security_menus sm ON sm.id=smi.menu_id WHERE sm.id IS NULL
        """,
        "entity_components_without_occurrence": """
            SELECT COUNT(*) FROM entity_schema_components esc
            LEFT JOIN entity_occurrences eo ON eo.id=esc.occurrence_id
            WHERE eo.id IS NULL
        """,
        "entity_relationship_facts_without_source": """
            SELECT COUNT(*) FROM entity_relationship_facts erf
            LEFT JOIN entity_occurrences eo ON eo.id=erf.source_occurrence_id
            WHERE eo.id IS NULL
        """,
        "entity_operation_facts_without_occurrence": """
            SELECT COUNT(*) FROM entity_operation_facts eof
            LEFT JOIN entity_occurrences eo ON eo.id=eof.occurrence_id
            WHERE eo.id IS NULL
        """,
        "entity_access_links_without_typed_target": """
            SELECT COUNT(*) FROM entity_access_links eal
            WHERE (surface='workflow' AND NOT EXISTS (
                       SELECT 1 FROM workflows w WHERE w.id=eal.record_id))
               OR (surface='rest_endpoint' AND NOT EXISTS (
                       SELECT 1 FROM rest_endpoints re WHERE re.id=eal.record_id))
               OR (surface IN ('security_operation','security_resource') AND NOT EXISTS (
                       SELECT 1 FROM security_operations so WHERE so.id=eal.record_id))
               OR (surface='security_policy' AND NOT EXISTS (
                       SELECT 1 FROM security_policies sp WHERE sp.id=eal.record_id))
               OR (surface='security_menu' AND NOT EXISTS (
                       SELECT 1 FROM security_menus sm WHERE sm.id=eal.record_id))
               OR (surface='security_menu_item' AND NOT EXISTS (
                       SELECT 1 FROM security_menu_items smi WHERE smi.id=eal.record_id))
               OR (surface='dbschema_table' AND NOT EXISTS (
                       SELECT 1 FROM dbschema_tables dt WHERE dt.id=eal.record_id))
        """,
        "api_registry_links_without_entry": """
            SELECT COUNT(*) FROM api_registry_entry_links link
            LEFT JOIN api_registry_entries entry
              ON entry.id=link.entry_id AND entry.repo_id=link.repo_id
            WHERE entry.id IS NULL
        """,
        "api_registry_links_without_source_file": """
            SELECT COUNT(*) FROM api_registry_entry_links link
            LEFT JOIN files file
              ON file.id=link.source_file_id AND file.repo_id=link.repo_id
            WHERE file.id IS NULL
        """,
        "api_registry_entries_without_registry_file": """
            SELECT COUNT(*) FROM api_registry_entries entry
            LEFT JOIN files file
              ON file.id=entry.registry_file_id AND file.repo_id=entry.repo_id
            WHERE file.id IS NULL
        """,
        "ui_source_diagnostics_without_source_file": """
            SELECT COUNT(*) FROM ui_source_diagnostics diagnostic
            LEFT JOIN files file
              ON file.id=diagnostic.source_file_id AND file.repo_id=diagnostic.repo_id
            WHERE file.id IS NULL
        """,
        "repo_scoped_file_ownership_mismatches": """
            SELECT COUNT(*) FROM (
                SELECT so.id FROM security_operations so JOIN files f ON f.id=so.file_id
                 WHERE so.file_id IS NOT NULL AND so.repo_id<>f.repo_id
                UNION ALL
                SELECT sp.id FROM security_policies sp JOIN files f ON f.id=sp.file_id
                 WHERE sp.file_id IS NOT NULL AND sp.repo_id<>f.repo_id
                UNION ALL
                SELECT sm.id FROM security_menus sm JOIN files f ON f.id=sm.file_id
                 WHERE sm.file_id IS NOT NULL AND sm.repo_id<>f.repo_id
                UNION ALL
                SELECT dt.id FROM dbschema_tables dt JOIN files f ON f.id=dt.file_id
                 WHERE dt.file_id IS NOT NULL AND dt.repo_id<>f.repo_id
                UNION ALL
                SELECT eo.id FROM entity_occurrences eo JOIN files f ON f.id=eo.source_file_id
                 WHERE eo.source_file_id IS NOT NULL AND eo.repo_id<>f.repo_id
            )
        """,
    }
    results: dict[str, int] = {}
    for name, sql in checks.items():
        try:
            results[name] = _count(conn, sql)
        except sqlite3.OperationalError:
            # The validator also supports legacy fixtures; migration presence is
            # reported separately and makes missing refresh metadata a failure.
            results[name] = -1
    return results


def _active_contract_quality_run_ids(conn: sqlite3.Connection) -> set[int]:
    """Return runs linked to the active generation of the current contract."""
    required_tables = ("catalog_builds", "repo_change_sets", "repo_index_runs")
    if not all(_table_exists(conn, table) for table in required_tables):
        return set()
    build = conn.execute(
        """SELECT id FROM catalog_builds
           WHERE status='active' AND delta_contract_version=?
           ORDER BY id DESC LIMIT 1""",
        (DELTA_CONTRACT_VERSION,),
    ).fetchone()
    if build is None:
        return set()
    return {
        int(row[0])
        for row in conn.execute(
            """SELECT DISTINCT rcs.repo_index_run_id
               FROM repo_change_sets rcs
               JOIN repo_index_runs rir ON rir.id=rcs.repo_index_run_id
               WHERE rcs.catalog_build_id=? AND rir.status='active'""",
            (int(build[0]),),
        )
    }


def _invalid_active_quality_runs(
    conn: sqlite3.Connection,
    required_quality_run_ids: set[int] | None = None,
) -> int:
    if not _table_exists(conn, "repo_index_runs"):
        return 0
    required_ids = (
        _active_contract_quality_run_ids(conn)
        if required_quality_run_ids is None
        else set(required_quality_run_ids)
    )
    invalid = 0
    rows = conn.execute(
        """SELECT id,repo_id,validation_summary FROM repo_index_runs
           WHERE status='active' ORDER BY id"""
    ).fetchall()
    active_ids = {int(row[0]) for row in rows}
    invalid = len(required_ids - active_ids)
    for run_id, repo_id, raw_summary in rows:
        if int(run_id) not in required_ids:
            continue
        if raw_summary is None:
            invalid += 1
            continue
        try:
            summary = json.loads(str(raw_summary))
            validate_quality_run(summary)
            if summary["kind"] == "reference":
                resolve_reference_quality_run(conn, int(repo_id), int(run_id), summary)
        except (TypeError, ValueError, json.JSONDecodeError, RefreshQualityError):
            invalid += 1
    return invalid


def validate_catalog_connection(
    conn: sqlite3.Connection,
    *,
    expected_catalog_build_id: int | None = None,
    required_quality_run_ids: set[int] | None = None,
) -> dict[str, Any]:
    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    fk_rows = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    migration_present = bool(
        _table_exists(conn, "schema_migrations")
        and conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name='023_delta_refresh'"
        ).fetchone()
    )
    refresh_contract_migration_present = bool(
        _table_exists(conn, "schema_migrations")
        and conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name='024_refresh_contracts'"
        ).fetchone()
    )
    hardening_migration_present = bool(
        _table_exists(conn, "schema_migrations")
        and conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name='025_delta_refresh_hardening'"
        ).fetchone()
    )
    api_registry_migration_present = bool(
        _table_exists(conn, "schema_migrations")
        and conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name='028_api_registry'"
        ).fetchone()
    )
    api_registry_tables_present = all(
        _table_exists(conn, table)
        for table in (
            "api_registry_entries",
            "api_registry_entry_links",
            "api_registry_issues",
            "ui_source_diagnostics",
        )
    )
    archival_migration_present = bool(
        _table_exists(conn, "schema_migrations")
        and conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name='029_repository_archival'"
        ).fetchone()
    )
    integration_link_rows = (
        _count(conn, "SELECT COUNT(*) FROM integration_links")
        if _table_exists(conn, "integration_links")
        else 0
    )
    invalid_active_quality_runs = _invalid_active_quality_runs(
        conn, required_quality_run_ids
    )
    active_rows = (
        conn.execute(
            "SELECT id,content_fingerprint,source_revisions_json,completed_at,diagnostic_error "
            "FROM catalog_builds WHERE status='active' ORDER BY id"
        ).fetchall()
        if _table_exists(conn, "catalog_builds")
        else []
    )
    orphan_counts = _logical_orphan_checks(conn)
    stable_key_missing = _count(
        conn, "SELECT COUNT(*) FROM symbols WHERE COALESCE(stable_key,'')=''"
    )
    stable_key_duplicates = _count(
        conn,
        "SELECT COUNT(*) FROM (SELECT file_id,stable_key FROM symbols "
        "GROUP BY file_id,stable_key HAVING COUNT(*)>1)",
    )
    in_progress = {
        "catalog_builds": _count(
            conn,
            "SELECT COUNT(*) FROM catalog_builds WHERE status IN ('building','validated')",
        ),
        "repo_index_runs": _count(
            conn,
            "SELECT COUNT(*) FROM repo_index_runs WHERE status IN ('building','validated')",
        ),
        "repo_change_sets": _count(
            conn,
            "SELECT COUNT(*) FROM repo_change_sets WHERE status IN ('planned','running')",
        ),
    }

    active_id = int(active_rows[0][0]) if len(active_rows) == 1 else None
    stored_fingerprint = active_rows[0][1] if len(active_rows) == 1 else None
    actual_fingerprint = logical_content_fingerprint(conn)
    revisions_valid = False
    revision_mismatches: list[str] = []
    if len(active_rows) == 1:
        try:
            revisions = json.loads(str(active_rows[0][2]))
            revisions_valid = isinstance(revisions, dict)
        except (TypeError, ValueError, json.JSONDecodeError):
            revisions = {}
        if revisions_valid:
            expected_revisions = active_source_revisions(conn)
            revision_mismatches = sorted(
                set(revisions).symmetric_difference(expected_revisions)
                | {
                    repo_key
                    for repo_key in set(revisions).intersection(expected_revisions)
                    if revisions[repo_key] != expected_revisions[repo_key]
                }
            )

    active_change_set_failures = 0
    if active_id is not None:
        active_change_set_failures = _count(
            conn,
            "SELECT COUNT(*) FROM repo_change_sets rcs "
            "JOIN repo_index_runs rir ON rir.id=rcs.repo_index_run_id "
            f"WHERE rcs.catalog_build_id={active_id} "
            "AND (rcs.status<>'succeeded' OR rir.status<>'active')",
        )

    summary: dict[str, Any] = {
        "integrity_check": integrity,
        "foreign_key_violations": len(fk_rows),
        "foreign_key_sample": fk_rows[:5],
        "migration_023_present": migration_present,
        "migration_024_present": refresh_contract_migration_present,
        "migration_025_present": hardening_migration_present,
        "migration_028_present": api_registry_migration_present,
        "api_registry_tables_present": api_registry_tables_present,
        "migration_029_present": archival_migration_present,
        "integration_link_rows": integration_link_rows,
        "invalid_active_quality_runs": invalid_active_quality_runs,
        "active_catalog_build_count": len(active_rows),
        "active_catalog_build_id": active_id,
        "expected_catalog_build_id": expected_catalog_build_id,
        "content_fingerprint_matches": bool(
            stored_fingerprint and stored_fingerprint == actual_fingerprint
        ),
        "source_revisions_valid": revisions_valid,
        "source_revision_mismatches": revision_mismatches,
        "active_change_set_failures": active_change_set_failures,
        "stable_key_missing": stable_key_missing,
        "stable_key_duplicates": stable_key_duplicates,
        "in_progress": in_progress,
        "logical_orphans": orphan_counts,
    }
    failures = []
    if integrity != "ok":
        failures.append("integrity_check")
    if fk_rows:
        failures.append("foreign_key_check")
    if not migration_present:
        failures.append("migration_023")
    if not refresh_contract_migration_present:
        failures.append("migration_024")
    if not hardening_migration_present:
        failures.append("migration_025")
    if not api_registry_migration_present:
        failures.append("migration_028")
    if not api_registry_tables_present:
        failures.append("api_registry_tables")
    if not archival_migration_present:
        failures.append("migration_029")
    if integration_link_rows:
        failures.append("integration_links")
    if invalid_active_quality_runs:
        failures.append("quality_runs")
    if len(active_rows) != 1:
        failures.append("active_catalog_build_count")
    elif (
        expected_catalog_build_id is not None and active_id != expected_catalog_build_id
    ):
        failures.append("active_catalog_build_id")
    if len(active_rows) == 1 and (
        active_rows[0][3] is None or active_rows[0][4] is not None
    ):
        failures.append("active_catalog_completion")
    if not summary["content_fingerprint_matches"]:
        failures.append("content_fingerprint")
    if not revisions_valid or revision_mismatches:
        failures.append("source_revisions")
    if active_change_set_failures:
        failures.append("active_change_sets")
    if stable_key_missing or stable_key_duplicates:
        failures.append("symbol_stable_keys")
    if any(in_progress.values()):
        failures.append("in_progress_state")
    if any(value != 0 for value in orphan_counts.values()):
        failures.append("logical_orphans")
    summary["ok"] = not failures
    summary["failures"] = failures
    if failures:
        raise CatalogIntegrityError(json.dumps(summary, sort_keys=True))
    return summary


def validate_catalog_path(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    try:
        return validate_catalog_connection(conn)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="catalog/catalog.db")
    args = parser.parse_args()
    try:
        summary = validate_catalog_path(args.db)
    except CatalogIntegrityError as exc:
        print(str(exc))
        raise SystemExit(1) from exc
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
