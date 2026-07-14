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


@dataclass
class BuildStats:
    rows_inserted: int = 0


def ensure_entity_access_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entity_access_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            UNIQUE(entity_id, surface, record_id, link_type, evidence_file_id, evidence_symbol_id)
        );

        CREATE INDEX IF NOT EXISTS idx_entity_access_links_entity_surface
            ON entity_access_links(entity_id, surface);
        CREATE INDEX IF NOT EXISTS idx_entity_access_links_surface_record
            ON entity_access_links(surface, record_id);
        CREATE INDEX IF NOT EXISTS idx_entity_access_links_evidence_file
            ON entity_access_links(evidence_file_id);
        """
    )


def ensure_dbschema_file_id_column(conn: sqlite3.Connection) -> None:
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dbschema_tables)").fetchall()
    }
    if "file_id" not in cols:
        conn.execute("ALTER TABLE dbschema_tables ADD COLUMN file_id INTEGER")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dbschema_tables_file_id ON dbschema_tables(file_id)"
    )

    conn.execute(
        """
        UPDATE dbschema_tables
        SET file_id = (
            SELECT f.id
            FROM files f
            WHERE f.path = dbschema_tables.source_file
            LIMIT 1
        )
        WHERE (file_id IS NULL OR file_id = 0)
          AND source_file IS NOT NULL
        """
    )


def _entity_file_anchors_cte() -> str:
    return """
    WITH entity_file_anchors AS (
        SELECT DISTINCT em.entity_id, em.file_id
        FROM entity_mappings em
        WHERE em.entity_id IS NOT NULL
          AND em.file_id IS NOT NULL

        UNION

        SELECT DISTINCT em.entity_id, f.id AS file_id
        FROM entity_mappings em
        JOIN files f
          ON f.path = em.source_text
        WHERE em.entity_id IS NOT NULL
          AND em.source_text IS NOT NULL

        UNION

        SELECT DISTINCT en.id AS entity_id, f.id AS file_id
        FROM entity_nodes en
        JOIN files f
          ON f.path = en.ent_file
        WHERE en.ent_file IS NOT NULL

        UNION

        SELECT DISTINCT w.entity_id, w.file_id
        FROM workflows w
        WHERE w.entity_id IS NOT NULL
          AND w.file_id IS NOT NULL

        UNION

        SELECT DISTINCT r.entity_id, r.file_id
        FROM rest_endpoints r
        WHERE r.entity_id IS NOT NULL
          AND r.file_id IS NOT NULL
    )
    """


def _run_insert(conn: sqlite3.Connection, sql: str) -> int:
    before = conn.total_changes
    conn.execute(sql)
    return conn.total_changes - before


def link_by_file_overlap(conn: sqlite3.Connection) -> int:
    total = 0

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            efa.entity_id,
            'security_operation',
            so.id,
            'file_id_overlap',
            so.file_id,
            'deterministic_exact',
            'entity anchor file_id equals security operation file_id'
        FROM entity_file_anchors efa
        JOIN security_operations so
          ON so.file_id = efa.file_id
        """,
    )

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            efa.entity_id,
            'security_policy',
            sp.id,
            'file_id_overlap',
            sp.file_id,
            'deterministic_exact',
            'entity anchor file_id equals security policy file_id'
        FROM entity_file_anchors efa
        JOIN security_policies sp
          ON sp.file_id = efa.file_id
        """,
    )

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            efa.entity_id,
            'security_menu',
            sm.id,
            'file_id_overlap',
            sm.file_id,
            'deterministic_exact',
            'entity anchor file_id equals security menu file_id'
        FROM entity_file_anchors efa
        JOIN security_menus sm
          ON sm.file_id = efa.file_id
        """,
    )

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            efa.entity_id,
            'security_menu_item',
            smi.id,
            'file_id_overlap',
            sm.file_id,
            'deterministic_exact',
            'entity anchor file_id equals parent security menu file_id'
        FROM entity_file_anchors efa
        JOIN security_menus sm
          ON sm.file_id = efa.file_id
        JOIN security_menu_items smi
          ON smi.menu_id = sm.id
        """,
    )

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            efa.entity_id,
            'dbschema_table',
            dt.id,
            'file_id_overlap',
            dt.file_id,
            'deterministic_exact',
            'entity anchor file_id equals dbschema source file_id'
        FROM entity_file_anchors efa
        JOIN dbschema_tables dt
          ON dt.file_id = efa.file_id
        """,
    )

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            efa.entity_id,
            'rest_endpoint',
            r.id,
            'file_id_overlap',
            r.file_id,
            'deterministic_exact',
            'entity anchor file_id equals rest endpoint source file_id'
        FROM entity_file_anchors efa
        JOIN rest_endpoints r
          ON r.file_id = efa.file_id
        """,
    )

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            efa.entity_id,
            'workflow',
            w.id,
            'file_id_overlap',
            w.file_id,
            'deterministic_exact',
            'entity anchor file_id equals workflow source file_id'
        FROM entity_file_anchors efa
        JOIN workflows w
          ON w.file_id = efa.file_id
        """,
    )

    return total


def link_by_entity_fk(conn: sqlite3.Connection) -> int:
    total = 0

    total += _run_insert(
        conn,
        """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT
            w.entity_id,
            'workflow',
            w.id,
            'entity_fk',
            w.file_id,
            'deterministic_exact',
            'direct workflow.entity_id'
        FROM workflows w
        WHERE w.entity_id IS NOT NULL
        """,
    )

    total += _run_insert(
        conn,
        """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT
            r.entity_id,
            'rest_endpoint',
            r.id,
            'entity_fk',
            r.file_id,
            'deterministic_exact',
            'direct rest_endpoints.entity_id'
        FROM rest_endpoints r
        WHERE r.entity_id IS NOT NULL
        """,
    )

    return total


def link_by_table_name_match(conn: sqlite3.Connection) -> int:
    """
    Link entities to dbschema_tables by matching entity_nodes.table_name
    to dbschema_tables.table_name (case-insensitive).

    This is the primary tracing path for dbschema: a single shared source
    file (dbschema.inc) means file_id overlap never fires for this surface.
    """
    return _run_insert(
        conn,
        """
        INSERT OR IGNORE INTO entity_access_links (
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            en.id,
            'dbschema_table',
            dt.id,
            'table_name_match',
            dt.file_id,
            'deterministic_exact',
            'entity_nodes.table_name = dbschema_tables.table_name'
        FROM entity_nodes en
        JOIN dbschema_tables dt
          ON LOWER(dt.table_name) = LOWER(en.table_name)
        WHERE en.table_name IS NOT NULL
          AND TRIM(en.table_name) <> ''
        """,
    )


def build(db: str, reset: bool) -> BuildStats:
    conn = get_connection(db)
    stats = BuildStats()

    try:
        ensure_entity_access_table(conn)
        ensure_dbschema_file_id_column(conn)
        if reset:
            conn.execute("DELETE FROM entity_access_links")

        stats.rows_inserted += link_by_file_overlap(conn)
        stats.rows_inserted += link_by_entity_fk(conn)
        stats.rows_inserted += link_by_table_name_match(conn)

        conn.commit()
    finally:
        conn.close()

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option(
    "--reset/--no-reset",
    default=True,
    show_default=True,
    help="Rebuild entity access links from a clean snapshot.",
)
def build_command(db: str, reset: bool) -> None:
    stats = build(db=db, reset=reset)
    click.echo(f"Entity access links inserted: {stats.rows_inserted}")


if __name__ == "__main__":
    cli()
