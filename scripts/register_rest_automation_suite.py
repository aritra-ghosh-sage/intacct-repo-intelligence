#!/usr/bin/env python3
"""Register a REST automation suite before ingesting its Gherkin evidence."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import click

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection


DEFAULT_DB = "catalog/catalog.db"


def register_suite(
    conn: sqlite3.Connection,
    suite_id: str,
    suite_root: Path,
    object_mapping: Path,
    revision: str | None,
    enabled: bool,
) -> None:
    """Upsert one explicit, operator-owned automation-suite registration."""
    root = suite_root.resolve()
    mapping = object_mapping.resolve()
    if not mapping.is_relative_to(root):
        raise ValueError("object_mapping must be located inside suite_root")

    conn.execute(
        """
        INSERT INTO source_repositories(
            suite_id, repo_root, kind, revision, enabled, object_mapping_path, updated_at
        )
        VALUES (?, ?, 'test_suite', ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(suite_id) DO UPDATE SET
            repo_root = excluded.repo_root,
            kind = excluded.kind,
            revision = excluded.revision,
            enabled = excluded.enabled,
            object_mapping_path = excluded.object_mapping_path,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            suite_id,
            str(root),
            revision,
            int(enabled),
            mapping.relative_to(root).as_posix(),
        ),
    )
    conn.commit()


@click.command()
@click.option("--db", "db_path", default=DEFAULT_DB, show_default=True)
@click.option("--suite-id", required=True, help="Stable ID, for example ia-restapi-automation.")
@click.option("--suite-root", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True)
@click.option(
    "--object-mapping",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option("--revision", help="Optional source revision or commit SHA.")
@click.option("--disabled", is_flag=True, help="Register the suite without enabling ingestion.")
def main(
    db_path: str,
    suite_id: str,
    suite_root: Path,
    object_mapping: Path,
    revision: str | None,
    disabled: bool,
) -> None:
    """Register or update a REST automation suite."""
    try:
        register_suite(
            get_connection(db_path),
            suite_id=suite_id,
            suite_root=suite_root,
            object_mapping=object_mapping,
            revision=revision,
            enabled=not disabled,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Registered REST automation suite: {suite_id}")


if __name__ == "__main__":
    main()
