"""Manifest-backed checkout resolution for PR impact tools."""

from __future__ import annotations

from pathlib import Path

from catalog.repositories import RepositoryError, load_workspace_manifest


def resolve_manifest_repo_root(manifest_path: str | Path, repo_key: str) -> Path:
    """Resolve one manifest repository key to an existing absolute checkout."""

    try:
        manifest = load_workspace_manifest(manifest_path)
    except RepositoryError as exc:
        raise ValueError(f"manifest_invalid: {exc}") from exc

    matches = [
        entry for entry in manifest["repositories"] if entry.get("repo_key") == repo_key
    ]
    if len(matches) != 1:
        raise ValueError(f"repo_not_found: manifest must contain exactly one {repo_key!r} entry")

    root = Path(str(matches[0]["local_root"])).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"repo_root_unavailable: checkout root does not exist: {root}")
    return root
