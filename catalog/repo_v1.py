"""Foundation and immutable inventory builder for Repo Intelligence V1.

This module deliberately supports one repository (``ia-main``), one full
build mode, and one sequential inventory step.  It is not a compatibility
wrapper around the legacy refresh orchestration.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from tqdm import tqdm

from catalog.refresh_transaction import (
    CatalogPromotionError,
    assert_parent_unchanged,
    parent_descriptor,
    promote_catalog_candidate,
    refresh_lock,
)
from catalog.repo_v1_entities import ENTITY_DIAGNOSTIC_CODES
from catalog.repo_v1_nextgen import (
    extract_snapshot_nextgen,
    validate_nextgen_candidate,
)
from catalog.repo_v1_openapi import (
    OpenAPIValidationError,
    extract_snapshot_openapi,
    validate_openapi_candidate,
)
from catalog.repo_v1_security import (
    extract_snapshot_security,
    validate_security_candidate,
)
from catalog.repo_v1_ui import extract_snapshot_ui, validate_ui_candidate
from catalog.repo_v1_workflows import (
    extract_snapshot_workflows,
    validate_workflow_candidate,
)
from catalog.repositories import RepositoryError, load_workspace_manifest
from catalog.source_snapshot import SourceSnapshotError, materialize_source_snapshot

SCHEMA_PATH = Path(__file__).with_name("repo_v1_schema.sql")
REPO_KEY = "ia-main"
DEFAULT_ACTIVE_DB = Path("catalog/catalog.db")
PHASE6_ADDITIVE_TABLES = frozenset(
    {
        "openapi_documents",
        "openapi_entity_links",
        "rest_endpoints",
        "openapi_diagnostics",
    }
)
PHASE7A_ADDITIVE_TABLES = frozenset(
    {
        "ui_surfaces",
        "ui_artifacts",
        "ui_fields",
        "ui_events",
        "ui_includes",
        "ui_diagnostics",
    }
)
PHASE7B_ADDITIVE_TABLES = frozenset(
    {
        "nextgen_families",
        "nextgen_artifacts",
        "nextgen_diagnostics",
    }
)
PHASE8A_ADDITIVE_TABLES = frozenset({"workflow_facts", "workflow_diagnostics"})
PHASE8B_ADDITIVE_TABLES = frozenset(
    {
        "security_operations",
        "security_operation_allowops",
        "security_policies",
        "security_policy_values",
        "security_policy_eops",
        "security_menus",
        "security_menu_items",
        "security_menu_op_links",
        "security_diagnostics",
    }
)
# Existing Phase 5 -> Phase 6 upgrades remain valid; Phase 7A adds its own
# complete table family on top of that accepted boundary.
_REPO_V1_ADDITIVE_TABLES = (
    PHASE6_ADDITIVE_TABLES
    | PHASE7A_ADDITIVE_TABLES
    | PHASE7B_ADDITIVE_TABLES
    | PHASE8A_ADDITIVE_TABLES
    | PHASE8B_ADDITIVE_TABLES
)


def _load_schema_contract() -> dict[str, frozenset[str]]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        return {
            str(table_row[0]): frozenset(
                str(column_row[1])
                for column_row in conn.execute(f'PRAGMA table_info("{table_row[0]}")')
            )
            for table_row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()


_REPO_V1_SCHEMA_CONTRACT = _load_schema_contract()


class RepoV1Error(RuntimeError):
    """A V1 foundation or inventory build cannot safely proceed."""


@dataclass(frozen=True)
class BuildResult:
    build_token: str
    target_commit_sha: str
    file_count: int
    active_db: Path
    promoted: bool


@dataclass(frozen=True)
class _RepositoryConfig:
    repo_key: str
    name: str | None
    kind: str | None
    language: str | None
    remote_url: str | None
    local_root: Path
    tracked_branch: str
    ignore_paths: tuple[str, ...]
    ignore_filenames: tuple[str, ...]
    ignore_filename_prefixes: tuple[str, ...]
    ignore_suffixes: tuple[str, ...]


def _v1_path_is_in_scope(
    path: str,
    ignore_paths: tuple[str, ...],
    ignore_filenames: tuple[str, ...],
    ignore_filename_prefixes: tuple[str, ...],
    ignore_suffixes: tuple[str, ...],
) -> bool:
    parts = PurePosixPath(path).parts
    if any(part.startswith(".") for part in parts[:-1]):
        return False
    filename = parts[-1]
    if (
        filename in ignore_filenames
        or any(
            filename.lower().startswith(prefix) for prefix in ignore_filename_prefixes
        )
        or PurePosixPath(filename).suffix.lower() in ignore_suffixes
    ):
        return False
    return not any(
        path == ignored or path.startswith(f"{ignored}/") for ignored in ignore_paths
    )


def _v1_detect_language(path: str) -> str:
    # TODO(repo-v1-cleanup): reconcile this V1-local map with the legacy parser
    # map only after V1 language ownership and parser support are separately defined.
    mapping = {
        ".java": "java",
        ".php": "php",
        ".inc": "php",
        ".menu": "php",
        ".pol": "php",
        ".ent": "php",
        ".cls": "php",
        ".phtml": "php",
        ".cqry": "php",
        ".qry": "php",
        ".rpt": "php",
        ".wfl": "php",
        ".map": "php",
        ".shortcuts": "php",
        ".js": "javascript",
        ".ts": "typescript",
        ".sql": "sql",
        ".xml": "xml",
        ".xsd": "xml",
        ".wsdl": "xml",
        ".json": "json",
        ".py": "python",
        ".yaml": "yaml",
        ".html": "html",
        ".xsl": "xslt",
        ".xslt": "xslt",
    }
    return mapping.get(PurePosixPath(path).suffix.lower(), "unknown")


def _repository_config(manifest_path: Path) -> _RepositoryConfig:
    try:
        manifest = load_workspace_manifest(manifest_path)
    except RepositoryError as exc:
        raise RepoV1Error(str(exc)) from exc
    matches = [
        entry for entry in manifest["repositories"] if entry.get("repo_key") == REPO_KEY
    ]
    if len(matches) != 1:
        raise RepoV1Error("workspace manifest must contain exactly one ia-main entry")
    entry = matches[0]
    if entry.get("storage", "central") != "central":
        raise RepoV1Error("ia-main must use central storage for the V1 foundation")
    root = Path(str(entry["local_root"])).expanduser().resolve()
    if not root.is_dir():
        raise RepoV1Error(f"ia-main checkout does not exist: {root}")
    return _RepositoryConfig(
        repo_key=REPO_KEY,
        name=entry.get("name"),
        kind=entry.get("kind"),
        language=entry.get("language"),
        remote_url=entry.get("remote_url"),
        local_root=root,
        tracked_branch=str(entry["tracked_branch"]),
        ignore_paths=tuple(entry.get("ignore_paths", [])),
        ignore_filenames=tuple(entry.get("ignore_filenames", [])),
        ignore_filename_prefixes=tuple(entry.get("ignore_filename_prefixes", [])),
        ignore_suffixes=tuple(entry.get("ignore_suffixes", [])),
    )


def _new_candidate(active_db: Path) -> Path:
    active_db.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{active_db.name}.candidate.", suffix=".db", dir=active_db.parent
    )
    os.close(descriptor)
    return Path(raw_path)


def _connect_candidate(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _insert_build(
    conn: sqlite3.Connection,
    *,
    candidate: Path,
    build_token: str,
    target_commit_sha: str,
) -> int:
    cursor = conn.execute(
        """INSERT INTO catalog_builds(
               build_token,catalog_path,status,source_revisions_json
           ) VALUES (?,?,?,?)""",
        (
            build_token,
            str(candidate),
            "building",
            json.dumps(
                {REPO_KEY: target_commit_sha}, sort_keys=True, separators=(",", ":")
            ),
        ),
    )
    return int(cursor.lastrowid)


def _build_inventory(
    *,
    candidate: Path,
    config: _RepositoryConfig,
    target_commit_sha: str,
    build_token: str,
    show_progress: bool,
) -> int:
    conn = _connect_candidate(candidate)
    try:
        build_id = _insert_build(
            conn,
            candidate=candidate,
            build_token=build_token,
            target_commit_sha=target_commit_sha,
        )
        repo_id = int(
            conn.execute(
                """INSERT INTO repos(
                       repo_key,name,kind,language,remote_url,local_root,
                       tracked_branch,target_commit_sha,build_id
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    config.repo_key,
                    config.name,
                    config.kind,
                    config.language,
                    config.remote_url,
                    str(config.local_root),
                    config.tracked_branch,
                    target_commit_sha,
                    build_id,
                ),
            ).lastrowid
        )
        with materialize_source_snapshot(
            config.repo_key,
            config.local_root,
            target_commit_sha,
            candidate.parent,
            include_path=lambda path: _v1_path_is_in_scope(
                path,
                config.ignore_paths,
                config.ignore_filenames,
                config.ignore_filename_prefixes,
                config.ignore_suffixes,
            ),
            show_progress=show_progress,
        ) as snapshot:
            entries = tqdm(
                snapshot.entries,
                desc="Writing V1 inventory",
                unit="file",
                disable=not show_progress,
            )
            for entry in entries:
                conn.execute(
                    """INSERT INTO files(
                           repo_id,path,blob_object_id,file_mode,size_bytes,
                           language,source_commit_sha
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        repo_id,
                        entry.path,
                        entry.object_id,
                        entry.mode,
                        entry.size,
                        _v1_detect_language(entry.path),
                        snapshot.target_sha,
                    ),
                )
            from catalog.repo_v1_symbols import extract_snapshot_symbols

            extract_snapshot_symbols(
                conn,
                repo_id=repo_id,
                snapshot=snapshot,
                show_progress=show_progress,
            )
            from catalog.repo_v1_relationships import extract_snapshot_relationships

            extract_snapshot_relationships(
                conn,
                repo_id=repo_id,
                snapshot=snapshot,
                show_progress=show_progress,
            )
            from catalog.repo_v1_entities import extract_snapshot_entity_occurrences

            entity_stats = extract_snapshot_entity_occurrences(
                conn,
                repo_id=repo_id,
                snapshot=snapshot,
                show_progress=show_progress,
            )
            openapi_stats = extract_snapshot_openapi(
                conn,
                repo_id=repo_id,
                snapshot=snapshot,
                show_progress=show_progress,
            )
            ui_stats = extract_snapshot_ui(
                conn,
                repo_id=repo_id,
                snapshot=snapshot,
                show_progress=show_progress,
            )
            nextgen_stats = extract_snapshot_nextgen(
                conn,
                repo_id=repo_id,
                snapshot=snapshot,
                show_progress=show_progress,
            )
            workflow_stats = extract_snapshot_workflows(
                conn, repo_id=repo_id, snapshot=snapshot, show_progress=show_progress
            )
            security_stats = extract_snapshot_security(
                conn, repo_id=repo_id, snapshot=snapshot, show_progress=show_progress
            )
        file_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE repo_id=?", (repo_id,)
            ).fetchone()[0]
        )
        conn.execute(
            "UPDATE catalog_builds SET status='validated',completed_at=?,validation_summary=? WHERE id=?",
            (
                datetime.now(UTC).isoformat(),
                json.dumps(
                    {
                        "repo_key": REPO_KEY,
                        "file_count": file_count,
                        "relationship_count": int(
                            conn.execute(
                                "SELECT COUNT(*) FROM relationships WHERE repo_id=?",
                                (repo_id,),
                            ).fetchone()[0]
                        ),
                        "entity_node_count": entity_stats.node_count,
                        "entity_occurrence_count": entity_stats.occurrence_count,
                        "entity_diagnostic_count": entity_stats.diagnostic_count,
                        "openapi_document_count": openapi_stats.document_count,
                        "openapi_entity_link_count": openapi_stats.link_count,
                        "rest_endpoint_count": openapi_stats.endpoint_count,
                        "openapi_diagnostic_count": openapi_stats.diagnostic_count,
                        "ui_surface_count": ui_stats.surface_count,
                        "ui_artifact_count": ui_stats.artifact_count,
                        "ui_field_count": ui_stats.field_count,
                        "ui_event_count": ui_stats.event_count,
                        "ui_include_count": ui_stats.include_count,
                        "ui_diagnostic_count": ui_stats.diagnostic_count,
                        "nextgen_family_count": nextgen_stats.family_count,
                        "nextgen_artifact_count": nextgen_stats.artifact_count,
                        "nextgen_diagnostic_count": nextgen_stats.diagnostic_count,
                        "workflow_fact_count": workflow_stats.fact_count,
                        "workflow_diagnostic_count": workflow_stats.diagnostic_count,
                        "workflow_resolved_entity_link_count": workflow_stats.resolved_entity_link_count,
                        "workflow_unresolved_entity_link_count": workflow_stats.unresolved_entity_link_count,
                        "workflow_ambiguous_entity_link_count": workflow_stats.ambiguous_entity_link_count,
                        "security_operation_count": security_stats.operation_count,
                        "security_allowop_count": security_stats.allowop_count,
                        "security_policy_count": security_stats.policy_count,
                        "security_policy_value_count": security_stats.policy_value_count,
                        "security_policy_eop_count": security_stats.policy_eop_count,
                        "security_menu_count": security_stats.menu_count,
                        "security_menu_item_count": security_stats.menu_item_count,
                        "security_menu_op_link_count": security_stats.menu_op_link_count,
                        "security_diagnostic_count": security_stats.diagnostic_count,
                        "security_unresolved_link_count": security_stats.unresolved_link_count,
                        "security_conflict_count": security_stats.conflict_count,
                    },
                    sort_keys=True,
                ),
                build_id,
            ),
        )
        conn.commit()
        return file_count
    except (OSError, SourceSnapshotError, sqlite3.Error, RuntimeError) as exc:
        conn.rollback()
        raise RepoV1Error(str(exc)) from exc
    finally:
        conn.close()


def _validate_candidate(
    candidate: Path, *, target_commit_sha: str, build_token: str
) -> None:
    conn = sqlite3.connect(candidate)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RepoV1Error(f"candidate integrity check failed: {integrity}")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RepoV1Error(f"candidate foreign-key check failed: {violations[:3]}")
        build = conn.execute(
            "SELECT id,status,source_revisions_json FROM catalog_builds WHERE build_token=?",
            (build_token,),
        ).fetchone()
        if build is None or build["status"] != "validated":
            raise RepoV1Error("candidate build is not validated")
        if json.loads(str(build["source_revisions_json"])) != {
            REPO_KEY: target_commit_sha
        }:
            raise RepoV1Error("candidate source revision does not match target commit")
        repo = conn.execute(
            "SELECT id,repo_key,target_commit_sha FROM repos WHERE build_id=?",
            (build["id"],),
        ).fetchone()
        if repo is None or (repo["repo_key"], repo["target_commit_sha"]) != (
            REPO_KEY,
            target_commit_sha,
        ):
            raise RepoV1Error("candidate repository provenance is invalid")
        if conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0] != 1:
            raise RepoV1Error("V1 candidate must contain exactly one repository")
        file_count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE repo_id=?", (repo["id"],)
        ).fetchone()[0]
        distinct_path_count = conn.execute(
            "SELECT COUNT(DISTINCT path) FROM files WHERE repo_id=?", (repo["id"],)
        ).fetchone()[0]
        if file_count != distinct_path_count:
            raise RepoV1Error("candidate inventory contains duplicate paths")
        invalid_provenance = conn.execute(
            """SELECT COUNT(*) FROM files
               WHERE (repo_id=? AND source_commit_sha<>?)
                  OR (repo_id=? AND (blob_object_id='' OR path=''))""",
            (repo["id"], target_commit_sha, repo["id"]),
        ).fetchone()[0]
        if invalid_provenance:
            raise RepoV1Error("candidate file provenance is invalid")
        invalid_symbol_ownership = conn.execute(
            """SELECT COUNT(*)
               FROM symbols s
               LEFT JOIN files f ON f.id=s.file_id
               WHERE f.id IS NULL OR s.repo_id<>f.repo_id OR s.repo_id<>?""",
            (repo["id"],),
        ).fetchone()[0]
        invalid_diagnostic_ownership = conn.execute(
            """SELECT COUNT(*)
               FROM symbol_diagnostics d
               LEFT JOIN files f ON f.id=d.file_id
               WHERE f.id IS NULL OR d.repo_id<>f.repo_id OR d.repo_id<>?""",
            (repo["id"],),
        ).fetchone()[0]
        invalid_diagnostic_provenance = conn.execute(
            """SELECT COUNT(*)
               FROM symbol_diagnostics d
               JOIN files f ON f.id=d.file_id
               WHERE d.source_commit_sha<>f.source_commit_sha"""
        ).fetchone()[0]
        invalid_symbol_facts = conn.execute(
            """SELECT COUNT(*)
               FROM symbols
               WHERE name='' OR kind='' OR language=''
                  OR start_line IS NULL OR end_line IS NULL
                  OR start_line < 1 OR end_line < start_line"""
        ).fetchone()[0]
        failed_with_symbols = conn.execute(
            """SELECT COUNT(*)
               FROM symbol_diagnostics d
               JOIN symbols s ON s.file_id=d.file_id
               WHERE d.repo_id=? AND d.severity='error'""",
            (repo["id"],),
        ).fetchone()[0]
        if invalid_symbol_ownership or invalid_diagnostic_ownership:
            raise RepoV1Error("candidate symbol ownership is invalid")
        if invalid_diagnostic_provenance:
            raise RepoV1Error("candidate symbol diagnostic provenance is invalid")
        if invalid_symbol_facts:
            raise RepoV1Error("candidate symbol facts are invalid")
        if failed_with_symbols:
            raise RepoV1Error("parser-failed file contains symbols")
        invalid_relationship_ownership = conn.execute(
            """SELECT COUNT(*)
               FROM relationships r
               LEFT JOIN repos rr ON rr.id=r.repo_id
               LEFT JOIN files f ON f.id=r.file_id
               LEFT JOIN symbols ss ON ss.id=r.source_symbol_id
               LEFT JOIN symbols ts ON ts.id=r.target_symbol_id
               WHERE rr.id IS NULL OR f.id IS NULL
                  OR f.repo_id<>r.repo_id
                  OR r.file_path<>f.path
                  OR r.language<>f.language
                  OR f.source_commit_sha<>rr.target_commit_sha
                  OR (r.source_symbol_id IS NOT NULL AND
                      (ss.id IS NULL OR ss.repo_id<>r.repo_id OR ss.file_id<>r.file_id))
                  OR (r.target_symbol_id IS NOT NULL AND
                      (ts.id IS NULL OR ts.repo_id<>r.repo_id))"""
        ).fetchone()[0]
        invalid_relationship_facts = conn.execute(
            """SELECT COUNT(*)
               FROM relationships r
               LEFT JOIN symbols ss ON ss.id=r.source_symbol_id
               LEFT JOIN symbols ts ON ts.id=r.target_symbol_id
               WHERE r.target_name=''
                  OR r.relationship_type=''
                  OR r.file_path=''
                  OR r.language=''
                  OR r.evidence=''
                  OR r.resolution_class=''
                  OR r.resolution_reason=''
                  OR r.extractor<>'phase2_regex_mvp'
                  OR r.confidence IS NULL
                  OR r.confidence<0 OR r.confidence>1
                  OR (r.source_symbol_id IS NOT NULL AND
                      (ss.name<>r.source_name OR r.source_name IS NULL))
                  OR (r.target_symbol_id IS NOT NULL AND
                      (ts.name<>r.target_name OR r.resolution_class<>'project_resolved'))
                  OR (r.target_symbol_id IS NULL AND r.resolution_class='project_resolved')"""
        ).fetchone()[0]
        if invalid_relationship_ownership:
            raise RepoV1Error("candidate relationship ownership is invalid")
        if invalid_relationship_facts:
            raise RepoV1Error("candidate relationship facts are invalid")
        invalid_entity_ownership = conn.execute(
            """SELECT COUNT(*)
               FROM entity_occurrences eo
               LEFT JOIN repos r ON r.id=eo.repo_id
               LEFT JOIN files f ON f.id=eo.source_file_id
               LEFT JOIN entity_nodes en ON en.id=eo.entity_id
               WHERE r.id IS NULL OR f.id IS NULL OR en.id IS NULL
                  OR eo.repo_id<>? OR f.repo_id<>eo.repo_id
                  OR f.path NOT LIKE '%.ent'
                  OR eo.source_commit_sha<>f.source_commit_sha
                  OR eo.source_commit_sha<>r.target_commit_sha
                  OR eo.source_key='' OR en.name<>eo.source_key""",
            (repo["id"],),
        ).fetchone()[0]
        invalid_entity_uniqueness = conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT repo_id,source_file_id,source_key,COUNT(*) AS count
                   FROM entity_occurrences
                   GROUP BY repo_id,source_file_id,source_key
                   HAVING count<>1
               )"""
        ).fetchone()[0]
        invalid_entity_facts = conn.execute(
            """SELECT COUNT(*) FROM entity_occurrences
               WHERE source_key='' OR source_commit_sha='' OR evidence=''
                  OR extractor<>'repo_v1_entities_v1'
                  OR dummy IS NOT NULL AND dummy NOT IN (0,1)"""
        ).fetchone()[0]
        invalid_entity_diagnostic_ownership = conn.execute(
            """SELECT COUNT(*)
               FROM entity_diagnostics d
               LEFT JOIN repos r ON r.id=d.repo_id
               LEFT JOIN files f ON f.id=d.file_id
               LEFT JOIN entity_occurrences eo ON eo.id=d.occurrence_id
               WHERE r.id IS NULL OR f.id IS NULL OR d.repo_id<>?
                  OR f.repo_id<>d.repo_id
                  OR f.path NOT LIKE '%.ent'
                  OR (eo.id IS NOT NULL AND eo.repo_id<>d.repo_id)
                  OR (eo.id IS NOT NULL AND (d.source_key IS NULL OR eo.source_key<>d.source_key))
                  OR d.code NOT IN ({codes})
                  OR d.severity<>'error' OR d.evidence='' OR d.extractor<>'repo_v1_entities_v1'
                  OR d.source_commit_sha<>f.source_commit_sha""".format(
                codes=",".join("?" for _ in ENTITY_DIAGNOSTIC_CODES)
            ),
            (repo["id"], *sorted(ENTITY_DIAGNOSTIC_CODES)),
        ).fetchone()[0]
        if (
            invalid_entity_ownership
            or invalid_entity_uniqueness
            or invalid_entity_facts
            or invalid_entity_diagnostic_ownership
        ):
            raise RepoV1Error(
                "candidate entity ownership, provenance, or diagnostic validation failed"
            )
        try:
            validate_openapi_candidate(
                conn,
                repo_id=int(repo["id"]),
                repo_key=REPO_KEY,
                target_commit_sha=target_commit_sha,
            )
        except OpenAPIValidationError as exc:
            raise RepoV1Error(str(exc)) from exc
        try:
            validate_ui_candidate(
                conn,
                repo_id=int(repo["id"]),
                target_commit_sha=target_commit_sha,
            )
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RepoV1Error(str(exc)) from exc
        try:
            validate_nextgen_candidate(
                conn,
                repo_id=int(repo["id"]),
                target_commit_sha=target_commit_sha,
            )
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RepoV1Error(str(exc)) from exc
        try:
            validate_workflow_candidate(
                conn, repo_id=int(repo["id"]), target_commit_sha=target_commit_sha
            )
            validate_security_candidate(
                conn, repo_id=int(repo["id"]), target_commit_sha=target_commit_sha
            )
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RepoV1Error(str(exc)) from exc
    finally:
        conn.close()


