"""Source revision contracts for queryable catalog generations."""

from __future__ import annotations

import sqlite3


def active_source_revisions(conn: sqlite3.Connection) -> dict[str, str | None]:
    """Return the exact source revisions represented by active repositories.

    Lifecycle state is catalog evidence, not refresh-attempt metadata.  Archived
    repositories are intentionally absent from new active generation contracts.
    The compatibility fallback supports pre-029 catalogs while they are being
    migrated inside a candidate.
    """

    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(repos)")}
    where = "WHERE lifecycle_state='active'" if "lifecycle_state" in columns else ""
    return {
        str(row[0]): row[1]
        for row in conn.execute(
            "SELECT repo_key,indexed_commit_sha FROM repos " + where + " ORDER BY repo_key"
        )
    }
