#!/usr/bin/env python3

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import click

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

DEFAULT_DB = "catalog/catalog.db"
OPENAPI_MAPPING_TYPES = [
    "openapispec_schema",
    "openapispec_operations",
    "openapispec_history",
    "openapispec_paths",
    "openapispec_actions",
    "openapispec_events",
    "openapispec_resource",
    "openapispec_components",
    "openapispec_security",
    "openapispec_unknown",
]


@dataclass
class LinkStats:
    mappings_inserted: int = 0
    unmatched_rows: int = 0


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return bool(row)


def _get_entities_by_name(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, name FROM entity_nodes").fetchall()
    return {
        str(row["name"]).lower(): int(row["id"])
        for row in rows
        if row["name"] is not None
    }


def _insert_mapping(
    conn: sqlite3.Connection,
    entity_id: int,
    file_id: int,
    mapping_type: str,
    source_text: str,
) -> bool:
    cur = conn.execute(
        """
        INSERT INTO entity_mappings(
            entity_id,
            symbol_id,
            file_id,
            mapping_type,
            confidence,
            source_text
        )
        SELECT ?, NULL, ?, ?, 1.0, ?
        WHERE NOT EXISTS (
            SELECT 1
            FROM entity_mappings
            WHERE entity_id = ?
              AND symbol_id IS NULL
              AND file_id = ?
              AND mapping_type = ?
              AND source_text = ?
        )
        """,
        (
            entity_id,
            file_id,
            mapping_type,
            source_text,
            entity_id,
            file_id,
            mapping_type,
            source_text,
        ),
    )
    return cur.rowcount > 0


def _link_openapispec(conn: sqlite3.Connection) -> LinkStats:
    if not _table_exists(conn, "openapispec_index"):
        raise click.ClickException(
            "Required table openapispec_index is missing. Run scan_openapispec.py first."
        )

    rows = conn.execute(
        """
        SELECT
            file_id,
            file_path,
            canonical_name,
            kind
        FROM openapispec_index
        WHERE state = 'active'
        """
    ).fetchall()

    stats = LinkStats()
    entities_by_name = _get_entities_by_name(conn)
    for row in rows:
        canonical_name = str(row["canonical_name"] or "").strip().lower()
        entity_id = entities_by_name.get(canonical_name)
        if entity_id is None:
            stats.unmatched_rows += 1
            continue

        file_id = row["file_id"]
        if file_id is None:
            stats.unmatched_rows += 1
            continue

        mapping_type = f"openapispec_{row['kind']}"
        inserted = _insert_mapping(
            conn=conn,
            entity_id=entity_id,
            file_id=int(file_id),
            mapping_type=mapping_type,
            source_text=str(row["file_path"] or ""),
        )
        if inserted:
            stats.mappings_inserted += 1

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("link")
@click.option("--db", default=DEFAULT_DB, show_default=True, help="Path to SQLite catalog database.")
@click.option("--reset", is_flag=True, help="Delete OpenAPI-derived mappings before relinking.")
def link_command(db: str, reset: bool) -> None:
    conn = get_connection(db)
    try:
        if reset:
            placeholders = ", ".join(["?"] * len(OPENAPI_MAPPING_TYPES))
            conn.execute(
                f"DELETE FROM entity_mappings WHERE mapping_type IN ({placeholders})",
                OPENAPI_MAPPING_TYPES,
            )
            conn.commit()

        stats = _link_openapispec(conn)
        conn.commit()
    finally:
        conn.close()

    click.echo(f"OpenAPI mappings inserted:   {stats.mappings_inserted}")
    click.echo(f"Unmatched openapispec rows: {stats.unmatched_rows}")


if __name__ == "__main__":
    cli()