"""Repository registry helpers for the shared catalog.

The registry is deliberately small: it owns stable repository identity and
operator-local checkout configuration.  Extractors should receive a resolved
repository record instead of reading ``config.REPO_PATH`` directly.
"""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Any

import yaml


class RepositoryError(ValueError):
    """Raised when workspace repository configuration is invalid."""


def load_workspace_manifest(path: str | Path) -> dict[str, Any]:
    """Load and validate a version 1 workspace repository manifest.

    The returned mapping is suitable for registration.  It intentionally does
    not resolve or inspect checkout paths; that is an indexing-time concern.
    """

    manifest_path = Path(path)
    try:
        document = yaml.safe_load(manifest_path.read_text())
    except OSError as exc:
        raise RepositoryError(f"cannot read workspace manifest {manifest_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RepositoryError(f"invalid YAML in workspace manifest {manifest_path}: {exc}") from exc

    if not isinstance(document, dict) or document.get("version") != 1:
        raise RepositoryError("workspace manifest must be a mapping with version: 1")
    repositories = document.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise RepositoryError("workspace manifest must contain a non-empty repositories list")

    seen: set[str] = set()
    for entry in repositories:
        if not isinstance(entry, dict):
            raise RepositoryError("each repository entry must be a mapping")
        missing = [key for key in ("repo_key", "local_root", "tracked_branch") if not entry.get(key)]
        if missing:
            raise RepositoryError(f"repository entry is missing {', '.join(missing)}")
        repo_key = str(entry["repo_key"])
        if repo_key in seen:
            raise RepositoryError(f"duplicate repo_key in workspace manifest: {repo_key}")
        seen.add(repo_key)
        builders = entry.get("builders", [])
        if not isinstance(builders, list) or not all(isinstance(item, str) and item for item in builders):
            raise RepositoryError(f"repository {repo_key} builders must be a list of non-empty strings")
    return document


def get_repository(conn: sqlite3.Connection, repo_key: str) -> sqlite3.Row:
    """Return one repository by stable key or raise ``RepositoryError``."""

    row = conn.execute("SELECT * FROM repos WHERE repo_key = ?", (repo_key,)).fetchone()
    if row is None:
        raise RepositoryError(f"unknown repository: {repo_key}")
    return row


def resolve_repository_root(conn: sqlite3.Connection, repo_key: str) -> Path:
    """Return an existing checkout root registered for ``repo_key``.

    This checks only the configured filesystem location.  Git branch/SHA and
    cleanliness checks belong to the refresh coordinator.
    """

    root = Path(get_repository(conn, repo_key)["local_root"]).expanduser()
    if not root.is_dir():
        raise RepositoryError(f"repository {repo_key} checkout root does not exist: {root}")
    return root.resolve()


def register_manifest(conn: sqlite3.Connection, manifest: dict[str, Any]) -> list[sqlite3.Row]:
    """Upsert manifest repositories and return their catalog rows.

    The caller owns the transaction so registration can be coordinated with
    migrations or candidate catalog creation.
    """

    rows: list[sqlite3.Row] = []
    for entry in manifest["repositories"]:
        builders = entry.get("builders", [])
        conn.execute(
            """
            INSERT INTO repos (
                repo_key, name, kind, language, remote_url, local_root,
                tracked_branch, enabled, profile, effective_builders_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo_key) DO UPDATE SET
                name=excluded.name, kind=excluded.kind, language=excluded.language,
                remote_url=excluded.remote_url, local_root=excluded.local_root,
                tracked_branch=excluded.tracked_branch, enabled=excluded.enabled,
                profile=excluded.profile, effective_builders_json=excluded.effective_builders_json
            """,
            (
                entry["repo_key"], entry.get("name"), entry.get("kind"),
                entry.get("language"), entry.get("remote_url"), entry["local_root"],
                entry["tracked_branch"], int(entry.get("enabled", True)),
                entry.get("profile"), json.dumps(builders, separators=(",", ":")),
            ),
        )
        rows.append(get_repository(conn, entry["repo_key"]))
    return rows
