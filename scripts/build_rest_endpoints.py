#!/usr/bin/env python3

"""
Phase 12: REST Endpoints Extraction

Extracts REST API endpoints from OpenAPI specification files indexed in openapispec_index.

For each OpenAPI paths file in the openapispec_index:
- Parse the YAML file to extract all paths and HTTP methods
- Link endpoints to files via file_id
- Optionally link to entities using existing OpenAPI-derived entity_mappings
- Populate the rest_endpoints table with method, path, and foreign key references

Expected output: ~2,000-3,000 REST endpoints across all OpenAPI specs.
"""

from __future__ import annotations

import re
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
    endpoints_updated: int = 0
    yaml_parse_failures: int = 0
    no_paths_found: int = 0
    symbol_fallback_files: int = 0
    symbol_fallback_endpoints: int = 0
    schema_bridge_hits: int = 0
    schema_bridge_overrides: int = 0


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


def _extract_endpoints_from_symbols(
    conn: sqlite3.Connection,
    file_id: int,
) -> list[tuple[str, str]]:
    """
    Reuse parser-emitted yaml_operation symbols as fallback endpoint evidence.

    Expected symbol name format: "METHOD /path".
    """
    rows = conn.execute(
        """
        SELECT name
        FROM symbols
        WHERE file_id = ?
          AND language = 'yaml'
          AND kind = 'yaml_operation'
        ORDER BY name
        """,
        (file_id,),
    ).fetchall()

    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    pattern = re.compile(r"^([A-Z]+)\s+(/.+)$")

    for row in rows:
        symbol_name = str(row["name"] or "").strip()
        match = pattern.match(symbol_name)
        if not match:
            continue

        method = match.group(1).upper()
        path = match.group(2)
        if method.lower() not in RECOGNIZED_METHODS:
            continue

        pair = (method, path)
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)

    return out


def _insert_endpoints(
    conn: sqlite3.Connection,
    endpoints: list[tuple[str, str, int, int | None, int | None]],
) -> int:
    """
    Insert REST endpoints into the database.
    Args:
        conn: Database connection
        endpoints: List of (method, path, file_id, entity_id, handler_symbol_id) tuples
    Returns:
        Count of inserted endpoints
    """
    inserted = 0
    sql = """
        INSERT OR IGNORE INTO rest_endpoints (
            method,
            path,
            file_id,
            entity_id,
            handler_symbol_id
        )
        VALUES (?, ?, ?, ?, ?)
    """

    for method, path, file_id, entity_id, handler_symbol_id in endpoints:
        try:
            cur = conn.execute(
                sql,
                (method, path, file_id, entity_id, handler_symbol_id),
            )
            if cur.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass

    return inserted


def _update_endpoints(
    conn: sqlite3.Connection,
    endpoints: list[tuple[str, str, int, int | None, int | None]],
) -> int:
    """
    Backfill nullable columns for endpoints that already exist.
    """
    updated = 0
    sql = """
        UPDATE rest_endpoints
        SET entity_id = COALESCE(entity_id, ?),
            handler_symbol_id = COALESCE(handler_symbol_id, ?)
        WHERE method = ?
          AND path = ?
          AND file_id = ?
    """

    for method, path, file_id, entity_id, handler_symbol_id in endpoints:
        cur = conn.execute(
            sql,
            (entity_id, handler_symbol_id, method, path, file_id),
        )
        if cur.rowcount > 0:
            updated += 1

    return updated


def _resolve_entity_ids_by_file(conn: sqlite3.Connection) -> dict[int, int]:
    """
    Build a deterministic file_id -> entity_id map from OpenAPI-derived mappings.

    Only files with exactly one distinct entity_id are mapped; ambiguous files are skipped.
    """
    rows = conn.execute(
        """
        SELECT
            file_id,
            COUNT(DISTINCT entity_id) AS entity_count,
            MIN(entity_id) AS entity_id
        FROM entity_mappings
        WHERE file_id IS NOT NULL
          AND entity_id IS NOT NULL
          AND mapping_type LIKE 'openapispec_%'
        GROUP BY file_id
        """
    ).fetchall()

    resolved: dict[int, int] = {}
    for row in rows:
        if row["entity_count"] == 1:
            resolved[int(row["file_id"])] = int(row["entity_id"])
    return resolved


def _openapi_family_from_path(file_path: str) -> str:
    stem = Path(file_path).name
    token = stem.split(".", 1)[0].strip().lower()
    return token


