"""Repository-scoped evidence contracts used by refresh admission.

The ownership registry is deliberately closed: a table must be named here
before it can affect a repository fingerprint.  This prevents a new, unclear
table from silently becoming authoritative evidence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from typing import Any

from catalog.content_fingerprint import AUTHORITATIVE_EVIDENCE_TABLES

# Every current authoritative family is either directly repository-owned or is
# explicitly excluded from repository fingerprints.  Global entity identity is
# included only through the entity nodes referenced by this repository.
_DIRECT_REPO_TABLES = frozenset({
    "files", "relationships", "entity_occurrences", "entity_mappings", "entity_roots",
    "workflows", "openapi_file_ref_edges", "rest_endpoints", "openapispec_index",
    "api_version_compatibility", "security_operations", "security_policies",
    "security_menus", "dbschema_tables", "entity_access_links",
    "test_cases", "test_diagnostics", "entity_schema_components",
    "entity_relationship_facts", "entity_operation_facts", "entity_extraction_coverage",
    "entity_semantic_conflicts", "ui_surfaces", "ui_artifacts", "ui_entity_references",
    "ui_artifact_includes", "ui_fields", "ui_events", "ui_script_dependencies",
    "ui_event_calls", "ui_resolution_issues", "api_registry_entries",
    "api_registry_entry_links", "api_registry_issues", "ui_source_diagnostics",
})
# Every derived family is resolved only through declared SQLite foreign keys;
# a table without a proven path is rejected rather than silently omitted.
REPOSITORY_OWNERSHIP: dict[str, str] = {
    spec.name: ("repo_id" if spec.name in _DIRECT_REPO_TABLES else "derived")
    for spec in AUTHORITATIVE_EVIDENCE_TABLES
}
REPOSITORY_OWNERSHIP["entity_nodes"] = "referenced_entity"
REPOSITORY_OWNERSHIP["repos"] = "identity"
REPOSITORY_OWNERSHIP["knowledge_items"] = "excluded"
REPOSITORY_OWNERSHIP["integration_links"] = "excluded"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _value(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, float):
        return {"float": value.hex()}
    return value


def repository_evidence_fingerprint(conn: sqlite3.Connection, repo_id: int) -> str:
    """Hash canonical source evidence for one repository, independent of IDs.

    It excludes run history, timestamps, logs, diagnostics, knowledge items,
    unsupported integration links, and all other repositories.  FK values are
    represented by their own scalar values where possible; the stable row JSON
    is sorted, so SQLite allocation order does not influence this digest.
    """
    digest = hashlib.sha256(b"repository-evidence-v2\n")
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        owned_ids: dict[str, set[int]] = {}
        for table, owner in REPOSITORY_OWNERSHIP.items():
            if not _table_exists(conn, table):
                continue
            if owner == "repo_id":
                owned_ids[table] = {
                    int(row[0]) for row in conn.execute(f'SELECT id FROM "{table}" WHERE repo_id=?', (repo_id,))
                }
            elif owner == "referenced_entity":
                owned_ids[table] = {
                    int(row[0]) for row in conn.execute(
                        "SELECT DISTINCT entity_id FROM entity_occurrences WHERE repo_id=?", (repo_id,)
                    )
                }
            elif owner == "identity":
                owned_ids[table] = {repo_id}

        # Resolve each child through a concrete declared FK to already-owned
        # evidence. This is a fixed-point because child table declarations are
        # not ordered by ownership depth.
        unresolved = {
            table for table, owner in REPOSITORY_OWNERSHIP.items()
            if owner == "derived" and _table_exists(conn, table)
        }
        while unresolved:
            progressed = False
            for table in sorted(tuple(unresolved)):
                for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
                    source, target, target_column = str(fk[3]), str(fk[2]), str(fk[4])
                    parent_ids = owned_ids.get(target)
                    if target_column != "id" or parent_ids is None:
                        continue
                    if parent_ids:
                        placeholders = ",".join("?" for _ in parent_ids)
                        owned_ids[table] = {
                            int(row[0]) for row in conn.execute(
                                f'SELECT id FROM "{table}" WHERE "{source}" IN ({placeholders})',
                                tuple(sorted(parent_ids)),
                            )
                        }
                    else:
                        owned_ids[table] = set()
                    unresolved.remove(table)
                    progressed = True
                    break
            if not progressed:
                raise RuntimeError(
                    "authoritative table has no proven repository ownership path: "
                    + ", ".join(sorted(unresolved))
                )
        rows: dict[str, dict[int, dict[str, Any]]] = {}
        foreign_keys: dict[tuple[str, str], str] = {}
        excluded_tables = {
            "knowledge_items", "integration_links", "test_diagnostics",
            "entity_semantic_conflicts", "ui_resolution_issues",
            "api_registry_issues", "ui_source_diagnostics",
        }
        excluded_columns = {
            "id", "repo_id", "created_at", "updated_at", "last_modified", "archived_at",
            "last_indexed", "last_symbols_extracted", "last_relationships_extracted",
            "last_scanned_at", "last_built_at", "last_attempted_at", "index_status",
            "diagnostic_error", "last_attempt_status", "last_attempt_error", "local_root",
        }
        for table, owner in sorted(REPOSITORY_OWNERSHIP.items()):
            if not _table_exists(conn, table):
                continue
            if owner == "excluded" or table in excluded_tables:
                continue
            identifiers = owned_ids.get(table, set())
            if table == "repos":
                # Repository identity is intentionally narrow; local checkout
                # and operator lifecycle state are not source evidence.
                selected = ["repo_key", "remote_url", "tracked_branch"]
            else:
                columns = [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]
                selected = [column for column in columns if column not in excluded_columns]
            for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
                target, source, target_column = str(fk[2]), str(fk[3]), str(fk[4])
                if source in selected and target_column == "id" and target not in excluded_tables:
                    foreign_keys[(table, source)] = target
                elif source in selected and target_column == "id":
                    selected.remove(source)
            if identifiers:
                placeholders = ",".join("?" for _ in identifiers)
                query = f'SELECT {",".join(chr(34)+c+chr(34) for c in selected)} FROM "{table}" WHERE id IN ({placeholders})'
                rows[table] = {
                    int(row["id"]): {column: _value(row[column]) for column in selected}
                    for row in conn.execute(
                        f'SELECT id,{",".join(chr(34)+c+chr(34) for c in selected)} FROM "{table}" WHERE id IN ({placeholders})',
                        tuple(sorted(identifiers)),
                    )
                }
            else:
                rows[table] = {}

        base = {
            (table, row_id): json.dumps(
                [table, {name: value for name, value in row.items() if (table, name) not in foreign_keys}],
                sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            )
            for table, table_rows in rows.items() for row_id, row in table_rows.items()
        }
        palette = {value: index for index, value in enumerate(sorted(set(base.values())))}
        labels = {vertex: palette[value] for vertex, value in base.items()}
        while True:
            signatures: dict[tuple[str, int], str] = {}
            for (table, row_id), label in labels.items():
                edges: dict[str, Any] = {}
                for column, value in rows[table][row_id].items():
                    target = foreign_keys.get((table, column))
                    if target is None or value is None:
                        continue
                    if not isinstance(value, int) or (target, value) not in labels:
                        raise RuntimeError(f"unresolvable evidence foreign key {table}.{column}={value!r}")
                    edges[column] = [target, labels[(target, value)]]
                signatures[(table, row_id)] = json.dumps(
                    [label, edges], sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
            palette = {value: index for index, value in enumerate(sorted(set(signatures.values())))}
            next_labels = {vertex: palette[value] for vertex, value in signatures.items()}
            if next_labels == labels:
                break
            labels = next_labels
        counts = Counter((table, label) for (table, _), label in labels.items())
        for table in sorted(rows):
            digest.update(f"table:{table}\n".encode())
            encoded = []
            for row_id, row in rows[table].items():
                value = {}
                for column, cell in row.items():
                    target = foreign_keys.get((table, column))
                    if target is None or cell is None:
                        value[column] = cell
                    else:
                        label = labels.get((target, cell))
                        if label is None or counts[(target, label)] != 1:
                            raise RuntimeError(f"ambiguous evidence foreign key {table}.{column}")
                        value[column] = {"table": target, "key": label}
                encoded.append(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
            for value in sorted(encoded):
                digest.update(value.encode() + b"\n")
        stale = [
            _value(row[0]) for row in conn.execute(
                "SELECT json_object('source_path',source_path,'prior_source_path',prior_source_path,"
                "'prior_source_blob_sha',prior_source_blob_sha,'target_commit_sha',target_commit_sha,"
                "'diagnostic_json',diagnostic_json) FROM repo_stale_evidence WHERE repo_id=? ORDER BY source_path",
                (repo_id,),
            )
        ] if _table_exists(conn, "repo_stale_evidence") else []
        digest.update(b"stale\n")
        for value in stale:
            digest.update(str(value).encode() + b"\n")
    finally:
        conn.row_factory = old_factory
    return digest.hexdigest()
