"""Deterministic logical fingerprints for authoritative catalog evidence.

The SQLite file itself contains build history and its own fingerprint, so a
physical file hash is necessarily self-referential.  This module instead hashes
canonical rows from an explicit registry of evidence tables.  Operational run,
change-set, migration, and graph-build metadata is deliberately excluded.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

CATALOG_CONTENT_VERSION = 2


@dataclass(frozen=True)
class EvidenceTable:
    name: str
    excluded_columns: frozenset[str] = frozenset()


_TIMESTAMP_COLUMNS = frozenset(
    {
        "created_at",
        "updated_at",
        "last_indexed",
        "last_symbols_extracted",
        "last_relationships_extracted",
        "last_scanned_at",
        "last_built_at",
        "last_attempted_at",
        "validated_at",
    }
)

# This registry is the content contract.  Adding a new authoritative evidence
# family requires an explicit entry and a CATALOG_CONTENT_VERSION decision.
_EVIDENCE_TABLE_NAMES = (
    "repos",
    "files",
    "symbols",
    "relationships",
    "entity_nodes",
    "entity_occurrences",
    "entity_mappings",
    "entity_roots",
    "workflows",
    "workflow_nodes",
    "workflow_edges",
    "openapi_file_ref_edges",
    "rest_endpoints",
    "knowledge_items",
    "openapispec_index",
    "api_version_compatibility",
    "security_operations",
    "security_operation_allowops",
    "security_policies",
    "security_policy_values",
    "security_policy_eops",
    "security_menus",
    "security_menu_items",
    "security_menu_op_links",
    "dbschema_tables",
    "dbschema_fields",
    "entity_access_links",
    "integration_links",
    "test_cases",
    "test_case_versions",
    "test_requests",
    "test_endpoint_links",
    "test_entity_links",
    "test_diagnostics",
    "entity_schema_components",
    "entity_relationship_facts",
    "entity_operation_facts",
    "entity_extraction_coverage",
    "entity_semantic_conflicts",
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
AUTHORITATIVE_EVIDENCE_TABLES: tuple[EvidenceTable, ...] = tuple(
    EvidenceTable(
        name,
        _TIMESTAMP_COLUMNS
        | (
            frozenset(
                {
                    "local_root",
                    "index_status",
                    "diagnostic_error",
                    "last_attempt_status",
                    "last_attempt_error",
                }
            )
            if name == "repos"
            else frozenset()
        ),
    )
    for name in _EVIDENCE_TABLE_NAMES
)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, float):
        return {"float": value.hex()}
    return value


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def logical_content_fingerprint(conn: sqlite3.Connection) -> str:
    """Return a stable SHA-256 over registered evidence rows.

    Rows are ordered by their canonical selected-column serialization rather
    than query-plan or physical row order.  Missing optional tables are encoded
    explicitly, which also makes fingerprints deterministic during migrations.
    """

    digest = hashlib.sha256()
    digest.update(f"catalog-content-v{CATALOG_CONTENT_VERSION}\n".encode())
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        for spec in AUTHORITATIVE_EVIDENCE_TABLES:
            digest.update(f"table:{spec.name}\n".encode())
            if not _table_exists(conn, spec.name):
                digest.update(b"<missing>\n")
                continue
            columns = [
                str(row[1])
                for row in conn.execute(f'PRAGMA table_info("{spec.name}")')
                if str(row[1]) not in spec.excluded_columns
            ]
            digest.update(
                json.dumps(columns, separators=(",", ":"), ensure_ascii=True).encode()
                + b"\n"
            )
            if not columns:
                continue
            quoted = ",".join(f'"{column}"' for column in columns)
            encoded_rows = []
            for row in conn.execute(f'SELECT {quoted} FROM "{spec.name}"'):
                encoded_rows.append(
                    json.dumps(
                        [_canonical_value(row[column]) for column in columns],
                        separators=(",", ":"),
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            for encoded in sorted(encoded_rows):
                digest.update(encoded.encode() + b"\n")
    finally:
        conn.row_factory = old_factory
    return digest.hexdigest()


def fingerprint_database(path: str) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return logical_content_fingerprint(conn)
    finally:
        conn.close()