def _build_schema_entity_bridge(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str, str], int]:
    """
    Build deterministic (module, canonical_name, family) -> entity_id mapping from schema rows.

    Only keys resolving to exactly one distinct entity_id are retained.
    """
    rows = conn.execute(
        """
        SELECT
            oi.file_path,
            LOWER(TRIM(oi.module)) AS module_key,
            LOWER(TRIM(oi.canonical_name)) AS canonical_key,
            em.entity_id
        FROM openapispec_index oi
        JOIN entity_mappings em
          ON em.file_id = oi.file_id
        WHERE oi.kind = 'schema'
          AND COALESCE(oi.state, 'active') = 'active'
          AND COALESCE(TRIM(oi.module), '') <> ''
          AND COALESCE(TRIM(oi.canonical_name), '') <> ''
          AND em.entity_id IS NOT NULL
          AND em.mapping_type LIKE 'openapispec_%'
        """
    ).fetchall()

    grouped: dict[tuple[str, str, str], set[int]] = {}
    for row in rows:
        family = _openapi_family_from_path(str(row["file_path"] or ""))
        key = (str(row["module_key"]), str(row["canonical_key"]), family)
        grouped.setdefault(key, set()).add(int(row["entity_id"]))

    bridge: dict[tuple[str, str, str], int] = {}
    for key, entity_ids in grouped.items():
        if len(entity_ids) == 1:
            bridge[key] = next(iter(entity_ids))
    return bridge


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _build_entity_indexes(
    conn: sqlite3.Connection,
) -> tuple[dict[str, int], dict[str, list[int]]]:
    rows = conn.execute(
        """
        SELECT id, name, ent_file
        FROM entity_nodes
        """
    ).fetchall()

    by_ent_stem: dict[str, int] = {}
    by_normalized_name: dict[str, list[int]] = {}

    for row in rows:
        entity_id = int(row["id"])
        entity_name = str(row["name"] or "").strip()
        ent_file = str(row["ent_file"] or "").strip()

        if ent_file:
            stem = Path(ent_file).stem.lower()
            if stem and stem not in by_ent_stem:
                by_ent_stem[stem] = entity_id

        if entity_name:
            normalized = _normalize_name(entity_name)
            if normalized:
                by_normalized_name.setdefault(normalized, []).append(entity_id)

    return by_ent_stem, by_normalized_name


def _resolve_entity_fallback(
    spec_row: sqlite3.Row,
    by_ent_stem: dict[str, int],
    by_normalized_name: dict[str, list[int]],
    schema_entity_bridge: dict[tuple[str, str, str], int],
) -> int | None:
    mapped_to = str(spec_row["x_mapped_to"] or "").strip().lower()
    if mapped_to and mapped_to in by_ent_stem:
        return by_ent_stem[mapped_to]

    bridged = _resolve_entity_schema_bridge(spec_row, schema_entity_bridge)
    if bridged is not None:
        return bridged

    canonical_name = str(spec_row["canonical_name"] or "").strip()
    if canonical_name:
        normalized = _normalize_name(canonical_name)
        ids = by_normalized_name.get(normalized, [])
        if len(ids) == 1:
            return ids[0]

    return None


def _resolve_entity_schema_bridge(
    spec_row: sqlite3.Row,
    schema_entity_bridge: dict[tuple[str, str, str], int],
) -> int | None:
    module_key = str(spec_row["module"] or "").strip().lower()
    canonical_key = str(spec_row["canonical_name"] or "").strip().lower()
    family = _openapi_family_from_path(str(spec_row["file_path"] or ""))
    if not module_key or not canonical_key:
        return None
    return schema_entity_bridge.get((module_key, canonical_key, family))


