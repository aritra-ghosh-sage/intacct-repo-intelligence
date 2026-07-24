#!/usr/bin/env python3
"""Operator commands for the repository registry."""

from __future__ import annotations

import argparse
import sqlite3

from catalog.db import get_connection
from catalog.repositories import load_workspace_manifest, register_manifest


def register(db: str, manifest_path: str) -> None:
    manifest = load_workspace_manifest(manifest_path)
    conn = get_connection(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = register_manifest(conn, manifest)
        conn.commit()
        for row in rows:
            print(f"registered {row['repo_key']}")
    finally:
        conn.close()


def list_repositories(db: str) -> None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute(
            """SELECT repo_key,tracked_branch,indexed_commit_sha,index_status,last_scanned_at,
                      last_attempt_status,last_attempted_at,last_attempt_error
               FROM repos ORDER BY repo_key"""
        ):
            print(
                f"{row['repo_key']} branch={row['tracked_branch']} "
                f"sha={row['indexed_commit_sha'] or '-'} status={row['index_status']} "
                f"scanned={row['last_scanned_at'] or '-'} "
                f"attempt={row['last_attempt_status']} at={row['last_attempted_at'] or '-'} "
                f"error={row['last_attempt_error'] or '-'}"
            )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage catalog repositories")
    parser.add_argument("--db", default="catalog/catalog.db")
    subcommands = parser.add_subparsers(dest="command", required=True)
    register_parser = subcommands.add_parser("register")
    register_parser.add_argument("--manifest", default="config/workspace_repos.yaml")
    subcommands.add_parser("list")
    args = parser.parse_args()
    if args.command == "register":
        register(args.db, args.manifest)
    else:
        list_repositories(args.db)


if __name__ == "__main__":
    main()
