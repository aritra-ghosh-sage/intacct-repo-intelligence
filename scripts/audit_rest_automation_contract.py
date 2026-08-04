"""Read-only Contract-V1 source audit for REST automation evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

try:
    from catalog.repositories import load_workspace_manifest
    from catalog.rest_automation_contract import (
        RestAutomationContractError,
        audit_contract_v1,
        resolve_contract_v1_paths,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.repositories import load_workspace_manifest
    from catalog.rest_automation_contract import (
        RestAutomationContractError,
        audit_contract_v1,
        resolve_contract_v1_paths,
    )


def audit(
    suite_root: Path,
    object_mapping: str,
    version_compatibility: str,
    non_request_inventory: str,
) -> tuple[dict[str, str], ...]:
    """Validate and hash the three Contract-V1 files without writing anything."""
    paths = resolve_contract_v1_paths(
        {
            "features_root": ".",
            "object_mapping": object_mapping,
            "version_compatibility": version_compatibility,
            "non_request_inventory": non_request_inventory,
        },
        suite_root,
    )
    return tuple(
        {"field": item.field, "path": item.path, "sha256": item.sha256}
        for item in audit_contract_v1(paths)
    )


def audit_manifest(manifest_path: Path, repo_key: str) -> tuple[dict[str, str], ...]:
    """Audit one manifest-declared Contract-V1 suite without writing catalog state."""
    manifest = load_workspace_manifest(manifest_path)
    entry = next(
        (item for item in manifest["repositories"] if item["repo_key"] == repo_key),
        None,
    )
    if entry is None:
        raise RestAutomationContractError(f"repository is not declared: {repo_key}")
    config = entry.get("rest_automation")
    if not isinstance(config, dict) or config.get("coverage_contract_version") != 1:
        raise RestAutomationContractError(
            f"repository {repo_key} does not declare Contract-V1 REST automation inputs"
        )
    paths = resolve_contract_v1_paths(config, Path(entry["local_root"]))
    return tuple(
        {"field": item.field, "path": item.path, "sha256": item.sha256}
        for item in audit_contract_v1(paths)
    )


@click.command()
@click.option("--manifest", "manifest_path", type=click.Path(path_type=Path))
@click.option("--repo")
@click.option("--suite-root", type=click.Path(path_type=Path, file_okay=False))
@click.option("--object-mapping")
@click.option("--version-compatibility")
@click.option("--non-request-inventory")
def main(
    manifest_path: Path | None,
    repo: str | None,
    suite_root: Path | None,
    object_mapping: str | None,
    version_compatibility: str | None,
    non_request_inventory: str | None,
) -> None:
    """Report the verified Contract-V1 source inputs as deterministic JSON."""
    try:
        if manifest_path is not None or repo is not None:
            if manifest_path is None or not repo:
                raise RestAutomationContractError(
                    "--manifest and --repo must be provided together"
                )
            if any(
                value is not None
                for value in (
                    suite_root,
                    object_mapping,
                    version_compatibility,
                    non_request_inventory,
                )
            ):
                raise RestAutomationContractError(
                    "manifest audit cannot be combined with direct contract paths"
                )
            records = audit_manifest(manifest_path, repo)
        else:
            if (
                suite_root is None
                or object_mapping is None
                or version_compatibility is None
                or non_request_inventory is None
            ):
                raise RestAutomationContractError(
                    "direct audit requires --suite-root and all three contract file paths"
                )
            records = audit(
                suite_root,
                object_mapping,
                version_compatibility,
                non_request_inventory,
            )
    except (RestAutomationContractError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"status": "ok", "artifacts": records}, sort_keys=True))


if __name__ == "__main__":
    main()