def _mark_candidate_active(candidate: Path, active_db: Path, build_token: str) -> None:
    conn = sqlite3.connect(candidate)
    try:
        conn.execute(
            "UPDATE catalog_builds SET status='active',catalog_path=? WHERE build_token=? AND status='validated'",
            (str(active_db), build_token),
        )
        if conn.total_changes != 1:
            raise RepoV1Error("candidate build could not enter active promotion state")
        conn.commit()
    finally:
        conn.close()


def build_ia_main(
    *,
    manifest_path: str | Path = "config/workspace_repos.yaml",
    active_db: str | Path = DEFAULT_ACTIVE_DB,
    target_sha: str | None = None,
    promote: bool = True,
    show_progress: bool = False,
) -> BuildResult:
    """Build and optionally promote a full immutable ``ia-main`` inventory."""

    manifest = _repository_config(Path(manifest_path))
    from catalog.source_snapshot import resolve_commit_sha

    try:
        resolved_sha = resolve_commit_sha(
            manifest.local_root, target_sha or manifest.tracked_branch
        )
    except SourceSnapshotError as exc:
        raise RepoV1Error(str(exc)) from exc

    active = Path(active_db).expanduser().resolve()
    token = uuid.uuid4().hex
    with refresh_lock(active):
        expected_parent = parent_descriptor(
            active,
            expected_schema=_REPO_V1_SCHEMA_CONTRACT,
            allowed_missing_tables=_REPO_V1_ADDITIVE_TABLES,
        )
        _assert_phase_parent_boundary(active)
        candidate = _new_candidate(active)
        try:
            file_count = _build_inventory(
                candidate=candidate,
                config=manifest,
                target_commit_sha=resolved_sha,
                build_token=token,
                show_progress=show_progress,
            )
            _validate_candidate(
                candidate, target_commit_sha=resolved_sha, build_token=token
            )
            if not promote:
                return BuildResult(token, resolved_sha, file_count, active, False)
            _mark_candidate_active(candidate, active, token)
            assert_parent_unchanged(
                active,
                expected_parent,
                expected_schema=_REPO_V1_SCHEMA_CONTRACT,
                allowed_missing_tables=_REPO_V1_ADDITIVE_TABLES,
            )
            previous = active.with_name(active.name + ".previous")
            promote_catalog_candidate(active, candidate, previous, token)
            return BuildResult(token, resolved_sha, file_count, active, True)
        finally:
            candidate.unlink(missing_ok=True)


