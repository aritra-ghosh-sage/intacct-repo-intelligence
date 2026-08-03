"""Build isolated API Registry evidence from the three Registry JSON sources."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

try:
    from catalog.api_registry import RegistryExtractionError, build_registry
    from catalog.db import get_connection, require_foreign_key_integrity
    from catalog.repositories import get_repository, resolve_repository_root
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.api_registry import RegistryExtractionError, build_registry
    from catalog.db import get_connection, require_foreign_key_integrity
    from catalog.repositories import get_repository, resolve_repository_root


DEFAULT_DB = "catalog/catalog.db"


def build(db: str, repo_root: Path, repo_key: str) -> dict[str, int]:
    """Build Registry-only evidence for one registered repository."""
    conn = get_connection(db)
    try:
        repository = get_repository(conn, repo_key)
        repo_id = int(repository["id"])
        conn.execute("BEGIN IMMEDIATE")
        stats = build_registry(conn, repo_id=repo_id, repo_root=repo_root)
        require_foreign_key_integrity(conn, context="API Registry build")
        conn.commit()
        return {
            "entries": stats.entries,
            "source_links": stats.source_links,
            "source_optional": stats.source_optional,
        }
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


@click.command()
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--repo", "repo_key", required=True, help="Registered repository key.")
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=None,
    help="Repository root containing the exact Registry source files.",
)
def main(db: str, repo_key: str, repo_root: Path | None) -> None:
    """Build only API Registry evidence; no OpenAPI/UI/entity/REST facts change."""
    conn = get_connection(db)
    try:
        get_repository(conn, repo_key)
        root = repo_root.resolve() if repo_root is not None else resolve_repository_root(conn, repo_key)
    finally:
        conn.close()
    try:
        click.echo(json.dumps(build(db, root, repo_key), sort_keys=True))
    except RegistryExtractionError as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
