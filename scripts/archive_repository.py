"""Operator entry point for source-less repository archival."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from catalog.archive_ownership import target_row_counts
from catalog.archive_repository import archive_repository
from catalog.repository_lifecycle import (
    RepositoryArchivedError,
    verify_github_repository_active,
)


def _github_confirms_archived(repo: sqlite3.Row) -> bool:
    """Use the lifecycle provider check, accepting only its archived result."""

    try:
        verify_github_repository_active(
            remote_url=repo["remote_url"],
            root=Path(str(repo["local_root"])),
            branch=str(repo["tracked_branch"]),
        )
    except RepositoryArchivedError:
        return True
    return False


def status(db_path: str | Path, repo_key: str) -> dict[str, object]:
    """Read lifecycle state and target-owned evidence counts without mutation."""

    conn = sqlite3.connect(Path(db_path))
    conn.row_factory = sqlite3.Row
    try:
        repo = conn.execute("SELECT * FROM repos WHERE repo_key=?", (repo_key,)).fetchone()
        if repo is None:
            raise ValueError(f"unknown repository: {repo_key}")
        return {
            "repo_key": repo_key,
            "lifecycle_state": repo["lifecycle_state"],
            "archive_source": repo["archive_source"],
            "archive_reason": repo["archive_reason"],
            "archived_at": repo["archived_at"],
            "target_owned_counts": target_row_counts(conn, int(repo["id"])),
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="catalog/catalog.db", help="active catalog SQLite path")
    subcommands = parser.add_subparsers(dest="command", required=True)
    status_parser = subcommands.add_parser("status", help="read archival state")
    status_parser.add_argument("repo_key")
    archive_parser = subcommands.add_parser("archive", help="archive one repository via candidate promotion")
    archive_parser.add_argument("repo_key")
    archive_parser.add_argument("--source", choices=("manual", "github"), required=True)
    archive_parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status(args.db, args.repo_key), sort_keys=True, indent=2))
        return
    result = archive_repository(
        args.db,
        args.repo_key,
        source=args.source,
        reason=args.reason,
        github_archive_verifier=_github_confirms_archived if args.source == "github" else None,
    )
    print(json.dumps(result.__dict__, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