def _resolve_handler_symbol_ids(
    conn: sqlite3.Connection,
    file_id: int,
) -> dict[tuple[str, str], int]:
    rows = conn.execute(
        """
        SELECT id, name
        FROM symbols
        WHERE file_id = ?
          AND language = 'yaml'
          AND kind = 'yaml_operation'
        """,
        (file_id,),
    ).fetchall()

    out: dict[tuple[str, str], int] = {}
    pattern = re.compile(r"^([A-Z]+)\s+(/.+)$")

    for row in rows:
        symbol_id = int(row["id"])
        symbol_name = str(row["name"] or "").strip()
        match = pattern.match(symbol_name)
        if not match:
            continue

        method = match.group(1).upper()
        path = match.group(2)
        if method.lower() not in RECOGNIZED_METHODS:
            continue

        key = (method, path)
        if key not in out:
            out[key] = symbol_id

    return out


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
        entity_by_file_id = _resolve_entity_ids_by_file(conn)
        entity_by_ent_stem, entity_by_name = _build_entity_indexes(conn)
        schema_entity_bridge = _build_schema_entity_bridge(conn)

        # Get all OpenAPI spec files from openapispec_index
        # Focus on files in paths directories which contain REST endpoint definitions
        specs = conn.execute(
            """
            SELECT id, file_id, file_path, kind, canonical_name, x_mapped_to
                 , module
            FROM openapispec_index
                        WHERE (file_path LIKE '%/paths/%' OR kind = 'operations')
                            AND file_path NOT LIKE '%/paths/workflows.%'
            ORDER BY file_path
            """
        ).fetchall()

        click.echo(f"📊 Found {len(specs)} OpenAPI specification files with paths")

        for spec_row in tqdm(specs, desc="Building REST endpoints", unit="spec"):
            spec_id, file_id, file_path, kind = (
                spec_row["id"],
                spec_row["file_id"],
                spec_row["file_path"],
                spec_row["kind"],
            )
            stats.specs_processed += 1

            if file_id is None:
                continue

            # Construct full path to the YAML file
            yaml_file_path = repo_root / file_path
            if not yaml_file_path.exists():
                continue

            # Parse the OpenAPI YAML file
            doc, parsed_ok = _parse_yaml(yaml_file_path)
            if not parsed_ok:
                stats.yaml_parse_failures += 1

            # Extract endpoints from the document first.
            endpoints = _extract_endpoints_from_yaml(doc) if parsed_ok else []

            # Reuse YAML parser symbols as deterministic fallback.
            fallback_used = False
            if not endpoints:
                endpoints = _extract_endpoints_from_symbols(conn, int(file_id))
                if endpoints:
                    fallback_used = True

            if not endpoints:
                stats.no_paths_found += 1
                continue

            if fallback_used:
                stats.symbol_fallback_files += 1
                stats.symbol_fallback_endpoints += len(endpoints)

            # Prepare data for insertion: (method, path, file_id, entity_id, handler_symbol_id)
            # entity_id is populated only when deterministic evidence resolves to one entity.
            resolved_entity_id = entity_by_file_id.get(int(file_id))
            bridged_candidate = _resolve_entity_schema_bridge(
                spec_row=spec_row,
                schema_entity_bridge=schema_entity_bridge,
            )

            if bridged_candidate is not None:
                if resolved_entity_id is None:
                    resolved_entity_id = bridged_candidate
                    stats.schema_bridge_hits += 1
                elif resolved_entity_id != bridged_candidate:
                    # Per precedence policy, prefer unique schema-bridge evidence.
                    resolved_entity_id = bridged_candidate
                    stats.schema_bridge_overrides += 1

            if resolved_entity_id is None:
                resolved_entity_id = _resolve_entity_fallback(
                    spec_row,
                    by_ent_stem=entity_by_ent_stem,
                    by_normalized_name=entity_by_name,
                    schema_entity_bridge=schema_entity_bridge,
                )

            handler_by_endpoint = _resolve_handler_symbol_ids(conn, int(file_id))
            endpoints_to_insert = [
                (
                    method,
                    path,
                    int(file_id),
                    resolved_entity_id,
                    handler_by_endpoint.get((method, path)),
                )
                for method, path in endpoints
            ]

            # Insert into database
            inserted = _insert_endpoints(conn, endpoints_to_insert)
            stats.endpoints_inserted += inserted
            stats.endpoints_updated += _update_endpoints(conn, endpoints_to_insert)

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
    click.echo(f"✅ REST endpoints updated:   {stats.endpoints_updated}")
    click.echo(f"⚠️  No paths found:          {stats.no_paths_found}")
    click.echo(f"♻️  Symbol fallback files:    {stats.symbol_fallback_files}")
    click.echo(f"♻️  Symbol fallback endpoints:{stats.symbol_fallback_endpoints}")
    click.echo(f"🔗 Schema bridge hits:       {stats.schema_bridge_hits}")
    click.echo(f"🔀 Schema bridge overrides:  {stats.schema_bridge_overrides}")
    click.echo(f"❌ YAML parse failures:      {stats.yaml_parse_failures}")


if __name__ == "__main__":
    cli()
