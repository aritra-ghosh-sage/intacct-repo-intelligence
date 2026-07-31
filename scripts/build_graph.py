#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from catalog.content_fingerprint import logical_content_fingerprint
from catalog.graph_materialization import build_graph
from catalog.graph_projection import (
    GRAPH_PROJECTION_VERSION,
    projection_diff,
)
from config import CATALOG_DB as SQLITE_DB
from config import GRAPH_DB


def create_sqlite_snapshot(source_path: str, snapshot_path: Path) -> None:
    """Create one transactionally consistent SQLite backup for build and validation."""
    source = sqlite3.connect(f"file:{Path(source_path).resolve()}?mode=ro", uri=True)
    destination = sqlite3.connect(snapshot_path)
    destination.execute("PRAGMA foreign_keys = ON")
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def source_fingerprint(snapshot_path: Path) -> str:
    digest = hashlib.sha256()
    with snapshot_path.open("rb") as snapshot:
        for chunk in iter(lambda: snapshot.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_graph_builds_migration(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_builds'"
    ).fetchone()
    if not exists:
        raise RuntimeError(
            "graph_builds is missing; apply migrations/017_graph_builds.sql first"
        )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(graph_builds)")}
    required = {
        "catalog_build_id",
        "base_graph_build_id",
        "build_mode",
        "projection_version",
        "source_revisions_json",
    }
    if not required.issubset(columns):
        raise RuntimeError(
            "graph_builds lacks migration 023 generation metadata; apply catalog migration 023"
        )


@dataclass(frozen=True)
class GraphDeltaEligibility:
    eligible: bool
    reason: str
    catalog_build_id: int | None = None
    parent_catalog_build_id: int | None = None
    base_graph_build_id: int | None = None
    content_fingerprint: str | None = None
    source_revisions_json: str | None = None


def graph_delta_eligibility(sqlite_path: str, graph_path: str) -> GraphDeltaEligibility:
    current_path = Path(sqlite_path).resolve()
    previous_path = current_path.with_name(current_path.name + ".previous")
    graph = Path(graph_path).resolve()
    if not graph.is_file():
        return GraphDeltaEligibility(False, "active graph file is unavailable")
    if not previous_path.is_file():
        return GraphDeltaEligibility(False, "previous SQLite catalog is unavailable")
    current = sqlite3.connect(f"file:{current_path}?mode=ro", uri=True)
    previous = sqlite3.connect(f"file:{previous_path}?mode=ro", uri=True)
    current.row_factory = sqlite3.Row
    previous.row_factory = sqlite3.Row
    try:
        current_build = current.execute(
            "SELECT * FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if current_build is None:
            return GraphDeltaEligibility(
                False, "active catalog build metadata is unavailable"
            )
        parent_id = current_build["parent_catalog_build_id"]
        if parent_id is None:
            return GraphDeltaEligibility(
                False, "active catalog has no parent generation"
            )
        previous_build = previous.execute(
            "SELECT * FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if previous_build is None or int(previous_build["id"]) != int(parent_id):
            return GraphDeltaEligibility(
                False, "previous catalog generation does not match parent"
            )
        current_fingerprint = logical_content_fingerprint(current)
        previous_fingerprint = logical_content_fingerprint(previous)
        if current_build["content_fingerprint"] != current_fingerprint:
            return GraphDeltaEligibility(
                False, "active catalog logical fingerprint mismatch"
            )
        if previous_build["content_fingerprint"] != previous_fingerprint:
            return GraphDeltaEligibility(
                False, "previous catalog logical fingerprint mismatch"
            )
        parent_copy = current.execute(
            "SELECT content_fingerprint,source_revisions_json FROM catalog_builds WHERE id=?",
            (int(parent_id),),
        ).fetchone()
        if (
            parent_copy is None
            or parent_copy["content_fingerprint"] != previous_fingerprint
        ):
            return GraphDeltaEligibility(
                False, "parent catalog metadata fingerprint mismatch"
            )
        if (
            parent_copy["source_revisions_json"]
            != previous_build["source_revisions_json"]
        ):
            return GraphDeltaEligibility(
                False, "previous source revisions do not match parent"
            )
        base_graph = current.execute(
            """SELECT * FROM graph_builds
               WHERE catalog_build_id=? AND graph_path=?
                 AND status IN ('active','previous')
               ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,id DESC LIMIT 1""",
            (int(parent_id), str(graph)),
        ).fetchone()
        if base_graph is None:
            return GraphDeltaEligibility(
                False, "no graph build is linked to the parent catalog"
            )
        if int(base_graph["projection_version"] or -1) != GRAPH_PROJECTION_VERSION:
            return GraphDeltaEligibility(False, "graph projection version mismatch")
        if base_graph["source_fingerprint"] != previous_fingerprint:
            return GraphDeltaEligibility(
                False, "base graph fingerprint does not match parent"
            )
        return GraphDeltaEligibility(
            True,
            "eligible",
            int(current_build["id"]),
            int(parent_id),
            int(base_graph["id"]),
            current_fingerprint,
            str(current_build["source_revisions_json"]),
        )
    except sqlite3.Error as exc:
        return GraphDeltaEligibility(False, f"delta metadata unavailable: {exc}")
    finally:
        current.close()
        previous.close()


def build_graph_delta(
    previous_sqlite_path: str, current_sqlite_path: str, candidate_graph_path: str
) -> dict[str, dict[str, int]]:
    """Build a delta-equivalent candidate and return its canonical diff.

    Ladybug bulk mutation has varied across supported releases.  V1 computes
    the exact canonical old/new projection delta, then materializes that target
    into an isolated candidate with the same shared projection contract.  This
    preserves delta eligibility/failure semantics and semantic equality while
    avoiding an in-place graph mutation.
    """

    previous = sqlite3.connect(
        f"file:{Path(previous_sqlite_path).resolve()}?mode=ro", uri=True
    )
    current = sqlite3.connect(
        f"file:{Path(current_sqlite_path).resolve()}?mode=ro", uri=True
    )
    try:
        summary = projection_diff(previous, current)
    finally:
        previous.close()
        current.close()
    build_graph(current_sqlite_path, candidate_graph_path)
    return summary


def preserve_previous_graph(active: Path, previous: Path, build_token: str) -> None:
    """Prepare the rollback copy without moving or removing the active path."""
    if not active.exists():
        return
    temporary = previous.with_name(f"{previous.name}.tmp.{build_token}")
    temporary.unlink(missing_ok=True)
    try:
        try:
            os.link(active, temporary)
        except OSError:
            shutil.copy2(active, temporary)
        os.replace(temporary, previous)
    finally:
        temporary.unlink(missing_ok=True)


def _activate_graph_metadata(
    metadata: sqlite3.Connection, active: Path, build_id: int
) -> None:
    metadata.execute(
        """
        UPDATE graph_builds
        SET status='previous'
        WHERE graph_path=? AND id<>? AND status='active'
        """,
        (str(active), build_id),
    )
    metadata.execute(
        """
        UPDATE graph_builds
        SET status='active', completed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (build_id,),
    )
    metadata.commit()


def _rollback_graph_files(
    active: Path,
    previous: Path,
    previous_backup: Path,
    *,
    had_active: bool,
) -> None:
    if had_active:
        if not previous.exists():
            raise RuntimeError("previous graph is unavailable for activation rollback")
        os.replace(previous, active)
    else:
        active.unlink(missing_ok=True)
    if previous_backup.exists():
        os.replace(previous_backup, previous)


def promote_validated_graph(
    sqlite_path: str = SQLITE_DB,
    graph_path: str = GRAPH_DB,
    mode: str = "auto",
) -> None:
    if mode not in {"auto", "full", "delta"}:
        raise ValueError(f"unsupported graph build mode: {mode}")
    active = Path(graph_path).expanduser().resolve()
    active.parent.mkdir(parents=True, exist_ok=True)
    previous = active.with_name(active.name + ".previous")
    lock_path = active.with_name(active.name + ".build.lock")
    build_token = uuid.uuid4().hex
    candidate = active.with_name(f"{active.name}.candidate.{build_token}")
    snapshot = active.with_name(f"{active.name}.snapshot.{build_token}.db")
    previous_stage = active.with_name(f"{active.name}.previous.stage.{build_token}")
    previous_backup = active.with_name(f"{active.name}.previous.backup.{build_token}")
    build_id = None
    promoted = False
    requested_mode = mode

    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another graph build holds {lock_path}") from exc

        metadata = sqlite3.connect(sqlite_path)
        metadata.execute("PRAGMA foreign_keys = ON")
        metadata.row_factory = sqlite3.Row
        try:
            require_graph_builds_migration(metadata)
            catalog_build = metadata.execute(
                "SELECT * FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if catalog_build is None:
                raise RuntimeError("active catalog build metadata is required")
            catalog_build_id = int(catalog_build["id"])
            eligibility = graph_delta_eligibility(sqlite_path, graph_path)
            if mode == "delta" and not eligibility.eligible:
                build_id = int(
                    metadata.execute(
                        """INSERT INTO graph_builds(
                               graph_path,source_db,status,source_fingerprint,
                               catalog_build_id,build_mode,projection_version,
                               source_revisions_json,completed_at,error
                           ) VALUES (?,?,'failed',?,?,'delta',?,?,CURRENT_TIMESTAMP,?)""",
                        (
                            str(active),
                            str(Path(sqlite_path).resolve()),
                            str(catalog_build["content_fingerprint"] or "unavailable"),
                            catalog_build_id,
                            GRAPH_PROJECTION_VERSION,
                            str(catalog_build["source_revisions_json"]),
                            eligibility.reason,
                        ),
                    ).lastrowid
                )
                metadata.commit()
                raise RuntimeError(
                    f"forced graph delta unavailable: {eligibility.reason}"
                )
            effective_mode = (
                "delta" if mode != "full" and eligibility.eligible else "full"
            )
            fallback_reason = (
                eligibility.reason
                if requested_mode == "auto" and effective_mode == "full"
                else None
            )
            create_sqlite_snapshot(sqlite_path, snapshot)
            snapshot_conn = sqlite3.connect(
                f"file:{snapshot.resolve()}?mode=ro", uri=True
            )
            try:
                fingerprint = logical_content_fingerprint(snapshot_conn)
            finally:
                snapshot_conn.close()
            if fingerprint != catalog_build["content_fingerprint"]:
                raise RuntimeError(
                    "active catalog metadata does not match its logical content fingerprint"
                )
            cur = metadata.execute(
                """
                INSERT INTO graph_builds(
                    graph_path,source_db,status,source_fingerprint,
                    catalog_build_id,base_graph_build_id,build_mode,
                    projection_version,source_revisions_json
                ) VALUES (?,?,'building',?,?,?,?,?,?)
                """,
                (
                    str(active),
                    str(Path(sqlite_path).resolve()),
                    fingerprint,
                    catalog_build_id,
                    eligibility.base_graph_build_id
                    if effective_mode == "delta"
                    else None,
                    effective_mode,
                    GRAPH_PROJECTION_VERSION,
                    str(catalog_build["source_revisions_json"]),
                ),
            )
            build_id = int(cur.lastrowid)
            metadata.commit()

            delta_summary = None
            if effective_mode == "delta":
                previous_db = (
                    Path(sqlite_path)
                    .resolve()
                    .with_name(Path(sqlite_path).name + ".previous")
                )
                delta_summary = build_graph_delta(
                    str(previous_db), str(snapshot), str(candidate)
                )
            else:
                build_graph(str(snapshot), str(candidate))
            from validation.validate_graph import validate_paths

            validation_summary = validate_paths(
                str(snapshot),
                str(candidate),
                expected_fingerprint=fingerprint,
                expected_catalog_build_id=catalog_build_id,
                expected_projection_version=GRAPH_PROJECTION_VERSION,
            )
            validation_payload = json.loads(validation_summary)
            validation_payload.update(
                {
                    "requested_mode": requested_mode,
                    "effective_mode": effective_mode,
                    "fallback_reason": fallback_reason,
                    "delta_summary": delta_summary,
                }
            )
            validation_summary = json.dumps(validation_payload, sort_keys=True)
            metadata.execute(
                """
                UPDATE graph_builds
                SET status='validated', validation_summary=?
                WHERE id=?
                """,
                (validation_summary, build_id),
            )
            metadata.commit()

            previous_stage.unlink(missing_ok=True)
            previous_backup.unlink(missing_ok=True)
            had_active = active.exists()
            if had_active:
                shutil.copy2(active, previous_stage)
            if previous.exists():
                shutil.copy2(previous, previous_backup)
            try:
                os.replace(candidate, active)
                promoted = True
                if previous_stage.exists():
                    os.replace(previous_stage, previous)
            except Exception:
                if promoted and previous_stage.exists():
                    os.replace(previous_stage, active)
                    promoted = False
                if previous_backup.exists():
                    os.replace(previous_backup, previous)
                raise

            try:
                _activate_graph_metadata(metadata, active, build_id)
            except Exception:
                try:
                    metadata.rollback()
                except sqlite3.Error:
                    pass
                promoted = False
                _rollback_graph_files(
                    active,
                    previous,
                    previous_backup,
                    had_active=had_active,
                )
                raise
            print(f"Promoted validated graph to {active}")
        except Exception as exc:
            if build_id is not None and not promoted:
                try:
                    metadata.execute(
                        """
                        UPDATE graph_builds
                        SET status='failed', completed_at=CURRENT_TIMESTAMP, error=?
                        WHERE id=?
                        """,
                        (str(exc), build_id),
                    )
                    metadata.commit()
                except sqlite3.Error:
                    pass
            raise
        finally:
            metadata.close()
            candidate.unlink(missing_ok=True)
            snapshot.unlink(missing_ok=True)
            previous_stage.unlink(missing_ok=True)
            previous_backup.unlink(missing_ok=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build and atomically promote the Ladybug graph."
    )
    parser.add_argument("--db", default=SQLITE_DB, help="SQLite catalog path")
    parser.add_argument("--graph", default=GRAPH_DB, help="Active Ladybug graph path")
    parser.add_argument("--mode", choices=("auto", "full", "delta"), default="auto")
    args = parser.parse_args()
    promote_validated_graph(args.db, args.graph, mode=args.mode)


if __name__ == "__main__":
    main()
