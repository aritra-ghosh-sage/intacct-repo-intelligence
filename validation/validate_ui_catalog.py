"""Read-only integrity validation for source-provenanced UI catalog evidence."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any


class UiCatalogValidationError(RuntimeError):
    """The UI evidence is structurally invalid or violates the requested policy."""


UI_TABLES = (
    "ui_surfaces",
    "ui_artifacts",
    "ui_entity_references",
    "ui_artifact_includes",
    "ui_fields",
    "ui_events",
    "ui_script_dependencies",
    "ui_event_calls",
    "ui_resolution_issues",
)

NATURAL_KEY_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ui_surfaces", ("repo_id", "surface_key")),
    ("ui_artifacts", ("repo_id", "surface_id", "artifact_key")),
    (
        "ui_entity_references",
        (
            "repo_id",
            "surface_id",
            "entity_occurrence_id",
            "evidence_artifact_id",
            "reference_kind",
        ),
    ),
    ("ui_artifact_includes", ("repo_id", "source_artifact_id", "include_key")),
    ("ui_fields", ("repo_id", "artifact_id", "field_key")),
    ("ui_events", ("repo_id", "artifact_id", "event_key")),
    (
        "ui_script_dependencies",
        ("repo_id", "surface_id", "source_artifact_id", "dependency_key"),
    ),
    ("ui_event_calls", ("repo_id", "event_id", "dependency_id", "call_key")),
    ("ui_resolution_issues", ("repo_id", "surface_id", "issue_key")),
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def _relative_key(value: str) -> bool:
    if (
        not value
        or "\\" in value
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
    ):
        return False
    path = PurePosixPath(value)
    return all(part not in {"", ".", ".."} for part in path.parts)


def _surface_key_is_valid(surface_key: str, surface_kind: str, source_path: str | None) -> bool:
    if surface_kind == "actionui_form":
        prefix = "actionui:"
        if not surface_key.startswith(prefix):
            return False
        xml_path = surface_key.removeprefix(prefix)
        return (
            _relative_key(xml_path)
            and xml_path.endswith("_form.xml")
            and source_path == xml_path
        )
    if surface_kind == "nextgen":
        prefix = "nextgen:"
        if not surface_key.startswith(prefix):
            return False
        family_key = surface_key.removeprefix(prefix)
        return _relative_key(family_key) and "/" in family_key
    return False


def _invalid_surface_keys(conn: sqlite3.Connection) -> int:
    return sum(
        not _surface_key_is_valid(
            str(row["surface_key"]), str(row["surface_kind"]), row["source_path"]
        )
        for row in conn.execute(
            "SELECT surface_key,surface_kind,source_path FROM ui_surfaces"
        )
    )


def duplicate_natural_key_groups(
    conn: sqlite3.Connection, table: str, columns: Sequence[str]
) -> int:
    """Count duplicate groups for one declared stable natural key."""

    if (
        not table.replace("_", "").isalnum()
        or not columns
        or any(not column.replace("_", "").isalnum() for column in columns)
    ):
        raise ValueError("natural-key table and columns must be simple SQL identifiers")
    column_list = ", ".join(columns)
    return _count(
        conn,
        f"SELECT COUNT(*) FROM (SELECT {column_list} FROM {table} "
        f"GROUP BY {column_list} HAVING COUNT(*) > 1)",
    )


def _ownership_checks(conn: sqlite3.Connection) -> dict[str, int]:
    checks = {
        "surfaces_missing_provenance": """
            SELECT COUNT(*) FROM ui_surfaces
            WHERE source_file_id IS NULL OR COALESCE(TRIM(source_path), '') = ''
        """,
        "surface_file_repo_mismatches": """
            SELECT COUNT(*) FROM ui_surfaces surface
            LEFT JOIN files file ON file.id=surface.source_file_id
            WHERE file.id IS NULL OR file.repo_id<>surface.repo_id
               OR surface.source_path<>file.path
        """,
        "surfaces_without_source_artifact": """
            SELECT COUNT(*) FROM ui_surfaces surface
            WHERE NOT EXISTS (
                SELECT 1 FROM ui_artifacts artifact
                WHERE artifact.repo_id=surface.repo_id
                  AND artifact.surface_id=surface.id
                  AND artifact.file_id=surface.source_file_id
            )
        """,
        "artifact_file_provenance_mismatches": """
            SELECT COUNT(*) FROM ui_artifacts artifact
            LEFT JOIN files file ON file.id=artifact.file_id
            WHERE file.id IS NULL OR file.repo_id<>artifact.repo_id
               OR artifact.source_path<>file.path
               OR COALESCE(TRIM(artifact.artifact_key), '') = ''
        """,
        "entity_reference_ownership_mismatches": """
            SELECT COUNT(*) FROM ui_entity_references reference
            LEFT JOIN entity_occurrences occurrence ON occurrence.id=reference.entity_occurrence_id
            LEFT JOIN ui_artifacts artifact ON artifact.id=reference.evidence_artifact_id
            WHERE occurrence.id IS NULL OR occurrence.repo_id<>reference.repo_id
               OR occurrence.entity_id<>reference.entity_id
               OR artifact.id IS NULL OR artifact.repo_id<>reference.repo_id
               OR artifact.surface_id<>reference.surface_id
        """,
        "artifact_record_ownership_mismatches": """
            SELECT COUNT(*) FROM (
                SELECT field.id FROM ui_fields field
                LEFT JOIN ui_artifacts artifact ON artifact.id=field.artifact_id
                WHERE artifact.id IS NULL OR artifact.repo_id<>field.repo_id
                UNION ALL
                SELECT event.id FROM ui_events event
                LEFT JOIN ui_artifacts artifact ON artifact.id=event.artifact_id
                WHERE artifact.id IS NULL OR artifact.repo_id<>event.repo_id
            )
        """,
        "include_provenance_mismatches": """
            SELECT COUNT(*) FROM ui_artifact_includes include
            LEFT JOIN ui_artifacts source_artifact ON source_artifact.id=include.source_artifact_id
            LEFT JOIN ui_artifacts target_artifact ON target_artifact.id=include.target_artifact_id
            WHERE source_artifact.id IS NULL OR source_artifact.repo_id<>include.repo_id
               OR (include.target_artifact_id IS NOT NULL
                   AND (target_artifact.id IS NULL OR target_artifact.repo_id<>include.repo_id))
               OR (include.resolution_status='resolved'
                   AND (include.target_artifact_id IS NULL
                        OR include.resolved_path IS NULL
                        OR include.resolved_path<>target_artifact.source_path))
        """,
        "script_dependency_surface_mismatches": """
            SELECT COUNT(*) FROM ui_script_dependencies dependency
            LEFT JOIN ui_artifacts artifact ON artifact.id=dependency.source_artifact_id
            LEFT JOIN files target_file ON target_file.id=dependency.target_file_id
            WHERE artifact.id IS NULL OR artifact.repo_id<>dependency.repo_id
               OR artifact.surface_id<>dependency.surface_id
               OR (dependency.target_file_id IS NOT NULL
                   AND (target_file.id IS NULL OR target_file.repo_id<>dependency.repo_id))
               OR (dependency.resolution_status='resolved'
                   AND (dependency.target_file_id IS NULL
                        OR dependency.script_path IS NULL
                        OR dependency.script_path<>target_file.path))
        """,
        "event_call_ownership_mismatches": """
            SELECT COUNT(*) FROM ui_event_calls call
            LEFT JOIN ui_events event ON event.id=call.event_id
            LEFT JOIN ui_artifacts event_artifact ON event_artifact.id=event.artifact_id
            LEFT JOIN ui_script_dependencies dependency ON dependency.id=call.dependency_id
            LEFT JOIN symbols symbol ON symbol.id=call.handler_symbol_id
            LEFT JOIN files symbol_file ON symbol_file.id=symbol.file_id
            WHERE event.id IS NULL
               OR event.repo_id<>call.repo_id
               OR (call.dependency_id IS NOT NULL
                   AND (dependency.id IS NULL OR dependency.repo_id<>call.repo_id
                        OR event_artifact.surface_id<>dependency.surface_id))
               OR (call.handler_symbol_id IS NOT NULL
                   AND (symbol.id IS NULL OR symbol_file.repo_id<>call.repo_id
                        OR call.dependency_id IS NULL
                        OR symbol.file_id<>dependency.target_file_id))
               OR (call.resolution_status='resolved'
                   AND (call.dependency_id IS NULL OR call.handler_symbol_id IS NULL
                        OR symbol.file_id<>dependency.target_file_id))
        """,
        "issue_surface_ownership_mismatches": """
            SELECT COUNT(*) FROM ui_resolution_issues issue
            LEFT JOIN ui_artifacts artifact ON artifact.id=issue.artifact_id
            LEFT JOIN ui_events event ON event.id=issue.event_id
            LEFT JOIN ui_artifacts event_artifact ON event_artifact.id=event.artifact_id
            LEFT JOIN ui_script_dependencies dependency ON dependency.id=issue.dependency_id
            WHERE (issue.artifact_id IS NOT NULL
                   AND (artifact.id IS NULL OR artifact.repo_id<>issue.repo_id
                        OR artifact.surface_id<>issue.surface_id))
               OR (issue.event_id IS NOT NULL
                   AND (event.id IS NULL OR event.repo_id<>issue.repo_id
                        OR event_artifact.surface_id<>issue.surface_id))
               OR (issue.dependency_id IS NOT NULL
                   AND (dependency.id IS NULL OR dependency.repo_id<>issue.repo_id
                        OR dependency.surface_id<>issue.surface_id))
               OR COALESCE(TRIM(issue.issue_key), '') = ''
        """,
    }
    return {name: _count(conn, sql) for name, sql in checks.items()}


def _strict_resolution_issue_count(conn: sqlite3.Connection) -> int:
    return _count(
        conn,
        """
        SELECT COUNT(*) FROM ui_resolution_issues
        WHERE LOWER(issue_code) LIKE '%unresolved%'
           OR LOWER(issue_code) LIKE '%ambiguous%'
        """,
    )


def validate_ui_catalog_connection(
    conn: sqlite3.Connection, *, strict_resolution: bool = False
) -> dict[str, Any]:
    """Validate UI evidence without mutating the catalog.

    Error-severity extraction issues are always blocking. Unresolved and
    ambiguous issues remain evidence in normal mode and become failures only
    under ``strict_resolution``.
    """

    missing_tables = [table for table in UI_TABLES if not _table_exists(conn, table)]
    if missing_tables:
        summary: dict[str, Any] = {
            "ok": False,
            "strict_resolution": strict_resolution,
            "missing_tables": missing_tables,
            "failures": ["missing_ui_tables"],
        }
        raise UiCatalogValidationError(json.dumps(summary, sort_keys=True))

    foreign_key_rows = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    ownership = _ownership_checks(conn)
    duplicate_keys = {
        table: duplicate_natural_key_groups(conn, table, columns)
        for table, columns in NATURAL_KEY_SPECS
    }
    blocking_issues = _count(
        conn, "SELECT COUNT(*) FROM ui_resolution_issues WHERE severity='error'"
    )
    strict_issues = _strict_resolution_issue_count(conn)

    summary = {
        "strict_resolution": strict_resolution,
        "foreign_key_violations": len(foreign_key_rows),
        "foreign_key_sample": foreign_key_rows[:5],
        "invalid_surface_keys": _invalid_surface_keys(conn),
        "ownership": ownership,
        "duplicate_natural_key_groups": duplicate_keys,
        "blocking_resolution_issues": blocking_issues,
        "strict_resolution_issues": strict_issues,
    }
    failures: list[str] = []
    if foreign_key_rows:
        failures.append("foreign_key_check")
    if summary["invalid_surface_keys"]:
        failures.append("surface_keys")
    if any(ownership.values()):
        failures.append("repo_ownership_or_provenance")
    if any(duplicate_keys.values()):
        failures.append("stable_natural_keys")
    if blocking_issues:
        failures.append("blocking_resolution_issues")
    if strict_resolution and strict_issues:
        failures.append("strict_resolution_issues")
    summary["failures"] = failures
    summary["ok"] = not failures
    if failures:
        raise UiCatalogValidationError(json.dumps(summary, sort_keys=True))
    return summary


def validate_ui_catalog_path(
    path: str | Path, *, strict_resolution: bool = False
) -> dict[str, Any]:
    resolved = Path(path).resolve()
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return validate_ui_catalog_connection(conn, strict_resolution=strict_resolution)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="catalog/catalog.db")
    parser.add_argument(
        "--strict-resolution",
        action="store_true",
        help="Fail on unresolved or ambiguous UI resolution issues.",
    )
    args = parser.parse_args()
    try:
        summary = validate_ui_catalog_path(
            args.db, strict_resolution=args.strict_resolution
        )
    except UiCatalogValidationError as exc:
        print(str(exc))
        raise SystemExit(1) from exc
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
