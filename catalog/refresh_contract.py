"""Deterministic fingerprint of evidence-affecting refresh runtime inputs."""

from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from catalog.content_fingerprint import CATALOG_CONTENT_VERSION
from catalog.delta import DELTA_CONTRACT_VERSION
from catalog.migrations import (
    REFRESH_ATTEMPTS_AND_READINESS_MIGRATION,
    REFRESH_RELIABILITY_GATES_MIGRATION,
)
from catalog.refresh_quality import (
    RefreshQualityError,
    resolve_reference_quality_run,
    validate_quality_run,
)
from catalog.source_revisions import active_source_revisions

RUNTIME_CONTRACT_VERSION = 1
EVIDENCE_COMPARISON_VERSION = 1
READY = "ready"
FULL_RECOVERY_REQUIRED = "full_recovery_required"


@dataclass(frozen=True)
class ActiveReadiness:
    status: str
    reasons: tuple[str, ...]
    catalog_build_id: int | None

    @property
    def ready(self) -> bool:
        return self.status == READY

_EXACT_INPUTS = (
    "config.py",
    "pyproject.toml",
    "uv.lock",
    "catalog/schema.sql",
    "scripts/builder_registry.py",
    "scripts/builder_outcome.py",
    "scripts/refresh_workspace.py",
    "validation/validate_catalog_integrity.py",
)
_GLOB_INPUTS = (
    "catalog/**/*.py",
    "parser/**/*.py",
    "migrations/*.sql",
    "scripts/build_*.py",
    "scripts/scan_*.py",
    "scripts/link_*.py",
)


def runtime_input_paths(root: Path | None = None) -> tuple[Path, ...]:
    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    relative_paths: set[Path] = {Path(value) for value in _EXACT_INPUTS}
    for pattern in _GLOB_INPUTS:
        relative_paths.update(
            path.relative_to(project_root)
            for path in project_root.glob(pattern)
            if path.is_file()
        )
    return tuple(
        project_root / path
        for path in sorted(relative_paths, key=lambda p: p.as_posix())
    )


