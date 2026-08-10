"""Immutable OpenAPI workflow endpoint facts for repo-v1."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml

from catalog.repo_v1_openapi import HTTP_METHODS, endpoint_key
from catalog.source_snapshot import SourceSnapshot

EXTRACTOR = "repo_v1_workflows"
EXTRACTOR_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class WorkflowStats:
    fact_count: int
    diagnostic_count: int
    resolved_entity_link_count: int
    unresolved_entity_link_count: int
    ambiguous_entity_link_count: int


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def _key(
    kind: str, repo_key: str, path: str, pointer: str, extra: object = None
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "kind": kind,
                "repo_key": repo_key,
                "path": path,
                "pointer": pointer,
                "extra": extra,
            }
        ).encode()
    ).hexdigest()


def _evidence(fields: dict[str, object]) -> str:
    return _canonical(fields)


def _route(path: str) -> tuple[str, str, str] | None:
    parts = path.split("/")
    if (
        len(parts) != 5
        or parts[0]
        or parts[1] != "workflows"
        or any(not x for x in parts[2:])
    ):
        return None
    return parts[2], parts[3], parts[4]


def _yaml(source: bytes) -> object:
    return yaml.safe_load(source.decode("utf-8"))


def _transition(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("type"), str)
        or not isinstance(value.get("from"), list)
        or not isinstance(value.get("to"), list)
    ):
        return None
    if not all(isinstance(x, str) and x for x in value["from"] + value["to"]):
        return None
    return _canonical({"type": value["type"], "from": value["from"], "to": value["to"]})


def extract_snapshot_workflows(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> WorkflowStats:
    del show_progress
    repo = conn.execute("SELECT repo_key FROM repos WHERE id=?", (repo_id,)).fetchone()
    if repo is None:
        raise RuntimeError(f"unknown repository: {repo_id}")
    repo_key = str(repo[0])
    conn.execute("DELETE FROM workflow_diagnostics WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM workflow_facts WHERE repo_id=?", (repo_id,))
    rows = conn.execute(
        """SELECT e.*,d.path AS document_path,d.file_id AS document_file_id,d.source_commit_sha AS document_sha
                          FROM rest_endpoints e JOIN openapi_documents d ON d.id=e.document_id
                          WHERE e.repo_id=? ORDER BY d.path,e.source_pointer,e.http_method""",
        (repo_id,),
    ).fetchall()
    counts = {"resolved": 0, "unresolved": 0, "ambiguous": 0}
    diagnostics = 0
    for row in rows:
        route = _route(str(row["path_template"]))
        path = str(row["document_path"])
        filename = PurePosixPath(path).name.lower()
        if (
            route is None
            or not path.startswith("app/source/openapispec/")
            or "/paths/" not in path
            or not filename.endswith((".api.yaml", ".api.yml"))
            or str(row["http_method"]) not in HTTP_METHODS
        ):
            continue
        raw = (snapshot.snapshot_root / PurePosixPath(path)).read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        try:
            doc = _yaml(raw)
            pointer_parts = str(row["source_pointer"]).split("/")
            path_key = pointer_parts[2].replace("~1", "/").replace("~0", "~")
            method = pointer_parts[-1]
            operation = doc.get("paths", {}).get(path_key, {}).get(method, {})
        except (
            UnicodeDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            yaml.YAMLError,
        ):
            continue
        module, object_name, action = route
        transition_value = (
            operation.get("x-transition") if isinstance(operation, dict) else None
        )
        transition_json = _transition(transition_value)
        endpoint_id = int(row["id"])
        link_rows = conn.execute(
            "SELECT entity_occurrence_id FROM openapi_entity_links WHERE repo_id=? AND document_id=(SELECT document_id FROM rest_endpoints WHERE id=?) ORDER BY id",
            (repo_id, endpoint_id),
        ).fetchall()
        if len(link_rows) == 1:
            status, occurrence_id = "resolved", int(link_rows[0][0])
        elif len(link_rows) == 0:
            status, occurrence_id = "unresolved", None
        else:
            status, occurrence_id = "ambiguous", None
        counts[status] += 1
        lines = raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1)
        fields = {
            "module": module,
            "object_name": object_name,
            "action": action,
            "http_method": str(row["http_method"]),
            "path_template": str(row["path_template"]),
            "operation_id": row["operation_id"],
            "transition_json": transition_json,
            "entity_link_status": status,
            "entity_occurrence_id": occurrence_id,
            "source_path": path,
            "source_pointer": row["source_pointer"],
            "source_commit_sha": row["document_sha"],
            "source_hash": source_hash,
            "start_line": 1,
            "end_line": lines,
        }
        evidence = _evidence(fields)
        wkey = endpoint_key(
            repo_key,
            path,
            str(row["path_template"]),
            str(row["http_method"]),
            str(row["source_pointer"]),
        )
        workflow_id = int(
            conn.execute(
                """INSERT INTO workflow_facts(repo_id,workflow_key,endpoint_id,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,module,object_name,action,http_method,path_template,operation_id,transition_json,entity_occurrence_id,entity_link_status,evidence,extractor,extractor_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    repo_id,
                    wkey,
                    endpoint_id,
                    int(row["document_file_id"]),
                    path,
                    str(row["document_sha"]),
                    source_hash,
                    str(row["source_pointer"]),
                    1,
                    lines,
                    module,
                    object_name,
                    action,
                    str(row["http_method"]),
                    str(row["path_template"]),
                    row["operation_id"],
                    transition_json,
                    occurrence_id,
                    status,
                    evidence,
                    EXTRACTOR,
                    EXTRACTOR_VERSION,
                ),
            ).lastrowid
        )
        if transition_value is not None and transition_json is None:
            diagnostics += 1
            _diagnostic(
                conn,
                repo_id,
                int(row["document_file_id"]),
                workflow_id,
                path,
                source_hash,
                str(row["source_pointer"]),
                1,
                lines,
                "workflow.transition.invalid",
                "x-transition is not a valid transition mapping",
                {"value": transition_value},
            )
        if status != "resolved":
            diagnostics += 1
            code = "workflow.entity_link." + status
            _diagnostic(
                conn,
                repo_id,
                int(row["document_file_id"]),
                workflow_id,
                path,
                source_hash,
                str(row["source_pointer"]),
                1,
                lines,
                code,
                f"workflow endpoint has {len(link_rows)} same-document entity links",
                {"link_count": len(link_rows)},
            )
    return WorkflowStats(
        sum(counts.values()),
        diagnostics,
        counts["resolved"],
        counts["unresolved"],
        counts["ambiguous"],
    )


