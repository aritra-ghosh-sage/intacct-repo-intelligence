#!/usr/bin/env python3

from __future__ import annotations

import sqlite3
import sys
import re
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

LOW_SIGNAL_CANONICAL_SUFFIXES = {
    "ref",
    "reference",
    "history",
    "detail",
    "line",
    "entry",
    "status",
    "response",
    "request",
    "record",
    "map",
    "template",
    "preference",
    "provider",
    "log",
    "constraint",
}

MODULE_SCOPE_FALLBACKS: dict[str, list[str]] = {
    "ap": ["apar", "common", "company"],
    "ar": ["apar", "common", "company"],
    "co": ["company", "common"],
    "cm": ["cm", "common", "company"],
    "inv": ["inventory", "common"],
    "sales": ["sales", "common", "company"],
    "gl": ["gl", "common", "company"],
    "tax": ["tax", "common", "company"],
    "pa": ["pa", "common"],
    "contract": ["contract", "common"],
    "purchasing": ["purchasing", "common"],
    "core": ["common", "company"],
    "platform": ["platform", "common"],
    "reports": ["reports", "common"],
    "cre": ["cre", "common"],
    "ee": ["ee", "expenses", "common"],
}


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


def _get_entities_by_name(conn: sqlite3.Connection) -> dict[str, dict[str, list[int]]]:
    rows = conn.execute("SELECT id, name, module FROM entity_nodes").fetchall()
    entities_by_module: dict[str, dict[str, list[int]]] = {}
    for row in rows:
        name = str(row["name"] or "").strip()
        if not name:
            continue
        module = _normalize_module(str(row["module"] or ""))
        key = _normalize_name(name)
        entities_by_module.setdefault(module, {}).setdefault(key, []).append(int(row["id"]))
    return entities_by_module


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _normalize_module(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _module_candidates(module: str) -> list[str]:
    module_key = _normalize_module(module)
    candidates = [module_key]
    aliases = {
        "ap": "apar",
        "ar": "apar",
        "co": "company",
        "inv": "inventory",
    }
    alias_target = aliases.get(module_key)
    if alias_target and alias_target not in candidates:
        candidates.append(alias_target)
    for fallback in MODULE_SCOPE_FALLBACKS.get(module_key, []):
        normalized = _normalize_module(fallback)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return [candidate for candidate in candidates if candidate]


def _split_slug_parts(value: str) -> list[str]:
    return [part for part in re.split(r"[./]", value.lower()) if part]


def _openapi_name_candidates(
    canonical_name: str,
    slug: str,
    resource_path: str,
) -> list[str]:
    out: list[str] = []

    def add(raw: str | None) -> None:
        if not raw:
            return
        key = _normalize_name(raw)
        if key and key not in out:
            out.append(key)

    # Rule 1: canonical_name normalized case-insensitively.
    # Example: "billing-group-period" -> "billinggroupperiod".
    add(canonical_name)
    for variant in _canonical_name_variants(canonical_name):
        add(variant)

    # Rule 2: slug segment normalization.
    # Example: "accounts-payable.ap-bill.s1.api.yaml" -> "apbill".
    slug_parts = _split_slug_parts(slug)
    metadata_parts = {
        "api", "schema", "history", "yaml", "view", "uimeta",
        "s1", "s2", "systemfw1", "systemfw2", "objects",
        "services", "workflows", "actions", "events", "components",
    }
    if len(slug_parts) >= 2:
        for part in slug_parts[1:]:
            if part in metadata_parts:
                continue
            add(part)
    for part in slug_parts:
        if part in metadata_parts:
            continue
        add(part)

    # Rule 3: resource path entity extraction.
    # Example: "/services/v3/objects/ap-bill" -> "apbill".
    path_parts = [part for part in resource_path.lower().split("/") if part]
    if "objects" in path_parts:
        idx = path_parts.index("objects")
        if idx + 1 < len(path_parts):
            add(path_parts[idx + 1])
    for part in path_parts:
        if part in metadata_parts:
            continue
        add(part)

    return out


def _canonical_name_variants(canonical_name: str) -> list[str]:
    variants: list[str] = []
    parts = [part for part in re.split(r"[-_/]", canonical_name.lower()) if part]
    if len(parts) < 2:
        return variants

    # Rule 4: canonical suffix stripping for synthetic descriptor suffixes.
    # Example: "payment-provider-bank-account" -> "payment-provider-bank".
    trimmed = parts[:]
    while len(trimmed) > 1 and trimmed[-1] in LOW_SIGNAL_CANONICAL_SUFFIXES:
        trimmed = trimmed[:-1]
        variants.append("-".join(trimmed))

    # Rule 5: canonical prefix collapse for hierarchical names.
    # Example: "document-line-detail" -> "document-line", then "document".
    for idx in range(len(parts) - 1, 1, -1):
        variants.append("-".join(parts[:idx]))

    # Rule 6: singularization for plural nouns.
    # Example: "documents" -> "document".
    if parts[-1].endswith("s") and len(parts[-1]) > 4:
        singular = parts[:-1] + [parts[-1][:-1]]
        variants.append("-".join(singular))

    deduped: list[str] = []
    for value in variants:
        key = _normalize_name(value)
        if key and key not in deduped:
            deduped.append(value)
    return deduped


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
            kind,
            slug,
            module,
            resource_path
        FROM openapispec_index
        WHERE state = 'active'
        """
    ).fetchall()

    stats = LinkStats()
    entities_by_name = _get_entities_by_name(conn)
    for row in rows:
        module_keys = _module_candidates(str(row["module"] or ""))
        candidate_names = _openapi_name_candidates(
            canonical_name=str(row["canonical_name"] or ""),
            slug=str(row["slug"] or ""),
            resource_path=str(row["resource_path"] or ""),
        )
        entity_id: int | None = None
        for module_key in module_keys:
            module_entities = entities_by_name.get(module_key, {})
            for candidate in candidate_names:
                matches = module_entities.get(candidate, [])
                if len(matches) == 1:
                    entity_id = matches[0]
                    break
            if entity_id is not None:
                break

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