def runtime_fingerprint(root: Path | None = None) -> str:
    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    digest = hashlib.sha256()
    runtime = {
        "implementation": sys.implementation.name,
        "implementation_version": platform.python_version(),
        "version": sys.version,
    }
    digest.update(
        json.dumps(
            runtime, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    )
    digest.update(b"\0")
    for path in runtime_input_paths(project_root):
        relative = path.relative_to(project_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            data = path.read_bytes()
            digest.update(str(len(data)).encode("ascii"))
            digest.update(b"\0")
            digest.update(data)
        else:
            digest.update(b"missing")
        digest.update(b"\0")
    return digest.hexdigest()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def evaluate_active_readiness(conn: sqlite3.Connection) -> ActiveReadiness:
    """Return whether the active generation is admissible as a delta parent.

    Readiness is intentionally stricter than structural integrity.  In
    particular, a migrated historical build stays recovery-required until a
    new full candidate records all current contracts and quality evidence.
    """

    # The coordinator and validators both use this helper; normalize row access
    # here so callers do not need to know its SQLite-row requirement.
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row

    def result(status: str, reasons: tuple[str, ...], build_id: int | None) -> ActiveReadiness:
        conn.row_factory = old_factory
        return ActiveReadiness(status, reasons, build_id)

    try:
        required_tables = ("schema_migrations", "catalog_builds", "repo_change_sets", "repo_index_runs", "repos")
        if not all(_table_exists(conn, table) for table in required_tables):
            return result(FULL_RECOVERY_REQUIRED, ("refresh metadata unavailable",), None)
        required_catalog_columns = {
            "id", "status", "delta_contract_version", "content_fingerprint",
            "runtime_fingerprint", "manifest_hash", "builder_plan_hash", "source_revisions_json",
            "refresh_readiness", "content_contract_version", "runtime_contract_version",
            "evidence_comparison_version",
        }
        columns = {str(column[1]) for column in conn.execute("PRAGMA table_info(catalog_builds)")}
        if missing := required_catalog_columns - columns:
            return result(FULL_RECOVERY_REQUIRED, ("compatibility columns unavailable",), None)
        row = conn.execute(
            "SELECT * FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return result(FULL_RECOVERY_REQUIRED, ("refresh metadata unavailable",), None)
    if row is None:
        return result(FULL_RECOVERY_REQUIRED, ("no active catalog build",), None)
    reasons: list[str] = []
    required = {
        "refresh_readiness", "content_contract_version", "runtime_contract_version",
        "evidence_comparison_version", "runtime_fingerprint", "content_fingerprint",
        "manifest_hash", "builder_plan_hash", "source_revisions_json",
    }
    if required - columns:
        reasons.append("compatibility columns unavailable")
    elif str(row["refresh_readiness"] or "") != READY:
        reasons.append("active generation requires full recovery")
    if row["delta_contract_version"] != DELTA_CONTRACT_VERSION:
        reasons.append("delta-contract version mismatch")
    if "content_contract_version" in columns and row["content_contract_version"] != CATALOG_CONTENT_VERSION:
        reasons.append("content-contract version mismatch")
    if "runtime_contract_version" in columns and row["runtime_contract_version"] != RUNTIME_CONTRACT_VERSION:
        reasons.append("runtime-contract version mismatch")
    if "evidence_comparison_version" in columns and row["evidence_comparison_version"] != EVIDENCE_COMPARISON_VERSION:
        reasons.append("evidence-comparison version mismatch")
    if not row["content_fingerprint"]:
        reasons.append("active content fingerprint unavailable")
    if not row["runtime_fingerprint"]:
        reasons.append("runtime fingerprint unavailable")
    if not row["manifest_hash"] or not row["builder_plan_hash"]:
        reasons.append("active manifest or builder plan unavailable")
    try:
        migrations = {
            str(row[0]) for row in conn.execute("SELECT name FROM schema_migrations")
        }
        if REFRESH_ATTEMPTS_AND_READINESS_MIGRATION not in migrations:
            reasons.append("readiness migration unavailable")
        if REFRESH_RELIABILITY_GATES_MIGRATION not in migrations:
            reasons.append("reliability migration unavailable")
    except sqlite3.Error:
        reasons.append("refresh metadata unavailable")
    try:
        revisions = json.loads(str(row["source_revisions_json"]))
        if not isinstance(revisions, dict) or revisions != active_source_revisions(conn):
            reasons.append("active generation revision metadata is inconsistent")
    except (TypeError, ValueError, json.JSONDecodeError):
        reasons.append("active source revisions are invalid")

    try:
      repo_rows = conn.execute(
        "SELECT id,repo_key,indexed_commit_sha FROM repos WHERE lifecycle_state='active' ORDER BY repo_key"
      ).fetchall()
    except sqlite3.Error:
      return result(FULL_RECOVERY_REQUIRED, tuple(sorted(set(reasons + ["refresh metadata unavailable"]))), int(row["id"]))
    for repo_id, repo_key, sha in repo_rows:
        try:
            run = conn.execute(
            """SELECT rir.id,rir.validation_summary FROM repo_index_runs rir
               WHERE rir.repo_id=? AND rir.status='active' AND rir.commit_sha IS ?
               ORDER BY rir.id DESC LIMIT 1""",
            (repo_id, sha),
            ).fetchone()
        except sqlite3.Error:
            reasons.append("refresh metadata unavailable")
            continue
        if run is None:
            reasons.append(f"active run unavailable for {repo_key}")
            continue
        if not conn.execute(
            """SELECT 1 FROM repo_change_sets WHERE repo_index_run_id=?
               AND status='succeeded'""",
            (run["id"],),
        ).fetchone():
            reasons.append(f"active change-set unavailable for {repo_key}")
        try:
            validate_quality_run(__import__("json").loads(str(run["validation_summary"])))
            summary = __import__("json").loads(str(run["validation_summary"]))
            if summary["kind"] == "reference":
                resolve_reference_quality_run(conn, int(repo_id), int(run["id"]), summary)
        except (TypeError, ValueError, RefreshQualityError, sqlite3.Error):
            reasons.append(f"quality evidence unavailable for {repo_key}")
    return result(
        READY if not reasons else FULL_RECOVERY_REQUIRED,
        tuple(sorted(set(reasons))),
        int(row["id"]),
    )
