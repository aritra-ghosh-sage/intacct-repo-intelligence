#!/usr/bin/env python3
"""Read-only audit for catalog-owned REST automation static evidence."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import click

try:
    from catalog.rest_automation_contract import (
        STATIC_MAP_PATH,
        RestAutomationContractError,
        audit_static_entry,
        load_static_map,
        static_map_hashes,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.rest_automation_contract import (
        STATIC_MAP_PATH,
        RestAutomationContractError,
        audit_static_entry,
        load_static_map,
        static_map_hashes,
    )


def audit_static_map(conn: sqlite3.Connection, target_repo: str, map_path: Path = STATIC_MAP_PATH) -> list[dict[str, str]]:
    """Verify every reviewed entry against indexed ia-main evidence only."""
    target = conn.execute("SELECT id FROM repos WHERE repo_key=?", (target_repo,)).fetchone()
    if target is None:
        raise RestAutomationContractError(f"target repository is not indexed: {target_repo}")
    records = static_map_hashes(map_path)
    for entry in load_static_map(map_path):
        if entry.target_repo != target_repo:
            continue
        _entity, _endpoint, cited = audit_static_entry(conn, entry, production_repo_id=int(target[0]))
        records.extend(cited)
    # A source listed twice must agree, otherwise the map's own selectors are contradictory.
    return sorted(records, key=lambda item: (item["field"], item["path"]))


@click.command()
@click.option("--db", "db_path", type=click.Path(path_type=Path, exists=True))
@click.option("--target-repo", default="ia-main", show_default=True)
@click.option("--map", "map_path", type=click.Path(path_type=Path), default=STATIC_MAP_PATH)
@click.option("--suite-root", type=click.Path(path_type=Path, file_okay=False))
@click.option("--object-mapping")
@click.option("--version-compatibility")
@click.option("--non-request-inventory")
@click.option("--manifest", "manifest_path", type=click.Path(path_type=Path))
@click.option("--repo")
def main(db_path: Path | None, target_repo: str, map_path: Path, suite_root: Path | None,
         object_mapping: str | None, version_compatibility: str | None,
         non_request_inventory: str | None, manifest_path: Path | None, repo: str | None) -> None:
    """Print deterministic hashes after a read-only static-map audit."""
    try:
        # Compatibility-only inspection for obsolete target-owned V1 inputs.
        if db_path is None:
            from catalog.rest_automation_contract import (
                audit_contract_v1,
                resolve_contract_v1_paths,
            )
            if manifest_path is not None:
                from catalog.repositories import load_workspace_manifest
                manifest = load_workspace_manifest(manifest_path)
                entry = next((item for item in manifest["repositories"] if item["repo_key"] == repo), None)
                if not entry or entry.get("rest_automation", {}).get("coverage_contract_version") != 1:
                    raise RestAutomationContractError("repository does not declare Contract-V1")
                paths = resolve_contract_v1_paths(entry["rest_automation"], Path(entry["local_root"]))
            else:
                if not all((suite_root, object_mapping, version_compatibility, non_request_inventory)):
                    raise RestAutomationContractError("--db is required for static-map audit")
                paths = resolve_contract_v1_paths({"features_root": ".", "object_mapping": object_mapping,
                    "version_compatibility": version_compatibility, "non_request_inventory": non_request_inventory}, suite_root)
            records = [{"field": x.field, "path": x.path, "sha256": x.sha256} for x in audit_contract_v1(paths)]
            click.echo(json.dumps({"status": "ok", "artifacts": records}, sort_keys=True))
            return
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            records = audit_static_map(conn, target_repo, map_path)
        finally:
            conn.close()
    except (RestAutomationContractError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"status": "ok", "artifacts": records}, sort_keys=True))


if __name__ == "__main__":
    main()
