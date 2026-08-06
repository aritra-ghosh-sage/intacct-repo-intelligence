"""V1 relationship extraction from an immutable source snapshot."""

from __future__ import annotations

import math
import sqlite3
from pathlib import PurePosixPath

from tqdm import tqdm

from catalog.source_snapshot import SourceSnapshot, SourceSnapshotError
from parser.extract_relationships import (
    EXTRACTORS,
    RELATIONSHIP_EXTRACTOR,
    FileRow,
    Relationship,
    SymbolRow,
)


def _load_symbols(
    conn: sqlite3.Connection, repo_id: int
) -> tuple[
    dict[str, list[SymbolRow]],
    dict[int, list[SymbolRow]],
    dict[str, list[SymbolRow]],
]:
    """Load only candidate symbols for the pure legacy resolver/extractors."""

    by_name: dict[str, list[SymbolRow]] = {}
    by_file: dict[int, list[SymbolRow]] = {}
    by_qualified_name: dict[str, list[SymbolRow]] = {}
    rows = conn.execute(
        """SELECT s.id,s.name,s.kind,s.parent_symbol,s.file_id,f.path
           FROM symbols s
           JOIN files f ON f.id=s.file_id
           WHERE s.repo_id=? AND f.repo_id=?
           ORDER BY s.id""",
        (repo_id, repo_id),
    ).fetchall()
    for row in rows:
        symbol = SymbolRow(
            id=int(row["id"]),
            name=str(row["name"]),
            kind=str(row["kind"] or "unknown"),
            parent_symbol=row["parent_symbol"],
            file_id=int(row["file_id"]),
            file_path=str(row["path"]),
        )
        by_name.setdefault(symbol.name, []).append(symbol)
        by_file.setdefault(symbol.file_id, []).append(symbol)
        if symbol.parent_symbol:
            parent = str(symbol.parent_symbol)
            for qualified in (
                f"{parent}::{symbol.name}",
                f"{parent}.{symbol.name}",
            ):
                by_qualified_name.setdefault(qualified, []).append(symbol)
            short_parent = parent.split("\\")[-1].split(".")[-1]
            if short_parent != parent:
                for qualified in (
                    f"{short_parent}::{symbol.name}",
                    f"{short_parent}.{symbol.name}",
                ):
                    by_qualified_name.setdefault(qualified, []).append(symbol)
    return by_name, by_file, by_qualified_name


def _read_snapshot_text(snapshot: SourceSnapshot, path: str) -> str:
    destination = snapshot.snapshot_root.joinpath(*PurePosixPath(path).parts)
    try:
        return destination.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        raise SourceSnapshotError(f"snapshot read failed for {path}: {exc}") from exc


def _relationship_key(relationship: Relationship) -> tuple[object, ...]:
    return (
        relationship.source_symbol_id,
        relationship.source_name,
        relationship.source_kind,
        relationship.target_symbol_id,
        relationship.target_name,
        relationship.target_kind,
        relationship.relationship_type,
        relationship.file_id,
        relationship.file_path,
        relationship.language,
        relationship.confidence,
        relationship.evidence,
        relationship.resolution_class,
        relationship.resolution_reason,
    )


def _validate_extracted_relationship(
    relationship: Relationship, *, file_row: FileRow
) -> None:
    if relationship.file_id != file_row.id or relationship.file_path != file_row.path:
        raise ValueError("relationship source file does not match candidate file")
    if relationship.language != file_row.language:
        raise ValueError("relationship language does not match candidate file")
    if not relationship.target_name:
        raise ValueError("relationship target name is empty")
    if not relationship.relationship_type or not relationship.evidence:
        raise ValueError("relationship type or evidence is empty")
    if not relationship.resolution_class or not relationship.resolution_reason:
        raise ValueError("relationship resolution provenance is empty")
    if not math.isfinite(float(relationship.confidence)) or not 0 <= relationship.confidence <= 1:
        raise ValueError("relationship confidence is invalid")


def _insert_relationships(
    conn: sqlite3.Connection,
    relationships: list[Relationship],
    *,
    repo_id: int,
) -> int:
    # The legacy writer used INSERT OR IGNORE.  Deduplicate explicitly so NULL
    # source IDs have the same deterministic behavior as non-NULL IDs.
    unique = {_relationship_key(relationship): relationship for relationship in relationships}
    rows = [unique[key] for key in sorted(unique, key=repr)]
    for relationship in rows:
        conn.execute(
            """INSERT INTO relationships(
                   repo_id,source_symbol_id,source_name,source_kind,
                   target_symbol_id,target_name,target_kind,relationship_type,
                   file_id,file_path,language,confidence,evidence,
                   resolution_class,resolution_reason,extractor
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                repo_id,
                relationship.source_symbol_id,
                relationship.source_name,
                relationship.source_kind,
                relationship.target_symbol_id,
                relationship.target_name,
                relationship.target_kind,
                relationship.relationship_type,
                relationship.file_id,
                relationship.file_path,
                relationship.language,
                relationship.confidence,
                relationship.evidence,
                relationship.resolution_class,
                relationship.resolution_reason,
                RELATIONSHIP_EXTRACTOR,
            ),
        )
    return len(rows)


def extract_snapshot_relationships(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> int:
    """Extract relationships using only bytes materialized from ``snapshot``."""

    file_rows = {
        str(row["path"]): FileRow(
            id=int(row["id"]), path=str(row["path"]), language=str(row["language"])
        )
        for row in conn.execute(
            "SELECT id,path,language FROM files WHERE repo_id=? ORDER BY path", (repo_id,)
        ).fetchall()
    }
    if set(file_rows) != {entry.path for entry in snapshot.entries}:
        raise ValueError("snapshot and candidate file inventories differ")
    symbols_by_name, symbols_by_file, symbols_by_qualified_name = _load_symbols(
        conn, repo_id
    )
    parser_failed_file_ids = {
        int(row[0])
        for row in conn.execute(
            "SELECT DISTINCT file_id FROM symbol_diagnostics WHERE repo_id=?", (repo_id,)
        ).fetchall()
    }
    relationship_count = 0
    for entry in tqdm(
        snapshot.entries,
        desc="Extracting V1 relationships",
        unit="file",
        disable=not show_progress,
    ):
        file_row = file_rows[entry.path]
        if file_row.id in parser_failed_file_ids:
            continue
        extractor = EXTRACTORS.get(file_row.language)
        if extractor is None:
            continue
        savepoint = f"relationship_file_{file_row.id}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            text = _read_snapshot_text(snapshot, entry.path)
            relationships = list(
                extractor(
                    text,
                    file_row,
                    symbols_by_name,
                    symbols_by_file,
                    symbols_by_qualified_name,
                )
            )
            for relationship in relationships:
                _validate_extracted_relationship(relationship, file_row=file_row)
            relationship_count += _insert_relationships(
                conn, relationships, repo_id=repo_id
            )
        except SourceSnapshotError:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        except Exception as exc:  # noqa: BLE001
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise RuntimeError(f"relationship extraction failed for {entry.path}: {exc}") from exc
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    return relationship_count
