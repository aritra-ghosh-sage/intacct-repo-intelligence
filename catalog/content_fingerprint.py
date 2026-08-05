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
from collections import Counter
from dataclasses import dataclass
from typing import Any

# Version four deliberately identifies evidence by normalized relational
# content, rather than SQLite allocation order.  Delta and full candidates are
# allowed to allocate different surrogate IDs.
CATALOG_CONTENT_VERSION = 4


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
        "archived_at",
        "last_modified",
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
    "api_registry_entries",
    "api_registry_entry_links",
    "api_registry_issues",
    "ui_source_diagnostics",
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


class NormalizedEvidenceError(RuntimeError):
    """A stable evidence projection cannot be proven from the SQLite schema."""


def normalized_evidence_projection(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """Project authoritative evidence independent of surrogate SQLite IDs.

    Foreign keys to another authoritative table are rewritten to deterministic
    row colours.  Colour refinement is driven to a fixed point (not an
    arbitrary round limit), so it is invariant to SQLite allocation order even
    for cyclic evidence.  A non-unique colour used as a FK target is rejected
    rather than guessed.
    """

    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        specs = {spec.name: spec for spec in AUTHORITATIVE_EVIDENCE_TABLES}
        rows: dict[str, dict[int, dict[str, Any]]] = {}
        foreign_keys: dict[tuple[str, str], str] = {}
        for name, spec in specs.items():
            if not _table_exists(conn, name):
                rows[name] = {}
                continue
            info = list(conn.execute(f'PRAGMA table_info("{name}")'))
            id_columns = [str(column[1]) for column in info if int(column[5]) and str(column[1]) == "id"]
            if id_columns != ["id"]:
                raise NormalizedEvidenceError(f"{name} has no single surrogate id")
            selected = [str(column[1]) for column in info if str(column[1]) not in spec.excluded_columns]
            if "id" not in selected:
                raise NormalizedEvidenceError(f"{name}.id is unexpectedly excluded")
            for fk in conn.execute(f'PRAGMA foreign_key_list("{name}")'):
                target, source, target_column = str(fk[2]), str(fk[3]), str(fk[4])
                if target_column == "id":
                    if target not in specs:
                        raise NormalizedEvidenceError(
                            f"{name}.{source} references non-authoritative {target}.id"
                        )
                    foreign_keys[(name, source)] = target
            table_rows: dict[int, dict[str, Any]] = {}
            quoted = ",".join(f'"{column}"' for column in selected)
            for row in conn.execute(f'SELECT {quoted} FROM "{name}"'):
                table_rows[int(row["id"])] = {
                    column: _canonical_value(row[column]) for column in selected if column != "id"
                }
            rows[name] = table_rows

        # Give every vertex a deterministic base colour from scalar evidence.
        # Integer colours, assigned from sorted signatures, are important: a
        # cryptographic hash fed back into itself would never literally reach a
        # fixed point for a cycle.
        base_signatures: dict[tuple[str, int], str] = {}
        for table, table_rows in rows.items():
            for row_id, row in table_rows.items():
                base = {key: value for key, value in row.items() if (table, key) not in foreign_keys}
                base_signatures[(table, row_id)] = json.dumps(
                    [table, base], sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
        palette = {value: index for index, value in enumerate(sorted(set(base_signatures.values())))}
        labels = {vertex: palette[value] for vertex, value in base_signatures.items()}
        while True:
            signatures: dict[tuple[str, int], str] = {}
            for table, table_rows in rows.items():
                for row_id, row in table_rows.items():
                    edges: dict[str, Any] = {}
                    for column, cell in row.items():
                        target = foreign_keys.get((table, column))
                        if target is None or cell is None:
                            continue
                        elif not isinstance(cell, int) or (target, cell) not in labels:
                            raise NormalizedEvidenceError(
                                f"unresolvable foreign key {table}.{column}={cell!r}"
                            )
                        else:
                            edges[column] = [target, labels[(target, cell)]]
                    signatures[(table, row_id)] = json.dumps(
                        [labels[(table, row_id)], edges], sort_keys=True,
                        separators=(",", ":"), ensure_ascii=True,
                    )
            palette = {value: index for index, value in enumerate(sorted(set(signatures.values())))}
            next_labels = {vertex: palette[value] for vertex, value in signatures.items()}
            # The current colour is part of the next signature, making the
            # partition monotonic; finite rows therefore guarantee convergence.
            if all(next_labels[key] == labels[key] for key in labels):
                break
            labels = next_labels

        label_counts: dict[str, Counter[int]] = {
            table: Counter(
                label for (candidate_table, _row_id), label in labels.items()
                if candidate_table == table
            )
            for table in rows
        }
        ambiguous = {
            (table, label)
            for table, counts in label_counts.items()
            for label, count in counts.items()
            if count > 1
        }
        projection: dict[str, tuple[str, ...]] = {}
        for table, table_rows in rows.items():
            encoded: list[str] = []
            for row_id, row in table_rows.items():
                value: dict[str, Any] = {}
                for column, cell in row.items():
                    target = foreign_keys.get((table, column))
                    if target is None or cell is None:
                        value[column] = cell
                    else:
                        label = labels.get((target, cell))
                        if label is None or (target, label) in ambiguous:
                            raise NormalizedEvidenceError(
                                f"ambiguous foreign key {table}.{column}={cell!r}"
                            )
                        value[column] = {"table": target, "key": label}
                encoded.append(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
            projection[table] = tuple(sorted(encoded))
        return projection
    finally:
        conn.row_factory = old_factory


def normalized_evidence_fingerprint(conn: sqlite3.Connection, *, version: int = 1) -> str:
    digest = hashlib.sha256(f"catalog-evidence-comparison-v{version}\n".encode())
    for table, rows in normalized_evidence_projection(conn).items():
        digest.update(f"table:{table}\n".encode())
        for row in rows:
            digest.update(row.encode() + b"\n")
    return digest.hexdigest()


def compare_normalized_evidence(
    left: sqlite3.Connection, right: sqlite3.Connection
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Return compact table/key diagnostics for a full-vs-delta parity gate."""

    left_rows, right_rows = normalized_evidence_projection(left), normalized_evidence_projection(right)
    differences: dict[str, dict[str, tuple[str, ...]]] = {}
    for table in AUTHORITATIVE_EVIDENCE_TABLES:
        before, after = Counter(left_rows[table.name]), Counter(right_rows[table.name])
        if before != after:
            only_left = list((before - after).elements())
            only_right = list((after - before).elements())
            differences[table.name] = {
                "only_left": tuple(sorted(only_left)[:20]),
                "only_right": tuple(sorted(only_right)[:20]),
            }
    return differences
