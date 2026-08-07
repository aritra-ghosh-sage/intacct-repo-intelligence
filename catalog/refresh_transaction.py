"""Small, reusable primitives for atomic catalog candidate promotion.

These primitives intentionally know nothing about a particular refresh mode or
builder.  Callers are responsible for creating and validating a candidate;
this module only makes the parent snapshot and promotion race safe.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Collection, Mapping
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


def parent_descriptor(
    active: Path,
    *,
    expected_schema: Mapping[str, frozenset[str]] | None = None,
    allowed_missing_tables: Collection[str] = (),
) -> ParentDescriptor:
    """Return the active generation and filesystem identity used for CAS.

    ``expected_schema`` lets a workflow reject a readable database belonging
    to a different catalog schema before creating a candidate. A caller may
    explicitly allow a known additive set of missing tables while it builds a
    complete replacement candidate. Callers that do not supply it retain the
    historical generic descriptor behavior.
    """

    if not active.exists():
        return ParentDescriptor(0, "", None, "{}", None, None)
    stat = active.stat()
    if stat.st_size == 0:
        raise CatalogPromotionError(
            f"active catalog is empty: {active}; remove it for deliberate fresh "
            f"initialization or restore {active.name}.previous before retrying"
        )
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{active}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(catalog_builds)")
        }
        if "source_revisions_json" not in columns:
            raise CatalogPromotionError(
                f"active catalog is missing catalog_builds.source_revisions_json: {active}; "
                f"restore {active.name}.previous or remove it for deliberate fresh "
                "initialization"
            )
        if expected_schema is not None:
            actual_schema = {
                str(table_row[0]): {
                    str(column_row[1])
                    for column_row in conn.execute(
                        f'PRAGMA table_info("{table_row[0]}")'
                    )
                }
                for table_row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            missing_tables = sorted(
                (set(expected_schema) - set(actual_schema))
                - set(allowed_missing_tables)
            )
            unexpected_tables = sorted(set(actual_schema) - set(expected_schema))
            missing_columns = {
                table: sorted(expected_schema[table] - actual_schema.get(table, set()))
                for table in expected_schema
                if table in actual_schema
                and expected_schema[table] - actual_schema[table]
            }
            mismatched_columns = {
                table: sorted(actual_schema[table] - expected_schema[table])
                for table in expected_schema
                if table in actual_schema
                and actual_schema[table] - expected_schema[table]
            }
            if (
                missing_tables
                or unexpected_tables
                or missing_columns
                or mismatched_columns
            ):
                details: list[str] = []
                if missing_tables:
                    details.append(f"missing tables: {', '.join(missing_tables)}")
                if unexpected_tables:
                    details.append(f"unexpected tables: {', '.join(unexpected_tables)}")
                if missing_columns:
                    details.append(
                        "missing columns: "
                        + ", ".join(
                            f"{table}({', '.join(columns)})"
                            for table, columns in sorted(missing_columns.items())
                        )
                    )
                if mismatched_columns:
                    details.append(
                        "unexpected columns: "
                        + ", ".join(
                            f"{table}({', '.join(columns)})"
                            for table, columns in sorted(mismatched_columns.items())
                        )
                    )
                raise CatalogPromotionError(
                    f"active catalog schema is incompatible with the requested workflow: {active}; "
                    + "; ".join(details)
                    + f"; restore {active.name}.previous or remove it for deliberate fresh "
                    "initialization"
                )
        fingerprint = (
            "content_fingerprint" if "content_fingerprint" in columns else "NULL"
        )
        row = conn.execute(
            f"""SELECT id,build_token,{fingerprint} AS content_fingerprint,source_revisions_json
               FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if row is None:
            if expected_schema is not None:
                raise CatalogPromotionError(
                    f"active catalog has no active build: {active}; restore "
                    f"{active.name}.previous or remove it for deliberate fresh initialization"
                )
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
    except sqlite3.DatabaseError as exc:
        raise CatalogPromotionError(
            f"active catalog is not a readable SQLite database: {active}; "
            f"restore {active.name}.previous or remove it for deliberate fresh "
            "initialization"
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def assert_parent_unchanged(
    active: Path,
    expected: ParentDescriptor,
    *,
    expected_schema: Mapping[str, frozenset[str]] | None = None,
    allowed_missing_tables: Collection[str] = (),
) -> None:
    if (
        parent_descriptor(
            active,
            expected_schema=expected_schema,
            allowed_missing_tables=allowed_missing_tables,
        )
        != expected
    ):
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
    had_active = active.exists()
    try:
        # Copy rather than rename the active generation: a failed replacement
        # must leave both active and retained previous recovery state intact.
        if had_active:
            backup_database(active, previous_stage)
        if had_active and previous.exists():
            # Retain the exact recovery artifact, rather than a SQLite backup
            # whose valid bytes may differ due to page layout.
            os.replace(previous, previous_backup)
        os.replace(candidate, active)
        promoted = True
        if previous_stage.exists():
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
