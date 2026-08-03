"""Source-less, atomic archival of one repository's catalog evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable
import uuid

from catalog.archive_ownership import (
    ARCHIVE_OWNED_REPO_TABLES,
    ArchiveOwnershipError,
    assert_target_evidence_absent,
    purge_target_owned_evidence,
    target_entity_ids,
    validate_archive_ownership_schema,
)
from catalog.content_fingerprint import logical_content_fingerprint
from catalog.delta import DELTA_CONTRACT_VERSION
from catalog.migrations import apply_delta_refresh_migration
from catalog.refresh_transaction import (
    CatalogPromotionError,
    assert_parent_unchanged,
    backup_database,
    parent_descriptor,
    promote_catalog_candidate,
    refresh_lock,
)
from catalog.source_revisions import active_source_revisions
from validation.validate_catalog_integrity import validate_catalog_connection


class ArchiveRepositoryError(RuntimeError):
    """The requested archive operation cannot be proven safe."""


@dataclass(frozen=True)
class ArchiveResult:
    repo_key: str
    source: str
    build_token: str | None
    promoted: bool
    idempotent: bool
    purged_counts: dict[str, int]
    content_fingerprint: str | None
    graph_rebuild_required: bool = True


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def repository_evidence_fingerprint(conn: sqlite3.Connection, repo_id: int) -> str:
    """Fingerprint direct repository evidence for preservation assertions."""

    validate_archive_ownership_schema(conn)
    digest = hashlib.sha256()
    for table in ARCHIVE_OWNED_REPO_TABLES:
        columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
        quoted = ",".join(f'"{column}"' for column in columns)
        digest.update(f"{table}:{','.join(columns)}\n".encode())
        rows = [
            _stable_json(list(row))
            for row in conn.execute(
                f"SELECT {quoted} FROM {table} WHERE repo_id=?", (repo_id,)
            )
        ]
        for row in sorted(rows):
            digest.update(row.encode())
            digest.update(b"\n")
    return digest.hexdigest()


def _target_ids(conn: sqlite3.Connection, repo_id: int) -> dict[str, set[int]]:
    """IDs whose removal must not turn retained evidence into dangling links."""

    ids: dict[str, set[int]] = {}
    for table in (*ARCHIVE_OWNED_REPO_TABLES, "symbols"):
        if table == "symbols":
            rows = conn.execute(
                "SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.repo_id=?",
                (repo_id,),
            )
        else:
            rows = conn.execute(f"SELECT id FROM {table} WHERE repo_id=?", (repo_id,))
        ids[table] = {int(row[0]) for row in rows}
    return ids


def _ids_clause(ids: set[int]) -> tuple[str, tuple[int, ...]]:
    if not ids:
        return "(SELECT NULL WHERE 0)", ()
    return "(" + ",".join("?" for _ in ids) + ")", tuple(sorted(ids))


def _assert_no_active_inbound_references(
    conn: sqlite3.Connection,
    repo_id: int,
    ids: dict[str, set[int]],
    entity_ids: set[int],
    workflow_ids: set[int],
) -> None:
    """Block evidence retained by another repo before FK SET NULL can hide it."""

    failures: list[str] = []
    # Directly owned rows may only refer to target IDs from the target repo.
    # Canonical entity nodes intentionally remain shared and are checked only
    # during optional orphan cleanup below.
    for child in ARCHIVE_OWNED_REPO_TABLES:
        for fk in conn.execute(f"PRAGMA foreign_key_list({child})"):
            parent, from_column, to_column = str(fk[2]), str(fk[3]), str(fk[4])
            if to_column != "id" or parent in {"repos", "entity_nodes"}:
                continue
            parent_ids = ids.get(parent)
            if not parent_ids:
                continue
            clause, params = _ids_clause(parent_ids)
            count = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {child} WHERE repo_id<>? "
                    f"AND {from_column} IN {clause}",
                    (repo_id, *params),
                ).fetchone()[0]
            )
            if count:
                failures.append(f"{child}.{from_column}->{parent}={count}")

    # These children have ownership through another root rather than repo_id.
    special = (
        ("security_operation_allowops", "allowed_operation_id", "security_operations", "JOIN security_operations owner ON owner.id=c.operation_id"),
        ("security_operation_allowops", "file_id", "files", "JOIN security_operations owner ON owner.id=c.operation_id"),
        ("security_menu_op_links", "operation_id", "security_operations", "JOIN security_menu_items item ON item.id=c.menu_item_id JOIN security_menus owner ON owner.id=item.menu_id"),
        ("test_endpoint_links", "compatibility_id", "api_version_compatibility", "JOIN test_requests request ON request.id=c.test_request_id JOIN test_cases owner ON owner.id=request.test_case_id"),
        ("test_endpoint_links", "rest_endpoint_id", "rest_endpoints", "JOIN test_requests request ON request.id=c.test_request_id JOIN test_cases owner ON owner.id=request.test_case_id"),
        ("test_entity_links", "rest_endpoint_id", "rest_endpoints", "JOIN test_requests request ON request.id=c.test_request_id JOIN test_cases owner ON owner.id=request.test_case_id"),
        ("test_case_versions", "source_file_id", "files", "JOIN test_cases owner ON owner.id=c.test_case_id"),
        ("workflow_nodes", "file_id", "files", "JOIN workflows owner ON owner.id=c.workflow_id"),
        ("workflow_nodes", "symbol_id", "symbols", "JOIN workflows owner ON owner.id=c.workflow_id"),
        ("workflow_edges", "file_id", "files", "JOIN workflows owner ON owner.id=c.workflow_id"),
        ("workflow_edges", "symbol_id", "symbols", "JOIN workflows owner ON owner.id=c.workflow_id"),
    )
    for child, foreign_column, target_table, owner_join in special:
        target_ids = ids.get(target_table, set())
        if not target_ids:
            continue
        clause, params = _ids_clause(target_ids)
        count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {child} c {owner_join} WHERE owner.repo_id<>? "
                f"AND c.{foreign_column} IN {clause}",
                (repo_id, *params),
            ).fetchone()[0]
        )
        if count:
            failures.append(f"{child}.{foreign_column}->{target_table}={count}")

    integration = int(
        conn.execute(
            "SELECT COUNT(*) FROM integration_links WHERE source_repo_id=? OR target_repo_id=?",
            (repo_id, repo_id),
        ).fetchone()[0]
    )
    if integration:
        failures.append(f"integration_links={integration}")
    if entity_ids:
        clause, params = _ids_clause(entity_ids)
        count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM knowledge_items WHERE entity_id IN {clause}", params
            ).fetchone()[0]
        )
        if count:
            failures.append(f"knowledge_items.entity_id={count}")
    if workflow_ids:
        clause, params = _ids_clause(workflow_ids)
        count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM knowledge_items WHERE workflow_id IN {clause}", params
            ).fetchone()[0]
        )
        if count:
            failures.append(f"knowledge_items.workflow_id={count}")
    if failures:
        raise ArchiveRepositoryError(
            "active or user-owned evidence refers to archive target: " + "; ".join(failures)
        )


def _cleanup_orphan_entity_nodes(conn: sqlite3.Connection, candidate_entity_ids: set[int]) -> int:
    """Delete only entity identity with no occurrence and no retained reference."""

    removed = 0
    foreign_refs = []
    for (table,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        table = str(table)
        if table == "entity_nodes":
            continue
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):
            if str(fk[2]) == "entity_nodes" and str(fk[4]) == "id":
                foreign_refs.append((table, str(fk[3])))
    for entity_id in sorted(candidate_entity_ids):
        if conn.execute(
            "SELECT 1 FROM entity_occurrences WHERE entity_id=? LIMIT 1", (entity_id,)
        ).fetchone():
            continue
        if conn.execute(
            "SELECT 1 FROM knowledge_items WHERE entity_id=? LIMIT 1", (entity_id,)
        ).fetchone():
            continue
        if any(
            conn.execute(
                f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (entity_id,)
            ).fetchone()
            for table, column in foreign_refs
        ):
            continue
        cursor = conn.execute("DELETE FROM entity_nodes WHERE id=?", (entity_id,))
        removed += cursor.rowcount
    return removed


def _archive_build(
    conn: sqlite3.Connection,
    *,
    active_path: Path,
    parent_id: int,
    token: str,
) -> int:
    cursor = conn.execute(
        """INSERT INTO catalog_builds(
               build_token,parent_catalog_build_id,catalog_path,requested_mode,effective_mode,
               status,source_revisions_json,delta_contract_version,validation_summary
           ) VALUES (?,?,?,'archive','archive','building',?,?,?)""",
        (
            token,
            parent_id if parent_id else None,
            str(active_path),
            "{}",
            DELTA_CONTRACT_VERSION,
            _stable_json({"operation": "repository_archive", "graph_rebuild_required": True}),
        ),
    )
    return int(cursor.lastrowid)


def archive_repository(
    active_db: str | Path,
    repo_key: str,
    *,
    source: str,
    reason: str,
    github_archive_verifier: Callable[[sqlite3.Row], bool] | None = None,
    validator: Callable[..., dict] = validate_catalog_connection,
    before_promote: Callable[[], None] | None = None,
) -> ArchiveResult:
    """Archive exactly one repository through a candidate-only mutation.

    ``source='manual'`` requires no checkout or provider.  For ``github`` the
    caller must provide a verifier that returns literal ``True`` only after the
    configured provider has confirmed archival.
    """

    active = Path(active_db).resolve()
    if source not in {"manual", "github"}:
        raise ArchiveRepositoryError("archive source must be 'manual' or 'github'")
    if not reason.strip():
        raise ArchiveRepositoryError("archive reason is required")
    if not active.is_file():
        raise ArchiveRepositoryError(f"catalog database does not exist: {active}")
    token = str(uuid.uuid4())
    candidate = active.with_name(f"{active.name}.archive-candidate.{token}")
    previous = active.with_name(f"{active.name}.previous")
    with refresh_lock(active):
        parent = parent_descriptor(active)
        backup_database(active, candidate)
        try:
            conn = sqlite3.connect(candidate)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                apply_delta_refresh_migration(conn)
                validate_archive_ownership_schema(conn)
                target = conn.execute("SELECT * FROM repos WHERE repo_key=?", (repo_key,)).fetchone()
                if target is None:
                    raise ArchiveRepositoryError(f"unknown repository: {repo_key}")
                repo_id = int(target["id"])
                existing = {
                    table: int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE repo_id=?", (repo_id,)).fetchone()[0])
                    for table in ARCHIVE_OWNED_REPO_TABLES
                }
                if target["lifecycle_state"] == "archived" and not any(existing.values()):
                    # A source-less idempotent request has no candidate to
                    # validate or promote; discard the private backup first.
                    conn.close()
                    candidate.unlink(missing_ok=True)
                    return ArchiveResult(repo_key, source, None, False, True, {}, None)
                if source == "github":
                    if github_archive_verifier is None or github_archive_verifier(target) is not True:
                        raise ArchiveRepositoryError("GitHub archival confirmation is required")
                active_fingerprints = {
                    int(row[0]): repository_evidence_fingerprint(conn, int(row[0]))
                    for row in conn.execute(
                        "SELECT id FROM repos WHERE id<>? AND lifecycle_state='active'", (repo_id,)
                    )
                }
                entities = target_entity_ids(conn, repo_id)
                ids = _target_ids(conn, repo_id)
                workflow_ids = ids.get("workflows", set())
                conn.execute("BEGIN IMMEDIATE")
                _assert_no_active_inbound_references(conn, repo_id, ids, entities, workflow_ids)
                build_id = _archive_build(conn, active_path=active, parent_id=parent.catalog_build_id, token=token)
                purged = purge_target_owned_evidence(conn, repo_id)
                _cleanup_orphan_entity_nodes(conn, entities)
                conn.execute(
                    """UPDATE repos SET lifecycle_state='archived',archive_source=?,
                       archive_reason=?,archived_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (source, reason.strip(), repo_id),
                )
                assert_target_evidence_absent(conn, repo_id)
                for active_repo_id, before in active_fingerprints.items():
                    after = repository_evidence_fingerprint(conn, active_repo_id)
                    if after != before:
                        raise ArchiveRepositoryError(
                            f"active repository evidence changed during archive: {active_repo_id}"
                        )
                revisions = active_source_revisions(conn)
                conn.execute(
                    "UPDATE catalog_builds SET source_revisions_json=? WHERE id=?",
                    (_stable_json(revisions), build_id),
                )
                conn.execute("UPDATE catalog_builds SET status='previous' WHERE status='active'")
                fingerprint = logical_content_fingerprint(conn)
                conn.execute(
                    """UPDATE catalog_builds SET status='active',content_fingerprint=?,
                       completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (fingerprint, build_id),
                )
                summary = validator(conn, expected_catalog_build_id=build_id)
                summary["archive"] = {
                    "repo_key": repo_key,
                    "repo_id": repo_id,
                    "source": source,
                    "purged_counts": purged,
                    "graph_rebuild_required": True,
                }
                conn.execute(
                    "UPDATE catalog_builds SET validation_summary=? WHERE id=?",
                    (_stable_json(summary), build_id),
                )
                conn.commit()
            finally:
                conn.close()
            assert_parent_unchanged(active, parent)
            if before_promote is not None:
                before_promote()
            assert_parent_unchanged(active, parent)
            promote_catalog_candidate(active, candidate, previous, token)
            return ArchiveResult(repo_key, source, token, True, False, purged, fingerprint)
        except Exception:
            candidate.unlink(missing_ok=True)
            raise


__all__ = [
    "ArchiveRepositoryError",
    "ArchiveResult",
    "ArchiveOwnershipError",
    "CatalogPromotionError",
    "archive_repository",
    "repository_evidence_fingerprint",
]
