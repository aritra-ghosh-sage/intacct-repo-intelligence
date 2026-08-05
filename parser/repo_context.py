"""Repository resolution shared by repository-scoped extractors.

The catalog is authoritative for checkout locations.  Extractors must not
silently fall back to a different repository when more than one is indexed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from catalog.repository_lifecycle import require_repository_extractable
from config import REPO_PATH


@dataclass(frozen=True)
class RepoContext:
    id: int
    repo_key: str
    local_root: Path
    tracked_branch: str | None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def resolve_repo(conn: sqlite3.Connection, repo_key: str | None = None) -> RepoContext:
    """Return the requested repository, or the sole registered repository.

    Legacy no-argument invocations remain safe only while exactly one repo is
    registered.  This deliberately fails on older schemas instead of allowing
    a multi-repo run to leak facts through a global path lookup.
    """
    columns = _columns(conn, "repos")
    required = {"id", "repo_key", "local_root", "tracked_branch"}
    missing = required - columns
    if missing:
        raise RuntimeError(
            "Repository-scoped ingestion requires migrated repos columns: "
            + ", ".join(sorted(missing))
        )

    selected = "WHERE repo_key = ?" if repo_key else ""
    params: tuple[str, ...] = (repo_key,) if repo_key else ()
    rows = conn.execute(
        f"SELECT id, repo_key, local_root, tracked_branch FROM repos {selected} "
        "ORDER BY id",
        params,
    ).fetchall()
    if repo_key and not rows:
        raise RuntimeError(f"Unknown repository key: {repo_key}")
    if not repo_key and len(rows) != 1:
        raise RuntimeError(
            "--repo is required unless exactly one repository is registered"
        )

    # Resolve through the shared lifecycle boundary before touching the checkout.
    row = require_repository_extractable(conn, str(rows[0]["repo_key"]))
    root = Path(row["local_root"] or REPO_PATH).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"Repository checkout does not exist: {root}")
    return RepoContext(
        id=int(row["id"]),
        repo_key=str(row["repo_key"]),
        local_root=root,
        tracked_branch=row["tracked_branch"],
    )


def require_repo_scoped_files(conn: sqlite3.Connection) -> None:
    if "repo_id" not in _columns(conn, "files"):
        raise RuntimeError(
            "Repository-scoped ingestion requires files.repo_id; apply catalog migrations first"
        )
