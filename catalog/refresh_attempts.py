"""Durable, append-only audit records for workspace refresh attempts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


class RefreshAuditError(RuntimeError):
    """The refresh ledger could not record a required state transition."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS refresh_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_token TEXT NOT NULL UNIQUE,
 requested_repo_key TEXT NOT NULL, requested_mode TEXT NOT NULL CHECK(requested_mode IN ('auto','full','delta')),
 closure_json TEXT,target_revisions_json TEXT,parent_catalog_build_id INTEGER,parent_build_token TEXT,
 requested_effective_mode TEXT,effective_mode TEXT,manifest_hash TEXT,builder_plan_hash TEXT,
 delta_contract_version INTEGER,content_contract_version INTEGER,runtime_contract_version INTEGER,evidence_comparison_version INTEGER,
 readiness_before TEXT,readiness_after TEXT,fallback_reason TEXT,current_stage TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),failure_class TEXT,failure_detail TEXT,
 retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0,1)),started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_refresh_attempts_started ON refresh_attempts(started_at DESC);
CREATE TABLE IF NOT EXISTS refresh_attempt_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER NOT NULL,sequence INTEGER NOT NULL,stage TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('started','succeeded','failed','diagnostic')),detail TEXT,diagnostic_json TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(attempt_id,sequence),
 FOREIGN KEY(attempt_id) REFERENCES refresh_attempts(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_refresh_attempt_events_attempt ON refresh_attempt_events(attempt_id,sequence);
CREATE TRIGGER IF NOT EXISTS trg_refresh_attempt_events_no_update BEFORE UPDATE ON refresh_attempt_events BEGIN SELECT RAISE(ABORT, 'refresh attempt events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_refresh_attempt_events_no_delete BEFORE DELETE ON refresh_attempt_events BEGIN SELECT RAISE(ABORT, 'refresh attempt events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_refresh_attempts_no_delete BEFORE DELETE ON refresh_attempts BEGIN SELECT RAISE(ABORT, 'refresh attempts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_refresh_attempts_terminal_summary BEFORE UPDATE ON refresh_attempts WHEN OLD.status <> 'running' BEGIN SELECT RAISE(ABORT, 'terminal refresh attempt summary is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_refresh_attempts_terminal_event_match BEFORE UPDATE ON refresh_attempts WHEN NEW.status IN ('succeeded','failed') BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM refresh_attempt_events e WHERE e.attempt_id=OLD.id AND e.sequence=(SELECT MAX(sequence) FROM refresh_attempt_events WHERE attempt_id=OLD.id) AND e.stage=NEW.current_stage AND e.status=NEW.status AND COALESCE(json_extract(e.diagnostic_json,'$.failure_class'),'')=COALESCE(NEW.failure_class,'') AND COALESCE(json_extract(e.diagnostic_json,'$.failure_detail'),'')=COALESCE(NEW.failure_detail,'') AND COALESCE(json_extract(e.diagnostic_json,'$.retryable'),0)=NEW.retryable) THEN RAISE(ABORT, 'terminal refresh attempt summary must match terminal event') END;
END;
"""


def bootstrap_refresh_ledger(active: Path) -> None:
    """Install the independent audit ledger before any catalog admission work."""
    try:
        conn = _connection(active)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.executescript(_LEDGER_DDL)
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RefreshAuditError(f"bootstrap_audit_unavailable: {exc}") from exc


def create_refresh_attempt(active: Path, *, repo_key: str, requested_mode: str) -> str:
    """Create the attempt before manifest loading; ledger failure aborts work."""

    token = str(uuid.uuid4())
    conn = _connection(active)
    try:
        conn.execute("BEGIN IMMEDIATE")
        has_builds = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_builds'").fetchone()
        build_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(catalog_builds)")
        } if has_builds else set()
        parent = conn.execute(
            "SELECT id,build_token,"
            + ("refresh_readiness" if "refresh_readiness" in build_columns else "'full_recovery_required' AS refresh_readiness")
            + " FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone() if {"id", "build_token", "status"}.issubset(build_columns) else None
        cursor = conn.execute(
            """INSERT INTO refresh_attempts(
                   attempt_token,requested_repo_key,requested_mode,
                   parent_catalog_build_id,parent_build_token,readiness_before,
                   current_stage,status
               ) VALUES (?,?,?,?,?,?,?,'running')""",
            (
                token, repo_key, requested_mode,
                int(parent["id"]) if parent else None,
                str(parent["build_token"]) if parent else None,
                str(parent["refresh_readiness"]) if parent else "full_recovery_required",
                "attempt_created",
            ),
        )
        attempt_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO refresh_attempt_events(attempt_id,sequence,stage,status,detail) "
            "VALUES (?,1,'attempt_created','started','refresh lock acquired')",
            (attempt_id,),
        )
        conn.commit()
        return token
    except sqlite3.Error as exc:
        conn.rollback()
        raise RefreshAuditError(f"unable to create refresh attempt: {exc}") from exc
    finally:
        conn.close()


def record_attempt_event(
    active: Path,
    token: str,
    *,
    stage: str,
    status: str = "diagnostic",
    detail: str | None = None,
    fields: dict[str, Any] | None = None,
) -> None:
    """Append one event and atomically update the attempt's current state."""

    allowed = {
        "closure_json", "target_revisions_json", "requested_effective_mode",
        "effective_mode", "manifest_hash", "builder_plan_hash",
        "delta_contract_version", "content_contract_version",
        "runtime_contract_version", "evidence_comparison_version",
        "readiness_after", "fallback_reason", "failure_class", "failure_detail",
        "retryable", "status",
    }
    fields = fields or {}
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise RefreshAuditError("unsupported attempt fields: " + ", ".join(unknown))
    conn = _connection(active)
    try:
        conn.execute("BEGIN IMMEDIATE")
        attempt = conn.execute(
            "SELECT id,status FROM refresh_attempts WHERE attempt_token=?", (token,)
        ).fetchone()
        if attempt is None:
            raise RefreshAuditError(f"unknown refresh attempt token: {token}")
        if attempt["status"] != "running":
            raise RefreshAuditError(f"refresh attempt is already terminal: {token}")
        sequence = int(conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM refresh_attempt_events WHERE attempt_id=?",
            (attempt["id"],),
        ).fetchone()[0])
        terminal = fields.get("status") in {"succeeded", "failed"}
        update = {"current_stage": stage, **fields}
        if terminal:
            update["completed_at"] = "CURRENT_TIMESTAMP"
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in update.items():
            if key == "completed_at":
                assignments.append("completed_at=CURRENT_TIMESTAMP")
            else:
                assignments.append(f"{key}=?")
                params.append(value)
        params.append(token)
        diagnostic = _json(fields) if fields else None
        conn.execute(
            """INSERT INTO refresh_attempt_events(
                   attempt_id,sequence,stage,status,detail,diagnostic_json
               ) VALUES (?,?,?,?,?,?)""",
            (attempt["id"], sequence, stage, status, detail, diagnostic),
        )
        # The event is written first.  Terminal summary triggers can therefore
        # prove that the summary is only a projection of immutable history.
        conn.execute(
            "UPDATE refresh_attempts SET " + ",".join(assignments) + " WHERE attempt_token=?",
            params,
        )
        conn.commit()
    except (sqlite3.Error, RefreshAuditError) as exc:
        conn.rollback()
        raise RefreshAuditError(f"unable to persist refresh attempt event: {exc}") from exc
    finally:
        conn.close()


def record_attempt_failure(active: Path, token: str, *, stage: str, error: Exception) -> None:
    message = str(error)
    race = stage in {
        "source_revision_final", "source_revision_promotion", "parent_cas", "parent_cas_promotion"
    }
    evidence = stage in {
        "snapshot", "candidate_source_verification", "relationship_closure", "delta_apply"
    }
    migration = stage == "migration_apply"
    record_attempt_event(
        active,
        token,
        stage=stage,
        status="failed",
        detail=message,
        fields={
            "status": "failed",
            "failure_class": (
                "migration_failure" if migration
                else "source_race" if stage.startswith("source_revision")
                else "parent_cas_race" if stage.startswith("parent_cas")
                else "evidence_integrity" if evidence
                else "refresh_failure"
            ),
            "failure_detail": message,
            "retryable": int(race),
        },
    )
