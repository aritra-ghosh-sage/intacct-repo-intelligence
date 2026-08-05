"""Small, reusable primitives for atomic catalog candidate promotion.

These primitives intentionally know nothing about a particular refresh mode or
builder.  Callers are responsible for creating and validating a candidate;
this module only makes the parent snapshot and promotion race safe.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class CatalogPromotionError(RuntimeError):
    """A candidate cannot safely replace the active catalog."""


@dataclass(frozen=True)
class ParentDescriptor:
    catalog_build_id: int
    build_token: str
    content_fingerprint: str | None
    source_revisions_json: str
    device: int | None
    inode: int | None


def backup_database(source: Path, target: Path) -> None:
    """Make a consistent SQLite backup without mutating ``source``."""

    source_conn = sqlite3.connect(source)
    source_conn.execute("PRAGMA foreign_keys = ON")
    try:
        target_conn = sqlite3.connect(target)
        target_conn.execute("PRAGMA foreign_keys = ON")
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def parent_descriptor(active: Path) -> ParentDescriptor:
    """Return the active generation and filesystem identity used for CAS."""

    stat = active.stat()
    conn = sqlite3.connect(f"file:{active}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT id,build_token,content_fingerprint,source_revisions_json
               FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if row is None:
            return ParentDescriptor(0, "", None, "{}", stat.st_dev, stat.st_ino)
        return ParentDescriptor(
            int(row["id"]),
            str(row["build_token"]),
            str(row["content_fingerprint"])
            if row["content_fingerprint"] is not None
            else None,
            str(row["source_revisions_json"]),
            getattr(stat, "st_dev", None),
            getattr(stat, "st_ino", None),
        )
    finally:
        conn.close()


def assert_parent_unchanged(active: Path, expected: ParentDescriptor) -> None:
    if parent_descriptor(active) != expected:
        raise CatalogPromotionError(
            "parent-generation compare-and-swap failed: active catalog changed"
        )


@contextmanager
def refresh_lock(active: Path):
    """Serialize candidate promotion for one active catalog path."""

    lock_path = active.with_name(active.name + ".refresh.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield lock_path
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def promote_catalog_candidate(
    active: Path,
    candidate: Path,
    previous: Path,
    build_token: str,
) -> None:
    """Atomically promote an already-validated candidate.

    The active file is retained as ``previous`` for recovery.  ``build_token``
    is checked from the candidate so a stale or swapped candidate cannot be
    promoted accidentally.
    """

    conn = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT 1 FROM catalog_builds WHERE build_token=? AND status='active'",
            (build_token,),
        ).fetchone()
        if row is None:
            raise CatalogPromotionError("candidate does not contain active build token")
    finally:
        conn.close()
    previous_stage = previous.with_name(f"{previous.name}.stage.{build_token}")
    previous_backup = previous.with_name(f"{previous.name}.backup.{build_token}")
    previous_stage.unlink(missing_ok=True)
    previous_backup.unlink(missing_ok=True)
    promoted = False
    try:
        # Copy rather than rename the active generation: a failed replacement
        # must leave both active and retained previous recovery state intact.
        backup_database(active, previous_stage)
        if previous.exists():
            # Retain the exact recovery artifact, rather than a SQLite backup
            # whose valid bytes may differ due to page layout.
            os.replace(previous, previous_backup)
        os.replace(candidate, active)
        promoted = True
        os.replace(previous_stage, previous)
    except Exception:
        if promoted and previous_stage.exists():
            os.replace(previous_stage, active)
        if previous_backup.exists():
            os.replace(previous_backup, previous)
        raise
    finally:
        previous_stage.unlink(missing_ok=True)
        previous_backup.unlink(missing_ok=True)
