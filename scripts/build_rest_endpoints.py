#!/usr/bin/env python3

"""
Phase 12: REST Endpoints Extraction

Extracts REST API endpoints from OpenAPI specification files indexed in openapispec_index.

For each OpenAPI paths file in the openapispec_index:
- Parse the YAML file to extract all paths and HTTP methods
- Link endpoints to files via file_id
- Optionally link to entities if canonical_name can be matched to entity_nodes
- Populate the rest_endpoints table with method, path, and foreign key references

Expected output: ~2,000-3,000 REST endpoints across all OpenAPI specs.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from tqdm import tqdm

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

DEFAULT_DB = "catalog/catalog.db"
DEFAULT_REPO_ROOT = "/home/aritraghosh/projects/main"

# HTTP methods we recognize in OpenAPI paths
RECOGNIZED_METHODS = {"get", "post", "patch", "delete", "put"}


@dataclass
class BuildStats:
    specs_processed: int = 0
    endpoints_inserted: int = 0
    yaml_parse_failures: int = 0
    no_paths_found: int = 0


def _parse_yaml(file_path: Path) -> tuple[dict[str, Any], bool]:
    """
    Parse a YAML file safely.
    Returns (parsed_dict, success_bool)
    """
    try:
        with open(file_path, "r") as f:
            doc = yaml.safe_load(f)
        return doc or {}, True
    except Exception:
        return {}, False


def _extract_endpoints_from_yaml(
    doc: dict[str, Any],
) -> list[tuple[str, str]]:
    """
    Extract all (method, path) tuples from an OpenAPI document.
    Returns list of (method, path) tuples.
    """
    endpoints = []
    paths = doc.get("paths", {})

    if not paths:
        return endpoints

    for path_str, methods_obj in paths.items():
        if not isinstance(methods_obj, dict):
            continue

        for method in methods_obj.keys():
            method_lower = method.lower()
            if method_lower in RECOGNIZED_METHODS:
                endpoints.append((method_lower.upper(), path_str))

    return endpoints


def _insert_endpoints(
    conn: sqlite3.Connection,
    endpoints: list[tuple[str, str, int, int | None]],
) -> int:
    """
    Insert REST endpoints into the database.
    Args:
        conn: Database connection
        endpoints: List of (method, path, file_id, entity_id) tuples
    Returns:
        Count of inserted endpoints
    """
    inserted = 0
    sql = """
        INSERT OR IGNORE INTO rest_endpoints (
            method,
            path,
            file_id,
            entity_id
        )
        VALUES (?, ?, ?, ?)
    """

    for method, path, file_id, entity_id in endpoints:
        try:
            cur = conn.execute(sql, (method, path, file_id, entity_id))
            if cur.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass

    return inserted


def build(
    db: str,
    repo_root: Path,
    reset: bool = False,
) -> BuildStats:
    """
    Build REST endpoints from OpenAPI specification files indexed in openapispec_index.

    Args:
        db: Path to SQLite database
        repo_root: Root path of repository containing OpenAPI specs
        reset: Whether to clear rest_endpoints table before rebuilding

    Returns:
        BuildStats with counts of processed specs and inserted endpoints
    """
    if yaml is None:
        raise click.ClickException(
            "Missing dependency 'pyyaml'. Install project dependencies and rerun."
        )

    conn = get_connection(db)
    try:
        if reset:
            conn.execute("DELETE FROM rest_endpoints")
            conn.commit()

        stats = BuildStats()

        # Get all OpenAPI spec files from openapispec_index
        # Focus on files in paths directories which contain REST endpoint definitions
        specs = conn.execute(
            """
            SELECT id, file_id, file_path, kind
            FROM openapispec_index
            WHERE file_path LIKE '%/paths/%' OR kind = 'operations'
            ORDER BY file_path
            """
        ).fetchall()

        click.echo(f"📊 Found {len(specs)} OpenAPI specification files with paths")

        for spec_row in tqdm(specs, desc="Building REST endpoints", unit="spec"):
            spec_id, file_id, file_path, kind = spec_row
            stats.specs_processed += 1

            # Construct full path to the YAML file
            yaml_file_path = repo_root / file_path
            if not yaml_file_path.exists():
                continue

            # Parse the OpenAPI YAML file
            doc, parsed_ok = _parse_yaml(yaml_file_path)
            if not parsed_ok:
                stats.yaml_parse_failures += 1
                continue

            # Extract endpoints from the document
            endpoints = _extract_endpoints_from_yaml(doc)
            if not endpoints:
                stats.no_paths_found += 1
                continue

            # Prepare data for insertion: (method, path, file_id, entity_id)
            # For now, entity_id is None; could be enhanced to link via slug matching
            endpoints_to_insert = [
                (method, path, file_id, None)
                for method, path in endpoints
            ]

            # Insert into database
            inserted = _insert_endpoints(conn, endpoints_to_insert)
            stats.endpoints_inserted += inserted

            # Commit periodically to avoid holding locks
            if stats.specs_processed % 100 == 0:
                conn.commit()

        conn.commit()

    finally:
        conn.close()

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=Path(DEFAULT_REPO_ROOT),
    show_default=True,
    help="Repository root path used to resolve OpenAPI YAML files.",
)
@click.option(
    "--reset",
    is_flag=True,
    help="Delete rest_endpoints table content before rebuilding.",
)
def build_command(
    db: str,
    repo_root: Path,
    reset: bool,
) -> None:
    """Build REST endpoints from OpenAPI specification files."""
    stats = build(
        db=db,
        repo_root=repo_root.resolve(),
        reset=reset,
    )

    click.echo(f"✅ Specs processed:          {stats.specs_processed}")
    click.echo(f"✅ REST endpoints inserted:  {stats.endpoints_inserted}")
    click.echo(f"⚠️  No paths found:          {stats.no_paths_found}")
    click.echo(f"❌ YAML parse failures:      {stats.yaml_parse_failures}")


if __name__ == "__main__":
    cli()
