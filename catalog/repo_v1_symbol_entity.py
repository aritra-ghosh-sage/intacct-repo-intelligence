"""Materialize the reviewed repo-v1 symbol-to-entity contract."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import yaml

EXTRACTOR = "repo_v1_symbol_entity_v1"


class SymbolEntityMappingError(RuntimeError):
    """The reviewed mapping contract cannot be safely loaded."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SymbolEntityMappingError(f"mapping contract requires non-empty {label}")
    return value


def _entry_key(index: int) -> str:
    return f"mapping:{index + 1}"


def _entry_parts(
    entry: Any, index: int
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    if not isinstance(entry, dict):
        raise SymbolEntityMappingError(f"mapping {index + 1} must be an object")
    symbol = entry.get("symbol")
    entity = entry.get("entity")
    if not isinstance(symbol, dict) or not isinstance(entity, dict):
        raise SymbolEntityMappingError(
            f"mapping {index + 1} requires symbol and entity objects"
        )
    file_path = _text(symbol.get("file_path"), f"mappings[{index}].symbol.file_path")
    stable_key = _text(symbol.get("stable_key"), f"mappings[{index}].symbol.stable_key")
    source_path = _text(
        entity.get("source_path"), f"mappings[{index}].entity.source_path"
    )
    source_key = _text(entity.get("source_key"), f"mappings[{index}].entity.source_key")
    if any("*" in value for value in (file_path, stable_key, source_path, source_key)):
        raise SymbolEntityMappingError(f"mapping {index + 1} does not allow wildcards")
    mapping_type = _text(entry.get("mapping_type"), f"mappings[{index}].mapping_type")
    evidence = _text(entry.get("evidence"), f"mappings[{index}].evidence")
    return (
        {"file_path": file_path, "stable_key": stable_key},
        {"source_path": source_path, "source_key": source_key},
        mapping_type,
        evidence,
    )


def materialize_symbol_entity_links(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    build_id: int,
    repository: str,
    target_revision: str,
    contract_path: str | Path | None,
) -> dict[str, int | str | None]:
    """Load exact identities and persist resolved or explicit unresolved rows."""
    if contract_path is None:
        return {
            "mapping_count": 0,
            "resolved_count": 0,
            "unresolved_count": 0,
            "contract_path": None,
        }
    path = Path(contract_path).expanduser().resolve()
    try:
        raw = path.read_bytes()
        document = yaml.safe_load(raw.decode("utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SymbolEntityMappingError(
            f"cannot read mapping contract {path}: {exc}"
        ) from exc
    if not isinstance(document, dict) or document.get("schema_version") != "1":
        raise SymbolEntityMappingError("mapping contract schema_version must be '1'")
    if document.get("repository") != repository:
        raise SymbolEntityMappingError(
            "mapping contract repository must match the build repository"
        )
    if document.get("target_revision") != target_revision:
        raise SymbolEntityMappingError(
            "mapping contract target_revision must exactly match the build revision"
        )
    mappings = document.get("mappings")
    if not isinstance(mappings, list):
        raise SymbolEntityMappingError("mapping contract mappings must be a list")
    contract_sha = hashlib.sha256(raw).hexdigest()
    seen: set[tuple[str, str, str, str]] = set()
    counts = {"mapping_count": 0, "resolved_count": 0, "unresolved_count": 0}
    for index, entry in enumerate(mappings):
        symbol, entity, mapping_type, evidence = _entry_parts(entry, index)
        identity = (
            symbol["file_path"],
            symbol["stable_key"],
            entity["source_path"],
            entity["source_key"],
        )
        duplicate = identity in seen
        seen.add(identity)
        symbol_rows = conn.execute(
            """SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id
               WHERE s.repo_id=? AND f.path=? AND s.stable_key=?""",
            (repo_id, symbol["file_path"], symbol["stable_key"]),
        ).fetchall()
        entity_rows = conn.execute(
            """SELECT eo.id FROM entity_occurrences eo JOIN files f ON f.id=eo.source_file_id
               WHERE eo.repo_id=? AND f.path=? AND eo.source_key=?""",
            (repo_id, entity["source_path"], entity["source_key"]),
        ).fetchall()
        status = "resolved"
        reason = "exact_contract_identity"
        symbol_id = int(symbol_rows[0][0]) if len(symbol_rows) == 1 else None
        entity_id = int(entity_rows[0][0]) if len(entity_rows) == 1 else None
        if duplicate:
            status, reason, entity_id = (
                "ambiguous",
                "duplicate_contract_entry",
                None,
            )
        elif len(symbol_rows) != 1:
            status, reason, symbol_id = (
                "missing" if not symbol_rows else "ambiguous",
                "missing_symbol" if not symbol_rows else "ambiguous_symbol",
                None,
            )
        elif len(entity_rows) != 1:
            status, reason, entity_id = (
                "missing" if not entity_rows else "ambiguous",
                "missing_entity_occurrence"
                if not entity_rows
                else "ambiguous_entity_occurrence",
                None,
            )
        conn.execute(
            """INSERT INTO symbol_entity_links(
                repo_id,build_id,symbol_id,entity_occurrence_id,symbol_file_path,symbol_stable_key,
                entity_source_path,entity_source_key,mapping_type,resolution_status,resolution_reason,
                mapping_contract_path,mapping_contract_sha256,target_revision,contract_entry_key,evidence,extractor
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                repo_id,
                build_id,
                symbol_id,
                entity_id,
                symbol["file_path"],
                symbol["stable_key"],
                entity["source_path"],
                entity["source_key"],
                mapping_type,
                status,
                reason,
                str(path),
                contract_sha,
                target_revision,
                _entry_key(index),
                evidence,
                EXTRACTOR,
            ),
        )
        counts["mapping_count"] += 1
        counts["resolved_count" if status == "resolved" else "unresolved_count"] += 1
    counts["contract_path"] = str(path)
    counts["contract_sha256"] = contract_sha
    return counts


def mapping_rows_for_symbols(
    conn: sqlite3.Connection, repo_id: int, symbol_ids: list[int]
) -> list[sqlite3.Row]:
    if not symbol_ids:
        return []
    marks = ",".join("?" for _ in symbol_ids)
    return conn.execute(
        f"""SELECT l.*,s.name AS symbol_name,s.kind AS symbol_kind,
                   eo.entity_id,eo.source_key AS entity_source_key_resolved,
                   en.name AS entity_name,ef.path AS entity_source_path_resolved
              FROM symbol_entity_links l
              LEFT JOIN symbols s ON s.id=l.symbol_id
              LEFT JOIN entity_occurrences eo ON eo.id=l.entity_occurrence_id
              LEFT JOIN entity_nodes en ON en.id=eo.entity_id
              LEFT JOIN files ef ON ef.id=eo.source_file_id
             WHERE l.repo_id=? AND l.symbol_id IN ({marks}) ORDER BY l.symbol_id,l.id""",
        (repo_id, *symbol_ids),
    ).fetchall()


def entity_impact_facts(
    conn: sqlite3.Connection, repo_id: int, occurrence_id: int
) -> dict[str, list[dict[str, Any]]]:
    families = {
        "entity_metadata": (
            ("entity_section_facts", "occurrence_id"),
            ("entity_field_facts", "occurrence_id"),
            ("entity_schema_mappings", "occurrence_id"),
        ),
        "database": (
            ("entity_db_table_links", "occurrence_id"),
            ("entity_db_field_links", "occurrence_id"),
        ),
        "openapi": (("openapi_entity_links", "entity_occurrence_id"),),
        "workflows": (("workflow_facts", "entity_occurrence_id"),),
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for family, tables in families.items():
        rows: list[dict[str, Any]] = []
        for table, column in tables:
            query = f"SELECT * FROM {table} WHERE repo_id=? AND {column}=? ORDER BY id"
            for row in conn.execute(query, (repo_id, occurrence_id)).fetchall():
                rows.append(dict(row))
        result[family] = rows
    return result