def _diagnostic(
    conn,
    repo_id,
    file_id,
    workflow_id,
    path,
    source_hash,
    pointer,
    start,
    end,
    code,
    message,
    detail,
):
    evidence = _evidence(
        {"source_path": path, "source_pointer": pointer, "code": code, **detail}
    )
    key = _key(
        "workflow-diagnostic",
        "ia-main",
        path,
        pointer,
        {"code": code, "detail": detail},
    )
    conn.execute(
        """INSERT INTO workflow_diagnostics(repo_id,file_id,workflow_id,diagnostic_key,severity,code,message,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            repo_id,
            file_id,
            workflow_id,
            key,
            "warning",
            code,
            message,
            conn.execute(
                "SELECT target_commit_sha FROM repos WHERE id=?", (repo_id,)
            ).fetchone()[0],
            source_hash,
            pointer,
            start,
            end,
            evidence,
            EXTRACTOR,
            EXTRACTOR_VERSION,
        ),
    )


def validate_workflow_candidate(
    conn: sqlite3.Connection, *, repo_id: int, target_commit_sha: str
) -> None:
    repo = conn.execute("SELECT repo_key FROM repos WHERE id=?", (repo_id,)).fetchone()
    if repo is None:
        raise RuntimeError("workflow repository is missing")
    for row in conn.execute(
        "SELECT w.*,e.endpoint_key,e.repo_id AS endpoint_repo,d.path AS document_path,f.repo_id AS file_repo,f.path AS file_path,f.source_commit_sha AS file_sha FROM workflow_facts w LEFT JOIN rest_endpoints e ON e.id=w.endpoint_id LEFT JOIN openapi_documents d ON d.id=e.document_id LEFT JOIN files f ON f.id=w.source_file_id WHERE w.repo_id=?",
        (repo_id,),
    ):
        expected = endpoint_key(
            str(repo[0]),
            str(row["document_path"]),
            str(row["path_template"]),
            str(row["http_method"]),
            str(row["source_pointer"]),
        )
        route = _route(str(row["path_template"]))
        document_path = str(row["document_path"])
        if (
            route is None
            or not document_path.startswith("app/source/openapispec/")
            or "/paths/" not in document_path
            or not PurePosixPath(document_path)
            .name.lower()
            .endswith((".api.yaml", ".api.yml"))
            or str(row["http_method"]) not in HTTP_METHODS
            or not str(row["source_pointer"]).startswith("/paths/")
            or not str(row["source_pointer"]).endswith("/" + str(row["http_method"]))
            or row["endpoint_repo"] != repo_id
            or row["file_repo"] != repo_id
            or row["file_path"] != row["source_path"]
            or row["workflow_key"] != expected
            or row["source_commit_sha"] != target_commit_sha
            or row["source_commit_sha"] != row["file_sha"]
            or not _SHA256.fullmatch(str(row["source_hash"]))
            or row["extractor"] != EXTRACTOR
            or row["extractor_version"] != EXTRACTOR_VERSION
            or int(row["start_line"]) != 1
            or int(row["end_line"]) < int(row["start_line"])
            or row["entity_link_status"] == "resolved"
            and row["entity_occurrence_id"] is None
            or row["entity_link_status"] != "resolved"
            and row["entity_occurrence_id"] is not None
        ):
            raise RuntimeError(
                "workflow fact ownership, provenance, or key validation failed"
            )
        try:
            evidence = json.loads(str(row["evidence"]))
        except ValueError as exc:
            raise RuntimeError("workflow evidence is not canonical JSON") from exc
        expected_fields = {
            "module": row["module"],
            "object_name": row["object_name"],
            "action": row["action"],
            "http_method": row["http_method"],
            "path_template": row["path_template"],
            "operation_id": row["operation_id"],
            "transition_json": row["transition_json"],
            "entity_link_status": row["entity_link_status"],
            "entity_occurrence_id": row["entity_occurrence_id"],
            "source_path": row["source_path"],
            "source_pointer": row["source_pointer"],
            "source_commit_sha": row["source_commit_sha"],
            "source_hash": row["source_hash"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
        }
        if evidence != expected_fields or _canonical(evidence) != str(row["evidence"]):
            raise RuntimeError("workflow evidence does not match persisted fields")
        links = conn.execute(
            "SELECT entity_occurrence_id FROM openapi_entity_links WHERE repo_id=? AND document_id=(SELECT document_id FROM rest_endpoints WHERE id=?) ORDER BY id",
            (repo_id, row["endpoint_id"]),
        ).fetchall()
        expected_link = (
            int(row["entity_occurrence_id"])
            if row["entity_occurrence_id"] is not None
            else None
        )
        if (
            (
                row["entity_link_status"] == "resolved"
                and (len(links) != 1 or int(links[0][0]) != expected_link)
            )
            or (row["entity_link_status"] == "unresolved" and links)
            or (row["entity_link_status"] == "ambiguous" and len(links) <= 1)
        ):
            raise RuntimeError("workflow entity-link cardinality validation failed")
    for row in conn.execute(
        "SELECT d.*,f.repo_id AS file_repo,f.path AS file_path,f.source_commit_sha AS file_sha FROM workflow_diagnostics d LEFT JOIN files f ON f.id=d.file_id WHERE d.repo_id=?",
        (repo_id,),
    ):
        if (
            row["file_repo"] != repo_id
            or row["file_sha"] != target_commit_sha
            or row["source_commit_sha"] != target_commit_sha
            or row["extractor"] != EXTRACTOR
            or row["extractor_version"] != EXTRACTOR_VERSION
            or not _SHA256.fullmatch(str(row["source_hash"]))
            or row["severity"] != "warning"
            or row["code"]
            not in {
                "workflow.transition.invalid",
                "workflow.entity_link.unresolved",
                "workflow.entity_link.ambiguous",
            }
            or int(row["start_line"]) < 1
            or int(row["end_line"]) < int(row["start_line"])
            or _canonical(json.loads(row["evidence"])) != str(row["evidence"])
        ):
            raise RuntimeError("workflow diagnostic validation failed")
