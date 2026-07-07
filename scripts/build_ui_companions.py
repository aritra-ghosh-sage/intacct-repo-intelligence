#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

import click

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

DEFAULT_DB = "catalog/catalog.db"


def build_ui_companions(db: str) -> tuple[int, list[tuple[str, int]]]:
    conn = get_connection(db)
    try:
        deleted = conn.execute("DELETE FROM ui_companions").rowcount
        click.echo(f"Cleared ui_companions rows: {deleted}")

        conn.execute(
            """
            INSERT INTO ui_companions (entity_id, kind, file_id, language)
            SELECT DISTINCT
                em.entity_id,
                CASE
                    WHEN em.mapping_type IN ('editor', 'form_editor') THEN 'editor'
                    WHEN em.mapping_type = 'lister' THEN 'lister'
                    WHEN em.mapping_type IN ('picker', 'pick_picker', 'batch_picker') THEN 'picker'
                    WHEN em.mapping_type = 'xslt' THEN
                        CASE
                            WHEN LOWER(f.path) LIKE '%editor.xsl%' OR LOWER(f.path) LIKE '%editor.xslt%' THEN 'editor'
                            WHEN LOWER(f.path) LIKE '%lister.xsl%' OR LOWER(f.path) LIKE '%lister.xslt%' THEN 'lister'
                            WHEN LOWER(f.path) LIKE '%picker.xsl%' OR LOWER(f.path) LIKE '%picker.xslt%' THEN 'picker'
                            ELSE NULL
                        END
                    ELSE NULL
                END AS kind,
                em.file_id,
                f.language
            FROM entity_mappings em
            JOIN files f ON f.id = em.file_id
            WHERE em.mapping_type IN (
                'editor', 'form_editor', 'lister',
                'picker', 'pick_picker', 'batch_picker', 'xslt'
            )
              AND em.entity_id IS NOT NULL
              AND em.file_id IS NOT NULL
            """
        )

        conn.execute(
            """
            DELETE FROM ui_companions
            WHERE entity_id IS NULL OR kind IS NULL OR file_id IS NULL
            """
        )

        total = conn.execute("SELECT COUNT(*) FROM ui_companions").fetchone()[0]
        kind_rows = conn.execute(
            """
            SELECT kind, COUNT(*) AS cnt
            FROM ui_companions
            GROUP BY kind
            ORDER BY kind
            """
        ).fetchall()
        conn.commit()
        return int(total), [(str(row["kind"]), int(row["cnt"])) for row in kind_rows]
    finally:
        conn.close()


@click.command()
@click.option("--db", default=DEFAULT_DB, show_default=True, help="Path to SQLite catalog database.")
def main(db: str) -> None:
    total, kind_rows = build_ui_companions(db)
    click.echo(f"ui_companions inserted: {total}")
    for kind, cnt in kind_rows:
        click.echo(f"  {kind}: {cnt}")


if __name__ == "__main__":
    main()