def _assert_phase_parent_boundary(active: Path) -> None:
    """Allow only the complete ordered Phase 6 through Phase 8 families."""

    if not active.exists():
        return
    try:
        conn = sqlite3.connect(f"file:{active}?mode=ro", uri=True)
        try:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        # parent_descriptor owns the precise malformed-database error.
        return
    families = (
        PHASE6_ADDITIVE_TABLES,
        PHASE7A_ADDITIVE_TABLES,
        PHASE7B_ADDITIVE_TABLES,
        PHASE8A_ADDITIVE_TABLES,
        PHASE8B_ADDITIVE_TABLES,
    )
    states = []
    invalid = False
    for family in families:
        present = family & tables
        complete = present == family
        if present and not complete:
            invalid = True
        states.append(complete)
    if any(states[index] and not all(states[:index]) for index in range(len(states))):
        invalid = True
    if invalid:
        if (
            PHASE7B_ADDITIVE_TABLES <= tables
            and not (PHASE7A_ADDITIVE_TABLES <= tables)
        ) or (
            PHASE7A_ADDITIVE_TABLES & tables and not (PHASE7A_ADDITIVE_TABLES <= tables)
        ):
            message = "partial UI table set"
        elif PHASE7B_ADDITIVE_TABLES & tables and not (
            PHASE7B_ADDITIVE_TABLES <= tables
        ):
            message = "partial NextGen table set"
        elif PHASE6_ADDITIVE_TABLES & tables and not (PHASE6_ADDITIVE_TABLES <= tables):
            message = "partial OpenAPI table set"
        else:
            message = "active catalog schema has an incomplete or out-of-order Phase 6-8 family"
        raise CatalogPromotionError(message)


_assert_phase7b_parent_boundary = _assert_phase_parent_boundary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path("config/workspace_repos.yaml")
    )
    parser.add_argument("--active-db", type=Path, default=DEFAULT_ACTIVE_DB)
    parser.add_argument(
        "--target-sha", help="Git revision; defaults to ia-main tracked_branch"
    )
    parser.add_argument("--no-promote", action="store_true")
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the V1 inventory progress indicator",
    )
    args = parser.parse_args()
    result = build_ia_main(
        manifest_path=args.manifest,
        active_db=args.active_db,
        target_sha=args.target_sha,
        promote=not args.no_promote,
        show_progress=not args.no_progress,
    )
    print(json.dumps(result.__dict__, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
