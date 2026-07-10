#!/usr/bin/env python3

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
OPENAPISPEC_ROOT = Path("app/source/openapispec")
VERSION_PATTERN = re.compile(r"\.s(\d+)\.")


@dataclass
class ScanStats:
    files_processed: int = 0
    rows_indexed: int = 0
    files_missing_in_catalog: int = 0
    yaml_parse_failures: int = 0
    template_files_skipped: int = 0


def _is_template_file(rel_path: str) -> bool:
    lowered = rel_path.lower()
    filename = Path(lowered).name
    if "template" in lowered:
        return True
    return filename.startswith("template")


def _normalize_canonical_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("/")
    if not normalized:
        return None
    if "/" in normalized:
        normalized = normalized.split("/")[-1].strip()
    return normalized or None


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


def _get_file_id(conn: sqlite3.Connection, rel_path: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM files WHERE path = ? LIMIT 1",
        (rel_path,),
    ).fetchone()
    return int(row["id"]) if row else None


def _parse_yaml(path: Path) -> tuple[dict[str, Any] | None, bool]:
    """Return (doc, ok). Returns (None, False) on any read or parse failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, False

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None, False

    return (doc if isinstance(doc, dict) else None), True


def _infer_kind(filename: str) -> str:
    lowered = filename.lower()
    if lowered.endswith(".schema.history.yaml"):
        return "history"
    if lowered.endswith(".schema.yaml"):
        return "schema"
    if lowered.endswith(".api.yaml"):
        return "operations"
    if lowered.endswith(".view.yaml"):
        return "view"
    if lowered.endswith(".uimeta.yaml"):
        return "uimeta"
    if lowered.endswith(".viewmeta.yaml"):
        return "viewmeta"
    if "paths" in lowered:
        return "paths"
    if "components" in lowered:
        return "components"
    if "security" in lowered:
        return "security"
    if "resource" in lowered:
        return "resource"
    if "actions" in lowered:
        return "actions"
    if "events" in lowered:
        return "events"
    return "unknown"


def _infer_slug(filename: str) -> str:
    stem = filename
    stem = re.sub(r"\.schema\.history\.yaml$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(
        r"\.s\d+\.(schema|api|view|uimeta|viewmeta)\.yaml$",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    stem = re.sub(r"^objects\.", "", stem, flags=re.IGNORECASE)
    return stem


def _infer_version(filename: str) -> str | None:
    match = VERSION_PATTERN.search(filename)
    return f"s{match.group(1)}" if match else None


def _infer_canonical_name(doc: dict[str, Any] | None, slug: str) -> str | None:
    if not doc:
        return _normalize_canonical_name(slug.split(".")[-1] if slug else None)

    for key in ("object", "name"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_canonical_name(value)

    resource = doc.get("resource")
    if isinstance(resource, dict):
        resource_name = resource.get("name")
        if isinstance(resource_name, str) and resource_name.strip():
            return _normalize_canonical_name(resource_name)
        resource_path = resource.get("path") or resource.get("resource")
        if isinstance(resource_path, str) and resource_path.strip():
            return _normalize_canonical_name(resource_path)

    if isinstance(resource, str) and resource.strip():
        return _normalize_canonical_name(resource)

    return _normalize_canonical_name(slug.split(".")[-1] if slug else None)


def _infer_resource_path(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None

    resource = doc.get("resource")
    if isinstance(resource, str):
        return resource.strip() or None

    if isinstance(resource, dict):
        for key in ("path", "resource"):
            value = resource.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def _infer_title(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    value = doc.get("title") or doc.get("summary") or doc.get("displayName")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _infer_x_mapped_to(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None

    direct = doc.get("x-mappedTo")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    intacct_def = doc.get("x-intacct-definition")
    if isinstance(intacct_def, dict):
        mapped_to = intacct_def.get("mappedTo")
        if isinstance(mapped_to, str) and mapped_to.strip():
            return mapped_to.strip()

    return None


def scan_openapispec(conn: sqlite3.Connection, repo_root: Path) -> ScanStats:
    if yaml is None:
        raise click.ClickException(
            "Missing dependency 'pyyaml'. Install project dependencies and rerun."
        )

    root = repo_root / OPENAPISPEC_ROOT
    if not root.exists():
        raise click.ClickException(f"OpenAPI spec root not found: {root}")
    if not _table_exists(conn, "openapispec_index"):
        raise click.ClickException(
            "Required table openapispec_index is missing. Run the corresponding migration first."
        )

    # Rebuild the index snapshot each run so kind/classification corrections
    # replace stale rows instead of accumulating duplicates.
    conn.execute("DELETE FROM openapispec_index")

    stats = ScanStats()
    for yaml_path in tqdm(
        sorted(root.rglob("*.yaml")), desc="Scanning OpenAPI specs", unit="file"
    ):
        stats.files_processed += 1
        rel_path = yaml_path.relative_to(repo_root).as_posix()
        if _is_template_file(rel_path):
            stats.template_files_skipped += 1
            continue

        file_id = _get_file_id(conn, rel_path)
        if file_id is None:
            stats.files_missing_in_catalog += 1
            continue

        doc, parsed_ok = _parse_yaml(yaml_path)
        if not parsed_ok:
            stats.yaml_parse_failures += 1

        rel_to_root = yaml_path.relative_to(root)
        module = rel_to_root.parts[0] if rel_to_root.parts else None
        filename = yaml_path.name
        slug = _infer_slug(filename)

        conn.execute(
            """
            INSERT OR REPLACE INTO openapispec_index(
                file_id,
                file_path,
                module,
                slug,
                version,
                kind,
                x_mapped_to,
                canonical_name,
                resource_path,
                title,
                state,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)
            """,
            (
                file_id,
                rel_path,
                module,
                slug,
                _infer_version(filename),
                _infer_kind(filename),
                _infer_x_mapped_to(doc),
                _infer_canonical_name(doc, slug),
                _infer_resource_path(doc),
                _infer_title(doc),
            ),
        )
        stats.rows_indexed += 1

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("scan")
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
    help="Path to repository root containing app/source/openapispec.",
)
def scan_command(db: str, repo_root: Path) -> None:
    conn = get_connection(db)
    try:
        stats = scan_openapispec(conn=conn, repo_root=repo_root.resolve())
        conn.commit()
    finally:
        conn.close()

    click.echo(f"Processed openapispec files: {stats.files_processed}")
    click.echo(f"Indexed openapispec files:   {stats.rows_indexed}")
    click.echo(f"Missing in files table:      {stats.files_missing_in_catalog}")
    click.echo(f"Template files skipped:      {stats.template_files_skipped}")
    click.echo(f"YAML parse failures:         {stats.yaml_parse_failures}")


if __name__ == "__main__":
    cli()
