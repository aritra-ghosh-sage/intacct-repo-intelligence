"""Read-only, repo-v1-native PR impact tracing.

This module deliberately has no dependency on refresh, graph, MCP, or catalog
delta orchestration.  The only Git input is the revision pair in the Step 0
fixture.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from catalog.pr_impact_manifest import resolve_manifest_repo_root


ERROR_CODES = {
    "catalog_unavailable", "catalog_schema_mismatch", "catalog_integrity_failure",
    "catalog_foreign_key_failure", "active_build_missing", "repo_v1_single_repository",
    "repo_not_found", "catalog_revision_mismatch", "empty_diff", "malformed_git_revision",
    "changed_path_mismatch",
}
SURFACE_STATUSES = {"available", "empty", "unavailable", "unresolved", "ambiguous", "stale", "deferred"}
_STATUS_MAP = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied"}
_REQUIRED_TABLES = {
    "catalog_builds", "repos", "files", "symbols", "relationships", "entity_nodes",
    "entity_occurrences", "entity_diagnostics", "symbol_diagnostics", "openapi_documents",
    "openapi_entity_links", "rest_endpoints", "openapi_diagnostics", "ui_surfaces", "ui_artifacts",
    "ui_fields", "ui_events", "ui_includes", "ui_diagnostics", "nextgen_families", "nextgen_artifacts",
    "nextgen_diagnostics",
}


class Step1Error(Exception):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        self.code, self.message, self.extra = code, message, extra
        super().__init__(message)


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    old_path: str | None = None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)


def _fixture(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise Step1Error("malformed_fixture", str(exc), path=str(path)) from exc
    if not isinstance(value, dict) or not isinstance(value.get("pull_request"), dict):
        raise Step1Error("malformed_fixture", "fixture must contain pull_request")
    return value


def _revision(repo: Path, value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        raise Step1Error("malformed_git_revision", f"{label} must be a full Git object ID", revision=label)
    result = _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    resolved = result.stdout.decode().strip()
    if result.returncode or resolved.lower() != value.lower():
        raise Step1Error("malformed_git_revision", f"{label} is not the exact committed revision", revision=label)
    return resolved


def _changed_paths(repo: Path, base: str, target: str) -> list[ChangedFile]:
    result = _git(repo, "diff", "--raw", "-z", "-M", "--no-abbrev", base, target, "--")
    if result.returncode:
        raise Step1Error("malformed_git_revision", result.stderr.decode(errors="replace").strip())
    fields = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    out: list[ChangedFile] = []
    i = 0
    while i < len(fields) and fields[i]:
        header = fields[i]
        if not header.startswith(":") or " " not in header:
            raise Step1Error("malformed_git_diff", "Git raw diff record is malformed")
        status = header.rsplit(" ", 1)[1]
        i += 1
        if i >= len(fields) or not fields[i]:
            raise Step1Error("malformed_git_diff", "Git raw diff record has no path")
        first = fields[i]
        i += 1
        code = status[:1]
        if code in {"R", "C"}:
            if i >= len(fields) or not fields[i]:
                raise Step1Error("malformed_git_diff", "rename/copy record has no target path")
            second = fields[i]
            i += 1
            out.append(ChangedFile(second, _STATUS_MAP[code], first))
        else:
            out.append(ChangedFile(first, _STATUS_MAP.get(code, code.lower())))
    return sorted(out, key=lambda x: (x.path, x.status, x.old_path or ""))


def _safe_path(path: str) -> bool:
    return bool(path) and "\x00" not in path and not path.startswith("/") and "\\" not in path and all(
        part not in {"", ".", ".."} for part in PurePosixPath(path).parts
    )


def _open_catalog(path: Path, target_sha: str, repo_key: str) -> tuple[sqlite3.Connection, int, dict[str, Any]]:
    if not path.is_file():
        raise Step1Error("catalog_unavailable", f"catalog does not exist: {path}")
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
    except sqlite3.Error as exc:
        raise Step1Error("catalog_unavailable", str(exc)) from exc
    try:
        expected = sqlite3.connect(":memory:")
        try:
            expected.executescript(Path(__file__).with_name("repo_v1_schema.sql").read_text())
            actual_tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            expected_tables = {
                r[0] for r in expected.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            actual_tables.discard("sqlite_sequence")
            expected_tables.discard("sqlite_sequence")
            if actual_tables != expected_tables:
                raise Step1Error(
                    "catalog_schema_mismatch",
                    "active tables do not match repo-v1 schema",
                    missing=sorted(expected_tables - actual_tables),
                    extra=sorted(actual_tables - expected_tables),
                )
            for table in sorted(expected_tables):
                quoted = '"' + table.replace('"', '""') + '"'
                actual_columns = [tuple(r) for r in conn.execute(f"PRAGMA table_info({quoted})")]
                expected_columns = [tuple(r) for r in expected.execute(f"PRAGMA table_info({quoted})")]
                if actual_columns != expected_columns:
                    raise Step1Error(
                        "catalog_schema_mismatch",
                        f"column definitions do not match repo-v1 schema for {table}",
                        table=table,
                    )
                actual_foreign_keys = [tuple(r) for r in conn.execute(f"PRAGMA foreign_key_list({quoted})")]
                expected_foreign_keys = [tuple(r) for r in expected.execute(f"PRAGMA foreign_key_list({quoted})")]
                if actual_foreign_keys != expected_foreign_keys:
                    raise Step1Error(
                        "catalog_schema_mismatch",
                        f"foreign keys do not match repo-v1 schema for {table}",
                        table=table,
                    )
                actual_indexes = {
                    str(r[1]): tuple(r[2:])
                    for r in conn.execute(f"PRAGMA index_list({quoted})")
                    if str(r[3]) == "c"
                }
                expected_indexes = {
                    str(r[1]): tuple(r[2:])
                    for r in expected.execute(f"PRAGMA index_list({quoted})")
                    if str(r[3]) == "c"
                }
                if actual_indexes != expected_indexes:
                    raise Step1Error(
                        "catalog_schema_mismatch",
                        f"indexes do not match repo-v1 schema for {table}",
                        table=table,
                        missing=sorted(set(expected_indexes) - set(actual_indexes)),
                        extra=sorted(set(actual_indexes) - set(expected_indexes)),
                    )
                for index in sorted(expected_indexes):
                    actual_info = [tuple(r) for r in conn.execute(f"PRAGMA index_info(\"{index.replace(chr(34), chr(34) * 2)}\")")]
                    expected_info = [tuple(r) for r in expected.execute(f"PRAGMA index_info(\"{index.replace(chr(34), chr(34) * 2)}\")")]
                    if actual_info != expected_info:
                        raise Step1Error(
                            "catalog_schema_mismatch",
                            f"index columns do not match repo-v1 schema for {index}",
                            table=table,
                            index=index,
                        )
                    if expected_indexes[index][-1]:
                        actual_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index,)).fetchone()[0]
                        expected_sql = expected.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index,)).fetchone()[0]
                        if " ".join(str(actual_sql).split()).lower() != " ".join(str(expected_sql).split()).lower():
                            raise Step1Error(
                                "catalog_schema_mismatch",
                                f"partial index predicate does not match repo-v1 schema for {index}",
                                table=table,
                                index=index,
                            )
                expected_sql = expected.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                if "check" in str(expected_sql).lower():
                    actual_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                    if " ".join(str(actual_sql).split()).lower() != " ".join(str(expected_sql).split()).lower():
                        raise Step1Error(
                            "catalog_schema_mismatch",
                            f"CHECK constraints do not match repo-v1 schema for {table}",
                            table=table,
                        )
        finally:
            expected.close()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Step1Error("catalog_integrity_failure", "SQLite integrity_check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise Step1Error("catalog_foreign_key_failure", "SQLite foreign_key_check failed")
        builds = conn.execute("SELECT id, source_revisions_json FROM catalog_builds WHERE status='active'").fetchall()
        if len(builds) != 1:
            raise Step1Error("active_build_missing", "exactly one active build is required")
        repos = conn.execute("SELECT * FROM repos ORDER BY id").fetchall()
        if len(repos) != 1:
            raise Step1Error("repo_v1_single_repository", "Step 1 requires exactly one repo-v1 repository")
        repo = repos[0]
        if repo["repo_key"] != repo_key:
            raise Step1Error("repo_not_found", f"repo_key {repo_key!r} is not the active repo")
        if repo["target_commit_sha"] != target_sha:
            raise Step1Error("catalog_revision_mismatch", "catalog target SHA differs from fixture target SHA")
        build_id = int(builds[0]["id"])
        if int(repo["build_id"]) != build_id:
            raise Step1Error("catalog_provenance_mismatch", "repository is not owned by the active build")
        try:
            revisions = json.loads(str(builds[0]["source_revisions_json"]))
        except json.JSONDecodeError as exc:
            raise Step1Error("catalog_provenance_mismatch", "active build source revisions are invalid JSON") from exc
        if not isinstance(revisions, dict) or revisions.get(repo_key) != target_sha:
            raise Step1Error("catalog_provenance_mismatch", "active build source revision differs from fixture target SHA")
        return conn, int(repo["id"]), {"build_id": build_id, "repo_key": repo_key, "target_revision": target_sha, "integrity_check": "ok", "foreign_key_check": "ok"}
    except Exception:
        conn.close()
        raise


def _evidence(row: sqlite3.Row, path_key: str = "path", revision: str = "") -> dict[str, Any]:
    keys = set(row.keys())
    evidence: Any = row["evidence"] if "evidence" in keys else None
    if isinstance(evidence, str):
        try: evidence = json.loads(evidence)
        except json.JSONDecodeError: pass
    location = {}
    for key in ("start_line", "end_line", "source_line", "source_pointer"):
        if key in keys and row[key] is not None: location[key] = row[key]
    source_key = "source_path" if "source_path" in keys else ("file_path" if "file_path" in keys else path_key)
    result = {"catalog_record_id": int(row["id"]), "source_path": str(row[source_key]),
              "target_revision": revision, "source_location": location or None, "evidence": evidence,
              "extractor": row["extractor"] if "extractor" in keys else None}
    if "source_commit_sha" in keys:
        result["catalog_source_revision"] = row["source_commit_sha"]
    for key in ("resolution_class", "resolution_reason"):
        if key in keys:
            result[key] = row[key]
    return result


def _rows(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...], path: str, revision: str) -> list[dict[str, Any]]:
    return [_evidence(row, path, revision) for row in conn.execute(sql, args).fetchall()]


def _surface(name: str, rows: list[dict[str, Any]], supported: bool = True) -> dict[str, Any]:
    if not supported: return {"surface": name, "status": "unavailable", "facts": [], "warning": "repo-v1 does not model this surface"}
    if not rows: return {"surface": name, "status": "empty", "facts": [], "warning": "No direct repo-v1 rows matched; this is not proof of no impact"}
    if any(row.get("catalog_source_revision") != row.get("target_revision") for row in rows):
        return {"surface": name, "status": "stale", "facts": rows, "warning": "Direct repo-v1 rows are not from the fixture target revision"}
    if any(row.get("resolution_reason") == "ambiguous_project_symbol" for row in rows):
        return {"surface": name, "status": "ambiguous", "facts": rows, "warning": "Direct relationship rows include ambiguous catalog resolutions"}
    if any(row.get("resolution_class") not in (None, "project_resolved") for row in rows):
        return {"surface": name, "status": "unresolved", "facts": rows, "warning": "Direct relationship rows include unresolved catalog resolutions"}
    return {"surface": name, "status": "available", "facts": rows}


def _onboarding(config: Path) -> list[dict[str, Any]]:
    required = ("ia-restapi-automation-tests", "ia-gwdata-gl")
    try: data = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError): return [{"repository": x, "status": "deferred", "reason": "manifest unavailable"} for x in required]
    entries = {
        entry.get("repo_key"): entry
        for entry in data.get("repositories", [])
        if isinstance(entry, dict) and entry.get("repo_key") in required
    } if isinstance(data, dict) else {}
    result = []
    for repo_key in required:
        entry = entries.get(repo_key)
        if entry is None:
            result.append({"repository": repo_key, "status": "deferred", "reason": "repository is absent from the workspace manifest"})
            continue
        result.append({"repository": repo_key, "status": "deferred", "manifest": {
            k: entry.get(k) for k in ("local_root", "enabled", "depends_on", "profile", "builders", "storage") if k in entry
        }, "reason": "manifest-only feasibility; no external snapshot or impact evidence was read"})
    return result


def analyze_fixture(
    fixture: str | Path,
    manifest: str | Path,
    active_db: str | Path,
    repo_key: str,
) -> dict[str, Any]:
    document = _fixture(Path(fixture))
    pr = document["pull_request"]
    try:
        repo = resolve_manifest_repo_root(manifest, repo_key)
    except ValueError as exc:
        code, _, message = str(exc).partition(": ")
        raise Step1Error(code or "manifest_invalid", message or str(exc)) from exc
    base, target = _revision(repo, pr.get("base_revision"), "base_revision"), _revision(repo, pr.get("target_revision"), "target_revision")
    changed = _changed_paths(repo, base, target)
    if not changed: raise Step1Error("empty_diff", "Step 1 requires a non-empty Git diff")
    declared_rows = document.get("changed_files")
    if not isinstance(declared_rows, list) or not declared_rows:
        raise Step1Error("changed_path_mismatch", "fixture changed_files must be a non-empty list")
    declared = {(str(x.get("path")), str(x.get("status"))) for x in declared_rows if isinstance(x, dict)}
    actual = {(x.path, x.status) for x in changed}
    if len(declared) != len(declared_rows) or declared != actual:
        raise Step1Error("changed_path_mismatch", "fixture changed_files do not match the exact Git diff", expected=sorted(actual), declared=sorted(declared))
    for change in changed:
        if not _safe_path(change.path): raise Step1Error("malformed_git_diff", f"unsafe changed path: {change.path}")
    conn, repo_id, preflight = _open_catalog(Path(active_db), target, repo_key)
    try:
        paths = [x.path for x in changed]
        marks = ",".join("?" for _ in paths)
        files = {str(r["path"]): r for r in conn.execute(f"SELECT * FROM files WHERE repo_id=? AND path IN ({marks}) ORDER BY path", (repo_id, *paths)).fetchall()}
        file_ids = [int(r["id"]) for r in files.values()]
        ids = ",".join("?" for _ in file_ids) or "NULL"
        direct = [
            _surface("files", [_evidence(r, "path", target) for r in files.values()]),
            _surface("symbols", _rows(conn, f"SELECT s.*, f.path AS source_path, f.source_commit_sha AS source_commit_sha FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.repo_id=? AND s.file_id IN ({ids}) ORDER BY s.file_id,s.start_line,s.id", (repo_id, *file_ids), "source_path", target)),
            _surface("outgoing_relationships", _rows(conn, f"SELECT r.*, f.source_commit_sha AS source_commit_sha FROM relationships r JOIN files f ON f.id=r.file_id WHERE r.repo_id=? AND r.file_id IN ({ids}) ORDER BY r.id", (repo_id, *file_ids), "file_path", target)),
            _surface("incoming_relationships", _rows(conn, f"SELECT r.*, f.source_commit_sha AS source_commit_sha FROM relationships r JOIN files f ON f.id=r.file_id JOIN symbols s ON s.id=r.target_symbol_id WHERE r.repo_id=? AND s.file_id IN ({ids}) ORDER BY r.id", (repo_id, *file_ids), "file_path", target)),
            _surface("entity_occurrences", _rows(conn, f"SELECT e.*, f.path AS source_path FROM entity_occurrences e JOIN files f ON f.id=e.source_file_id WHERE e.repo_id=? AND e.source_file_id IN ({ids}) ORDER BY e.id", (repo_id, *file_ids), "source_path", target) if any(x.endswith(".ent") for x in paths) else [], any(x.endswith(".ent") for x in paths)),
            _surface("openapi_documents", _rows(conn, f"SELECT * FROM openapi_documents WHERE repo_id=? AND file_id IN ({ids}) ORDER BY id", (repo_id, *file_ids), "path", target)),
            _surface("openapi_entity_links", _rows(conn, f"SELECT l.*, d.path AS source_path FROM openapi_entity_links l JOIN openapi_documents d ON d.id=l.document_id JOIN entity_occurrences eo ON eo.id=l.entity_occurrence_id WHERE l.repo_id=? AND (d.file_id IN ({ids}) OR eo.source_file_id IN ({ids})) ORDER BY l.id", (repo_id, *file_ids, *file_ids), "source_path", target)),
            _surface("rest_endpoints", _rows(conn, f"SELECT e.*, d.path AS source_path FROM rest_endpoints e JOIN openapi_documents d ON d.id=e.document_id WHERE e.repo_id=? AND d.file_id IN ({ids}) ORDER BY e.id", (repo_id, *file_ids), "source_path", target)),
            _surface("actionui", _rows(conn, f"SELECT * FROM ui_surfaces WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id", (repo_id, *file_ids), "source_path", target)),
            _surface("actionui_artifacts", _rows(conn, f"SELECT * FROM ui_artifacts WHERE repo_id=? AND file_id IN ({ids}) ORDER BY id", (repo_id, *file_ids), "source_path", target)),
            _surface("actionui_fields", _rows(conn, f"SELECT f.*, a.source_path AS source_path, a.source_commit_sha AS source_commit_sha FROM ui_fields f JOIN ui_artifacts a ON a.id=f.artifact_id WHERE f.repo_id=? AND a.file_id IN ({ids}) ORDER BY f.id", (repo_id, *file_ids), "source_path", target)),
            _surface("actionui_events", _rows(conn, f"SELECT e.*, a.source_path AS source_path, a.source_commit_sha AS source_commit_sha FROM ui_events e JOIN ui_artifacts a ON a.id=e.artifact_id WHERE e.repo_id=? AND a.file_id IN ({ids}) ORDER BY e.id", (repo_id, *file_ids), "source_path", target)),
            _surface("actionui_includes", _rows(conn, f"SELECT i.*, a.source_path AS source_path, a.source_commit_sha AS source_commit_sha FROM ui_includes i JOIN ui_artifacts a ON a.id=i.artifact_id WHERE i.repo_id=? AND a.file_id IN ({ids}) ORDER BY i.id", (repo_id, *file_ids), "source_path", target)),
            _surface("nextgen", _rows(conn, f"SELECT * FROM nextgen_families WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id", (repo_id, *file_ids), "source_path", target)),
            _surface("nextgen_artifacts", _rows(conn, f"SELECT * FROM nextgen_artifacts WHERE repo_id=? AND file_id IN ({ids}) ORDER BY id", (repo_id, *file_ids), "source_path", target)),
            _surface("source_diagnostics", _rows(conn, f"SELECT d.*, f.path AS source_path FROM symbol_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id", (repo_id, *file_ids), "source_path", target)
            + _rows(conn, f"SELECT d.*, f.path AS source_path FROM entity_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id", (repo_id, *file_ids), "source_path", target)
            + _rows(conn, f"SELECT d.*, f.path AS source_path FROM openapi_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id", (repo_id, *file_ids), "source_path", target)
            + _rows(conn, f"SELECT d.*, f.path AS source_path FROM ui_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id", (repo_id, *file_ids), "source_path", target)
            + _rows(conn, f"SELECT d.*, f.path AS source_path FROM nextgen_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id", (repo_id, *file_ids), "source_path", target)),
            _surface("database_consumers", [], False), _surface("permissions", [], False), _surface("workflows", [], False), _surface("tests", [], False),
        ]
        gaps = [f"{x['surface']}: {x['status']}" for x in direct if x["status"] not in {"available", "unavailable", "deferred"}]
        return {"schema_version": "0.1", "analysis_kind": "pr_impact_step_1", "status": "partial" if gaps else "complete",
                "input": {"fixture": str(Path(fixture)), "manifest": str(Path(manifest)), "repo_root": str(repo), "active_db": str(Path(active_db)), "repo_key": repo_key, "base_revision": base, "target_revision": target},
                "preflight": preflight, "changed_files": [x.__dict__ for x in changed], "direct_traces": direct,
                "onboarding_feasibility": _onboarding(Path(manifest)), "impact_ranking": [], "gaps": gaps,
                "warnings": [x["warning"] for x in direct if "warning" in x], "provenance": {"source": "repo-v1 active SQLite and exact Git diff", "read_only": True,
                "contract": "Git diff validation only; no catalog delta processing."}}
    finally: conn.close()


def blocked_report(error: Step1Error) -> dict[str, Any]:
    return {"schema_version": "0.1", "analysis_kind": "pr_impact_step_1", "status": "blocked", "error": {"code": error.code, "message": error.message, **error.extra},
            "input": {}, "preflight": {},
            "changed_files": [], "direct_traces": [], "onboarding_feasibility": [], "impact_ranking": [], "gaps": [], "warnings": [], "provenance": {"read_only": True}}
