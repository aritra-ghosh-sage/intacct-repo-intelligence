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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from catalog.pr_impact_manifest import resolve_manifest_repo_root
from catalog.pr_impact_ranking import rank_direct_traces

ERROR_CODES = {
    "catalog_unavailable",
    "catalog_schema_mismatch",
    "catalog_integrity_failure",
    "catalog_foreign_key_failure",
    "active_build_missing",
    "repo_v1_single_repository",
    "repo_not_found",
    "catalog_revision_mismatch",
    "empty_diff",
    "malformed_git_revision",
    "changed_path_mismatch",
    "malformed_fixture",
    "malformed_git_diff",
    "catalog_provenance_mismatch",
    "metadata_unavailable",
    "metadata_malformed",
    "metadata_repository_mismatch",
    "metadata_revision_mismatch",
    "metadata_changed_path_mismatch",
}
SURFACE_STATUSES = {
    "available",
    "empty",
    "unavailable",
    "unresolved",
    "ambiguous",
    "stale",
    "deferred",
}
REPORT_SCHEMA_VERSION = "0.4"
DOWNSTREAM_RELATION_TYPES = {
    "tests_rest_of",
    "validates_gateway_behavior_of",
    "depends_on_schema_of",
}
DOWNSTREAM_STATUSES = SURFACE_STATUSES
SUPPORTED_SURFACES = {
    "files",
    "symbols",
    "outgoing_relationships",
    "incoming_relationships",
    "entity_occurrences",
    "openapi_documents",
    "openapi_entity_links",
    "rest_endpoints",
    "actionui",
    "actionui_artifacts",
    "actionui_fields",
    "actionui_events",
    "actionui_includes",
    "nextgen",
    "nextgen_artifacts",
    "source_diagnostics",
    "database_consumers",
    "entity_metadata",
    "permissions",
    "workflows",
    "tests",
}
_STATUS_MAP = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
}
_REQUIRED_TABLES = {
    "catalog_builds",
    "repos",
    "files",
    "symbols",
    "relationships",
    "entity_nodes",
    "entity_occurrences",
    "entity_diagnostics",
    "symbol_diagnostics",
    "openapi_documents",
    "openapi_entity_links",
    "rest_endpoints",
    "openapi_diagnostics",
    "ui_surfaces",
    "ui_artifacts",
    "ui_fields",
    "ui_events",
    "ui_includes",
    "ui_diagnostics",
    "nextgen_families",
    "nextgen_artifacts",
    "nextgen_diagnostics",
    "dbschema_tables",
    "dbschema_fields",
    "entity_section_facts",
    "entity_field_facts",
    "entity_schema_mappings",
    "entity_db_table_links",
    "entity_db_field_links",
    "repo_v1_database_diagnostics",
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
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )


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
        raise Step1Error(
            "malformed_git_revision",
            f"{label} must be a full Git object ID",
            revision=label,
        )
    result = _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    resolved = result.stdout.decode().strip()
    if result.returncode or resolved.lower() != value.lower():
        raise Step1Error(
            "malformed_git_revision",
            f"{label} is not the exact committed revision",
            revision=label,
        )
    return resolved


def _changed_paths(repo: Path, base: str, target: str) -> list[ChangedFile]:
    result = _git(repo, "diff", "--raw", "-z", "-M", "--no-abbrev", base, target, "--")
    if result.returncode:
        raise Step1Error(
            "malformed_git_revision", result.stderr.decode(errors="replace").strip()
        )
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
                raise Step1Error(
                    "malformed_git_diff", "rename/copy record has no target path"
                )
            second = fields[i]
            i += 1
            out.append(ChangedFile(second, _STATUS_MAP[code], first))
        else:
            out.append(ChangedFile(first, _STATUS_MAP.get(code, code.lower())))
    return sorted(out, key=lambda x: (x.path, x.status, x.old_path or ""))


def _safe_path(path: str) -> bool:
    return (
        bool(path)
        and "\x00" not in path
        and not path.startswith("/")
        and "\\" not in path
        and all(part not in {"", ".", ".."} for part in PurePosixPath(path).parts)
    )


def _open_catalog(
    path: Path, target_sha: str, repo_key: str, source_repo: Path
) -> tuple[sqlite3.Connection, int, dict[str, Any]]:
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
            expected.executescript(
                Path(__file__).with_name("repo_v1_schema.sql").read_text()
            )
            actual_tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            expected_tables = {
                r[0]
                for r in expected.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
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
                actual_columns = [
                    tuple(r) for r in conn.execute(f"PRAGMA table_info({quoted})")
                ]
                expected_columns = [
                    tuple(r) for r in expected.execute(f"PRAGMA table_info({quoted})")
                ]
                if actual_columns != expected_columns:
                    raise Step1Error(
                        "catalog_schema_mismatch",
                        f"column definitions do not match repo-v1 schema for {table}",
                        table=table,
                    )
                actual_foreign_keys = [
                    tuple(r) for r in conn.execute(f"PRAGMA foreign_key_list({quoted})")
                ]
                expected_foreign_keys = [
                    tuple(r)
                    for r in expected.execute(f"PRAGMA foreign_key_list({quoted})")
                ]
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
                    actual_info = [
                        tuple(r)
                        for r in conn.execute(
                            f'PRAGMA index_info("{index.replace(chr(34), chr(34) * 2)}")'
                        )
                    ]
                    expected_info = [
                        tuple(r)
                        for r in expected.execute(
                            f'PRAGMA index_info("{index.replace(chr(34), chr(34) * 2)}")'
                        )
                    ]
                    if actual_info != expected_info:
                        raise Step1Error(
                            "catalog_schema_mismatch",
                            f"index columns do not match repo-v1 schema for {index}",
                            table=table,
                            index=index,
                        )
                    if expected_indexes[index][-1]:
                        actual_sql = conn.execute(
                            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                            (index,),
                        ).fetchone()[0]
                        expected_sql = expected.execute(
                            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                            (index,),
                        ).fetchone()[0]
                        if (
                            " ".join(str(actual_sql).split()).lower()
                            != " ".join(str(expected_sql).split()).lower()
                        ):
                            raise Step1Error(
                                "catalog_schema_mismatch",
                                f"partial index predicate does not match repo-v1 schema for {index}",
                                table=table,
                                index=index,
                            )
                expected_sql = expected.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0]
                actual_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0]
                if (
                    "check" in str(expected_sql).lower()
                    or "check" in str(actual_sql).lower()
                ):
                    if (
                        " ".join(str(actual_sql).split()).lower()
                        != " ".join(str(expected_sql).split()).lower()
                    ):
                        raise Step1Error(
                            "catalog_schema_mismatch",
                            f"CHECK constraints do not match repo-v1 schema for {table}",
                            table=table,
                        )
        finally:
            expected.close()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Step1Error(
                "catalog_integrity_failure", "SQLite integrity_check failed"
            )
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise Step1Error(
                "catalog_foreign_key_failure", "SQLite foreign_key_check failed"
            )
        builds = conn.execute(
            "SELECT id, source_revisions_json FROM catalog_builds WHERE status='active'"
        ).fetchall()
        if len(builds) != 1:
            raise Step1Error(
                "active_build_missing", "exactly one active build is required"
            )
        repos = conn.execute("SELECT * FROM repos ORDER BY id").fetchall()
        if len(repos) != 1:
            raise Step1Error(
                "repo_v1_single_repository",
                "Step 1 requires exactly one repo-v1 repository",
            )
        repo = repos[0]
        if repo["repo_key"] != repo_key:
            raise Step1Error(
                "repo_not_found", f"repo_key {repo_key!r} is not the active repo"
            )
        catalog_revision = repo["target_commit_sha"]
        if not isinstance(catalog_revision, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40,64}", catalog_revision
        ):
            raise Step1Error(
                "catalog_provenance_mismatch",
                "catalog target SHA is not a full Git commit ID",
            )
        catalog_revision = _revision(source_repo, catalog_revision, "catalog_revision")
        if catalog_revision == target_sha:
            revision_relation = "exact"
        else:
            ancestor = _git(
                source_repo,
                "merge-base",
                "--is-ancestor",
                target_sha,
                catalog_revision,
            )
            if ancestor.returncode != 0:
                raise Step1Error(
                    "catalog_revision_mismatch",
                    "catalog target SHA is not the fixture target or a proven forward revision",
                    target_revision=target_sha,
                    catalog_revision=catalog_revision,
                )
            revision_relation = "forward_compatible"
        build_id = int(builds[0]["id"])
        if int(repo["build_id"]) != build_id:
            raise Step1Error(
                "catalog_provenance_mismatch",
                "repository is not owned by the active build",
            )
        try:
            revisions = json.loads(str(builds[0]["source_revisions_json"]))
        except json.JSONDecodeError as exc:
            raise Step1Error(
                "catalog_provenance_mismatch",
                "active build source revisions are invalid JSON",
            ) from exc
        if (
            not isinstance(revisions, dict)
            or revisions.get(repo_key) != catalog_revision
        ):
            raise Step1Error(
                "catalog_provenance_mismatch",
                "active build source revision differs from catalog target SHA",
            )
        return (
            conn,
            int(repo["id"]),
            {
                "build_id": build_id,
                "repo_key": repo_key,
                "target_revision": target_sha,
                "catalog_revision": catalog_revision,
                "revision_relation": revision_relation,
                "compatibility_evidence": (
                    "catalog target equals fixture target"
                    if revision_relation == "exact"
                    else "fixture target is a Git ancestor of catalog target"
                ),
                "integrity_check": "ok",
                "foreign_key_check": "ok",
            },
        )
    except Exception:
        conn.close()
        raise


def _evidence(
    row: sqlite3.Row, path_key: str = "path", revision: str = ""
) -> dict[str, Any]:
    keys = set(row.keys())
    evidence: Any = row["evidence"] if "evidence" in keys else None
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            pass
    location = {}
    for key in ("start_line", "end_line", "source_line", "source_pointer"):
        if key in keys and row[key] is not None:
            location[key] = row[key]
    source_key = (
        "source_path"
        if "source_path" in keys
        else ("file_path" if "file_path" in keys else path_key)
    )
    result = {
        "catalog_record_id": int(row["id"]),
        "source_path": str(row[source_key]),
        "target_revision": revision,
        "source_location": location or None,
        "evidence": evidence,
        "extractor": row["extractor"] if "extractor" in keys else None,
    }
    if "source_commit_sha" in keys:
        result["catalog_source_revision"] = row["source_commit_sha"]
    for key in (
        "resolution_class",
        "resolution_reason",
        "resolution_status",
        "entity_link_status",
    ):
        if key in keys:
            result[key] = row[key]
    return result


def _rows(
    conn: sqlite3.Connection, sql: str, args: tuple[Any, ...], path: str, revision: str
) -> list[dict[str, Any]]:
    return [
        _evidence(row, path, revision) for row in conn.execute(sql, args).fetchall()
    ]


def _surface(
    name: str,
    rows: list[dict[str, Any]],
    supported: bool = True,
    *,
    catalog_revision: str | None = None,
) -> dict[str, Any]:
    if not supported:
        return {
            "surface": name,
            "status": "unavailable",
            "facts": [],
            "warning": "repo-v1 does not model this surface",
        }
    if not rows:
        return {
            "surface": name,
            "status": "empty",
            "facts": [],
            "warning": "No direct repo-v1 rows matched; this is not proof of no impact",
        }
    if catalog_revision is not None and any(
        row.get("catalog_source_revision") != catalog_revision for row in rows
    ):
        return {
            "surface": name,
            "status": "stale",
            "facts": rows,
            "warning": "Direct repo-v1 rows are not from the active catalog revision",
        }
    if any(
        row.get("resolution_reason") == "ambiguous_project_symbol"
        or row.get("entity_link_status") == "ambiguous"
        for row in rows
    ):
        return {
            "surface": name,
            "status": "ambiguous",
            "facts": rows,
            "warning": "Direct relationship rows include ambiguous catalog resolutions",
        }
    if any(
        row.get("resolution_class") not in (None, "project_resolved")
        or row.get("resolution_status") not in (None, "resolved")
        or row.get("entity_link_status") not in (None, "resolved")
        for row in rows
    ):
        return {
            "surface": name,
            "status": "unresolved",
            "facts": rows,
            "warning": "Direct relationship rows include unresolved catalog resolutions",
        }
    return {"surface": name, "status": "available", "facts": rows}


def _fixture_fact(
    section: str, index: int, value: Any, revision: str
) -> dict[str, Any]:
    if isinstance(value, dict):
        evidence = value.get("evidence")
        path = value.get("path") or value.get("source")
        location = {
            key: value[key]
            for key in ("line", "start_line", "end_line")
            if key in value
        }
        status = value.get("status")
    else:
        evidence, path, location, status = value, None, {}, None
    return {
        "fact_key": f"step0:{section}:{index}",
        "source_path": path,
        "source_location": location or None,
        "target_revision": revision,
        "evidence": evidence if evidence is not None else value,
        "extractor": "pr_impact_step0_fixture",
        "status": status,
    }


def _fixture_surface(
    document: dict[str, Any], section: str, revision: str
) -> dict[str, Any]:
    surfaces = document.get("affected_surfaces")
    surface = surfaces.get(section) if isinstance(surfaces, dict) else None
    if section == "database":
        if (
            isinstance(surface, dict)
            and surface.get("status") == "not_in_scope_for_this_change"
        ):
            fact = _fixture_fact("affected_surfaces.database", 0, surface, revision)
            return {
                "surface": "database_consumers",
                "status": "deferred",
                "facts": [fact],
                "warning": "Step 0 records an explicit not-in-scope assertion; target-revision database evidence was not read",
            }
        if isinstance(surface, dict) and surface.get("status") in {
            "confirmed",
            "assessed",
        }:
            facts = [_fixture_fact("affected_surfaces.database", 0, surface, revision)]
            return {
                "surface": "database_consumers",
                "status": "deferred",
                "facts": facts,
                "warning": "Step 0 database assertions require target-revision catalog evidence and are not direct evidence",
            }
        return {
            "surface": "database_consumers",
            "status": "deferred",
            "facts": [],
            "warning": "Step 0 does not contain exact database evidence",
        }
    obligations = document.get("test_obligations")
    related = document.get("related_repositories")
    facts: list[dict[str, Any]] = []
    if isinstance(obligations, dict):
        for key in ("existing_or_expected", "recommended", "unresolved"):
            values = obligations.get(key, [])
            if isinstance(values, list):
                facts.extend(
                    _fixture_fact(f"test_obligations.{key}", index, value, revision)
                    for index, value in enumerate(values)
                )
    if isinstance(related, list):
        facts.extend(
            _fixture_fact("related_repositories", index, value, revision)
            for index, value in enumerate(related)
        )
    exact = [fact for fact in facts if fact.get("source_path")]
    if exact and all(
        fact.get("status") in {None, "confirmed", "assessed"} for fact in exact
    ):
        return {"surface": "tests", "status": "available", "facts": exact}
    return {
        "surface": "tests",
        "status": "deferred",
        "facts": facts,
        "warning": "Exact target-revision test evidence is unavailable",
    }


def _downstream_repositories(
    document: dict[str, Any], config: Path
) -> list[dict[str, Any]]:
    required = ("ia-restapi-automation-tests", "ia-gwdata-gl")
    related = document.get("related_repositories")
    related_items = related if isinstance(related, list) else []
    fixture_repositories = {
        item.get("repository"): item
        for item in related_items
        if isinstance(item, dict) and isinstance(item.get("repository"), str)
    }
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        entries = []
    else:
        entries = (
            data.get("repositories", [])
            if isinstance(data, dict) and isinstance(data.get("repositories"), list)
            else []
        )

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("repo_key") not in required:
            continue
        depends_on = entry.get("depends_on")
        if not isinstance(depends_on, list) or "ia-main" not in depends_on:
            continue
        repo_key = entry.get("repo_key")
        repository = next(
            (
                name
                for name in fixture_repositories
                if name == f"intacct/{repo_key}" or name.endswith(f"/{repo_key}")
            ),
            repo_key,
        )
        if not isinstance(repository, str) or repository in seen:
            continue
        seen.add(repository)
        fixture_item = fixture_repositories.get(repository, {})
        result.append(
            {
                "repository": repository,
                "repo_key": repo_key,
                "status": "deferred",
                "source_relationship": fixture_item.get("relationship"),
                "relationships": [],
                "manifest": {
                    k: entry.get(k)
                    for k in (
                        "local_root",
                        "enabled",
                        "depends_on",
                        "profile",
                        "builders",
                        "storage",
                    )
                    if k in entry
                },
                "reason": "manifest-only feasibility; no external snapshot or impact evidence was read",
            }
        )
    for repository, item in fixture_repositories.items():
        if repository in seen:
            continue
        seen.add(repository)
        result.append(
            {
                "repository": repository,
                "repo_key": None,
                "status": "deferred",
                "source_relationship": item.get("relationship"),
                "relationships": [],
                "manifest": {},
                "reason": "repository is not mapped to a downstream manifest entry",
            }
        )
    for repo_key in required:
        if repo_key in seen:
            continue
        seen.add(repo_key)
        result.append(
            {
                "repository": repo_key,
                "repo_key": repo_key,
                "status": "deferred",
                "source_relationship": None,
                "relationships": [],
                "manifest": {},
                "reason": "repository is absent from the workspace manifest",
            }
        )
    return result


def _confidence(
    direct: list[dict[str, Any]],
    downstream: list[dict[str, Any]],
    preflight: dict[str, Any],
    gaps: list[str],
) -> dict[str, Any]:
    total_surfaces = len(SUPPORTED_SURFACES)
    available_surfaces = sum(item.get("status") == "available" for item in direct)
    availability_score = round(100 * available_surfaces / max(1, total_surfaces))
    relation = preflight.get("revision_relation")
    freshness_score = {"exact": 100, "forward_compatible": 80}.get(relation, 0)
    gap_score = max(0, 100 - min(100, len(gaps) * 10))
    score = round(availability_score * 0.5 + freshness_score * 0.3 + gap_score * 0.2)
    return {
        "status": "computed",
        "score": score,
        "components": {
            "evidence_availability": {
                "available_surfaces": available_surfaces,
                "total_surfaces": total_surfaces,
                "score": availability_score,
            },
            "evidence_freshness": {
                "revision_relation": relation,
                "score": freshness_score,
            },
            "unresolved_gaps": {
                "count": len(gaps),
                "downstream_deferred": sum(
                    item.get("status") == "deferred" for item in downstream
                ),
                "score": gap_score,
            },
        },
        "formula": "50% evidence availability + 30% revision freshness + 20% unresolved-gap score",
    }


def _load_metadata(
    path: str | Path,
    document: dict[str, Any],
    changed: list[ChangedFile],
    base: str,
    target: str,
    repo_key: str,
) -> dict[str, Any]:
    metadata_path = Path(path)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Step1Error(
            "metadata_malformed", str(exc), path=str(metadata_path)
        ) from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema_version") != "0.1"
        or metadata.get("analysis_kind") != "pr_impact_metadata"
    ):
        raise Step1Error(
            "metadata_malformed",
            "metadata artifact has an unsupported shape",
            path=str(metadata_path),
        )
    pr = metadata.get("pull_request")
    fixture_pr = document.get("pull_request", {})
    if not isinstance(pr, dict):
        raise Step1Error(
            "metadata_malformed", "metadata artifact has no pull_request object"
        )
    if metadata.get("repo_key") != repo_key:
        raise Step1Error(
            "metadata_repository_mismatch",
            "metadata repo_key does not match the analysis",
        )
    if metadata.get("repository") != fixture_pr.get("repository"):
        raise Step1Error(
            "metadata_repository_mismatch",
            "metadata repository differs from fixture repository",
        )
    if pr.get("base_revision") != base or pr.get("target_revision") != target:
        raise Step1Error(
            "metadata_revision_mismatch",
            "metadata revisions differ from fixture revisions",
        )
    files = metadata.get("changed_files")
    if not isinstance(files, list) or not files:
        raise Step1Error(
            "metadata_changed_path_mismatch",
            "metadata changed_files must be a non-empty list",
        )
    declared: set[tuple[str, str]] = set()
    for item in files:
        if not isinstance(item, dict):
            raise Step1Error(
                "metadata_changed_path_mismatch",
                "metadata changed_files entries must be objects",
            )
        filename, status = item.get("filename"), item.get("status")
        if (
            not isinstance(filename, str)
            or not filename
            or not isinstance(status, str)
            or not status
        ):
            raise Step1Error(
                "metadata_changed_path_mismatch",
                "metadata changed_files entries require filename and status",
            )
        declared.add((filename, status))
    actual = {(item.path, item.status) for item in changed}
    if len(declared) != len(files) or declared != actual:
        raise Step1Error(
            "metadata_changed_path_mismatch",
            "metadata changed files differ from the exact Git diff",
        )
    return {
        "status": "available",
        "artifact_path": str(metadata_path),
        "repository": metadata.get("repository"),
        "repo_key": metadata.get("repo_key"),
        "number": pr.get("number"),
        "url": pr.get("url"),
        "base_revision": pr.get("base_revision"),
        "target_revision": pr.get("target_revision"),
        "provider": metadata.get("provenance", {}).get("provider")
        if isinstance(metadata.get("provenance"), dict)
        else None,
        "record_counts": {
            key: len(metadata.get(key, []))
            for key in (
                "changed_files",
                "reviews",
                "inline_comments",
                "issue_comments",
                "check_runs",
            )
            if isinstance(metadata.get(key, []), list)
        },
    }


def analyze_fixture(
    fixture: str | Path,
    manifest: str | Path,
    active_db: str | Path,
    repo_key: str,
    metadata: str | Path | None = None,
) -> dict[str, Any]:
    document = _fixture(Path(fixture))
    pr = document["pull_request"]
    try:
        repo = resolve_manifest_repo_root(manifest, repo_key)
    except ValueError as exc:
        code, _, message = str(exc).partition(": ")
        raise Step1Error(code or "manifest_invalid", message or str(exc)) from exc
    base, target = (
        _revision(repo, pr.get("base_revision"), "base_revision"),
        _revision(repo, pr.get("target_revision"), "target_revision"),
    )
    changed = _changed_paths(repo, base, target)
    if not changed:
        raise Step1Error("empty_diff", "Step 1 requires a non-empty Git diff")
    declared_rows = document.get("changed_files")
    if not isinstance(declared_rows, list) or not declared_rows:
        raise Step1Error(
            "changed_path_mismatch", "fixture changed_files must be a non-empty list"
        )
    declared = {
        (str(x.get("path")), str(x.get("status")))
        for x in declared_rows
        if isinstance(x, dict)
    }
    actual = {(x.path, x.status) for x in changed}
    if len(declared) != len(declared_rows) or declared != actual:
        raise Step1Error(
            "changed_path_mismatch",
            "fixture changed_files do not match the exact Git diff",
            expected=sorted(actual),
            declared=sorted(declared),
        )
    for change in changed:
        if not _safe_path(change.path):
            raise Step1Error(
                "malformed_git_diff", f"unsafe changed path: {change.path}"
            )
    conn, repo_id, preflight = _open_catalog(Path(active_db), target, repo_key, repo)
    try:

        def direct_surface(
            name: str, rows: list[dict[str, Any]], supported: bool = True
        ) -> dict[str, Any]:
            return _surface(
                name,
                rows,
                supported,
                catalog_revision=str(preflight["catalog_revision"]),
            )

        paths = [x.path for x in changed]
        marks = ",".join("?" for _ in paths)
        files = {
            str(r["path"]): r
            for r in conn.execute(
                f"SELECT * FROM files WHERE repo_id=? AND path IN ({marks}) ORDER BY path",
                (repo_id, *paths),
            ).fetchall()
        }
        file_ids = [int(r["id"]) for r in files.values()]
        ids = ",".join("?" for _ in file_ids) or "NULL"
        entity_file_ids = [
            int(row[0])
            for row in conn.execute(
                f"SELECT id FROM files WHERE repo_id=? AND path IN ({ids}) AND lower(path) LIKE '%.ent'",
                (repo_id, *paths),
            ).fetchall()
        ]
        entity_ids = ",".join("?" for _ in entity_file_ids) or "NULL"
        workflow_entity_args = (repo_id, *file_ids, repo_id, *entity_file_ids)
        workflow_entity_query = f"""
            SELECT w.* FROM workflow_facts w
            WHERE w.repo_id=? AND (
                w.source_file_id IN ({ids})
                OR w.entity_occurrence_id IN (
                    SELECT id FROM entity_occurrences WHERE repo_id=? AND source_file_id IN ({entity_ids})
                )
            ) ORDER BY w.id
        """
        security_operations = _rows(
            conn,
            f"SELECT * FROM security_operations WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
            (repo_id, *file_ids),
            "source_path",
            target,
        )
        operation_ids = [fact["catalog_record_id"] for fact in security_operations]
        operation_marks = ",".join("?" for _ in operation_ids) or "NULL"
        security_policies = _rows(
            conn,
            f"SELECT * FROM security_policies WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
            (repo_id, *file_ids),
            "source_path",
            target,
        )
        policy_ids = [fact["catalog_record_id"] for fact in security_policies]
        policy_marks = ",".join("?" for _ in policy_ids) or "NULL"
        security_menus = _rows(
            conn,
            f"SELECT * FROM security_menus WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
            (repo_id, *file_ids),
            "source_path",
            target,
        )
        menu_ids = [fact["catalog_record_id"] for fact in security_menus]
        menu_marks = ",".join("?" for _ in menu_ids) or "NULL"
        security_operation_allowops = _rows(
            conn,
            f"SELECT * FROM security_operation_allowops WHERE repo_id=? AND (source_file_id IN ({ids}) OR operation_id IN ({operation_marks}) OR allowed_operation_id IN ({operation_marks})) ORDER BY id",
            (repo_id, *file_ids, *operation_ids, *operation_ids),
            "source_path",
            target,
        )
        security_policy_values = _rows(
            conn,
            f"SELECT * FROM security_policy_values WHERE repo_id=? AND (source_file_id IN ({ids}) OR policy_id IN ({policy_marks})) ORDER BY id",
            (repo_id, *file_ids, *policy_ids),
            "source_path",
            target,
        )
        policy_value_ids = [
            fact["catalog_record_id"] for fact in security_policy_values
        ]
        policy_value_marks = ",".join("?" for _ in policy_value_ids) or "NULL"
        security_policy_eops = _rows(
            conn,
            f"SELECT * FROM security_policy_eops WHERE repo_id=? AND (source_file_id IN ({ids}) OR policy_value_id IN ({policy_value_marks}) OR operation_id IN ({operation_marks})) ORDER BY id",
            (repo_id, *file_ids, *policy_value_ids, *operation_ids),
            "source_path",
            target,
        )
        security_menu_items = _rows(
            conn,
            f"SELECT * FROM security_menu_items WHERE repo_id=? AND (source_file_id IN ({ids}) OR menu_id IN ({menu_marks})) ORDER BY id",
            (repo_id, *file_ids, *menu_ids),
            "source_path",
            target,
        )
        menu_item_ids = [fact["catalog_record_id"] for fact in security_menu_items]
        menu_item_marks = ",".join("?" for _ in menu_item_ids) or "NULL"
        security_menu_op_links = _rows(
            conn,
            f"SELECT * FROM security_menu_op_links WHERE repo_id=? AND (source_file_id IN ({ids}) OR menu_item_id IN ({menu_item_marks}) OR operation_id IN ({operation_marks})) ORDER BY id",
            (repo_id, *file_ids, *menu_item_ids, *operation_ids),
            "source_path",
            target,
        )
        security_direct = {
            "security_operations": security_operations,
            "security_operation_allowops": security_operation_allowops,
            "security_policies": security_policies,
            "security_policy_values": security_policy_values,
            "security_policy_eops": security_policy_eops,
            "security_menus": security_menus,
            "security_menu_items": security_menu_items,
            "security_menu_op_links": security_menu_op_links,
        }
        permissions = [fact for rows in security_direct.values() for fact in rows]
        entity_metadata = (
            _rows(
                conn,
                f"SELECT * FROM entity_section_facts WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                (repo_id, *file_ids),
                "source_path",
                target,
            )
            + _rows(
                conn,
                f"SELECT * FROM entity_field_facts WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                (repo_id, *file_ids),
                "source_path",
                target,
            )
            + _rows(
                conn,
                f"SELECT * FROM entity_schema_mappings WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                (repo_id, *file_ids),
                "source_path",
                target,
            )
        )
        database_consumers = (
            _rows(
                conn,
                f"SELECT * FROM dbschema_tables WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                (repo_id, *file_ids),
                "source_path",
                target,
            )
            + _rows(
                conn,
                f"SELECT * FROM dbschema_fields WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                (repo_id, *file_ids),
                "source_path",
                target,
            )
            + _rows(
                conn,
                f"SELECT * FROM entity_db_table_links WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                (repo_id, *file_ids),
                "source_path",
                target,
            )
            + _rows(
                conn,
                f"SELECT * FROM entity_db_field_links WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                (repo_id, *file_ids),
                "source_path",
                target,
            )
        )
        direct = [
            direct_surface(
                "files", [_evidence(r, "path", target) for r in files.values()]
            ),
            direct_surface(
                "symbols",
                _rows(
                    conn,
                    f"SELECT s.*, f.path AS source_path, f.source_commit_sha AS source_commit_sha FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.repo_id=? AND s.file_id IN ({ids}) ORDER BY s.file_id,s.start_line,s.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "outgoing_relationships",
                _rows(
                    conn,
                    f"SELECT r.*, f.source_commit_sha AS source_commit_sha FROM relationships r JOIN files f ON f.id=r.file_id WHERE r.repo_id=? AND r.file_id IN ({ids}) ORDER BY r.id",
                    (repo_id, *file_ids),
                    "file_path",
                    target,
                ),
            ),
            direct_surface(
                "incoming_relationships",
                _rows(
                    conn,
                    f"SELECT r.*, f.source_commit_sha AS source_commit_sha FROM relationships r JOIN files f ON f.id=r.file_id JOIN symbols s ON s.id=r.target_symbol_id WHERE r.repo_id=? AND s.file_id IN ({ids}) ORDER BY r.id",
                    (repo_id, *file_ids),
                    "file_path",
                    target,
                ),
            ),
            direct_surface(
                "entity_occurrences",
                _rows(
                    conn,
                    f"SELECT e.*, f.path AS source_path FROM entity_occurrences e JOIN files f ON f.id=e.source_file_id WHERE e.repo_id=? AND e.source_file_id IN ({ids}) ORDER BY e.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                )
                if any(x.endswith(".ent") for x in paths)
                else [],
                any(x.endswith(".ent") for x in paths),
            ),
            direct_surface(
                "openapi_documents",
                _rows(
                    conn,
                    f"SELECT * FROM openapi_documents WHERE repo_id=? AND file_id IN ({ids}) ORDER BY id",
                    (repo_id, *file_ids),
                    "path",
                    target,
                ),
            ),
            direct_surface(
                "openapi_entity_links",
                _rows(
                    conn,
                    f"SELECT l.*, d.path AS source_path FROM openapi_entity_links l JOIN openapi_documents d ON d.id=l.document_id JOIN entity_occurrences eo ON eo.id=l.entity_occurrence_id WHERE l.repo_id=? AND (d.file_id IN ({ids}) OR eo.source_file_id IN ({ids})) ORDER BY l.id",
                    (repo_id, *file_ids, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "rest_endpoints",
                _rows(
                    conn,
                    f"SELECT e.*, d.path AS source_path FROM rest_endpoints e JOIN openapi_documents d ON d.id=e.document_id WHERE e.repo_id=? AND d.file_id IN ({ids}) ORDER BY e.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "actionui",
                _rows(
                    conn,
                    f"SELECT * FROM ui_surfaces WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "actionui_artifacts",
                _rows(
                    conn,
                    f"SELECT * FROM ui_artifacts WHERE repo_id=? AND file_id IN ({ids}) ORDER BY id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "actionui_fields",
                _rows(
                    conn,
                    f"SELECT f.*, a.source_path AS source_path, a.source_commit_sha AS source_commit_sha FROM ui_fields f JOIN ui_artifacts a ON a.id=f.artifact_id WHERE f.repo_id=? AND a.file_id IN ({ids}) ORDER BY f.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "actionui_events",
                _rows(
                    conn,
                    f"SELECT e.*, a.source_path AS source_path, a.source_commit_sha AS source_commit_sha FROM ui_events e JOIN ui_artifacts a ON a.id=e.artifact_id WHERE e.repo_id=? AND a.file_id IN ({ids}) ORDER BY e.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "actionui_includes",
                _rows(
                    conn,
                    f"SELECT i.*, a.source_path AS source_path, a.source_commit_sha AS source_commit_sha FROM ui_includes i JOIN ui_artifacts a ON a.id=i.artifact_id WHERE i.repo_id=? AND a.file_id IN ({ids}) ORDER BY i.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "nextgen",
                _rows(
                    conn,
                    f"SELECT * FROM nextgen_families WHERE repo_id=? AND source_file_id IN ({ids}) ORDER BY id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "nextgen_artifacts",
                _rows(
                    conn,
                    f"SELECT * FROM nextgen_artifacts WHERE repo_id=? AND file_id IN ({ids}) ORDER BY id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface(
                "source_diagnostics",
                _rows(
                    conn,
                    f"SELECT d.*, f.path AS source_path FROM symbol_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                )
                + _rows(
                    conn,
                    f"SELECT d.*, f.path AS source_path FROM entity_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                )
                + _rows(
                    conn,
                    f"SELECT d.*, f.path AS source_path FROM openapi_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                )
                + _rows(
                    conn,
                    f"SELECT d.*, f.path AS source_path FROM ui_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                )
                + _rows(
                    conn,
                    f"SELECT d.*, f.path AS source_path FROM nextgen_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                )
                + _rows(
                    conn,
                    f"SELECT d.*, f.path AS source_path FROM workflow_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                )
                + _rows(
                    conn,
                    f"SELECT d.*, f.path AS source_path FROM security_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                )
                + _rows(
                    conn,
                    f"SELECT d.*, f.path AS source_path FROM repo_v1_database_diagnostics d JOIN files f ON f.id=d.file_id WHERE d.repo_id=? AND d.file_id IN ({ids}) ORDER BY d.id",
                    (repo_id, *file_ids),
                    "source_path",
                    target,
                ),
            ),
            direct_surface("database_consumers", database_consumers),
            direct_surface("entity_metadata", entity_metadata),
            direct_surface("permissions", permissions),
            direct_surface(
                "workflows",
                _rows(
                    conn,
                    workflow_entity_query,
                    workflow_entity_args,
                    "source_path",
                    target,
                ),
            ),
            _fixture_surface(document, "tests", target),
        ]
        gaps = [
            f"{x['surface']}: {x['status']}"
            for x in direct
            if x["status"] not in {"available", "unavailable", "deferred"}
        ]
        metadata_summary = (
            {"status": "not_provided"}
            if metadata is None
            else _load_metadata(metadata, document, changed, base, target, repo_key)
        )
        gaps.extend(
            f"{x['surface']}: {x['status']}"
            for x in direct
            if x["status"] == "deferred"
        )
        downstream = _downstream_repositories(document, Path(manifest))
        gaps.extend(
            f"downstream_repositories:{item['repository']}: {item['status']}"
            for item in downstream
            if item["status"] != "available"
        )
        ranking = rank_direct_traces(direct, [x.__dict__ for x in changed])
        confidence = _confidence(direct, downstream, preflight, gaps)
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "analysis_kind": "pr_impact_step_1",
            "status": "partial" if gaps else "complete",
            "input": {
                "fixture": str(Path(fixture)),
                "manifest": str(Path(manifest)),
                "repo_root": str(repo),
                "active_db": str(Path(active_db)),
                "repo_key": repo_key,
                "base_revision": base,
                "target_revision": target,
            },
            "preflight": preflight,
            "changed_files": [x.__dict__ for x in changed],
            "direct_traces": direct,
            "pr_metadata": metadata_summary,
            "downstream_repositories": downstream,
            "impact_ranking": ranking,
            "gaps": sorted(set(gaps)),
            "warnings": [x["warning"] for x in direct if "warning" in x],
            "confidence": confidence,
            "provenance": {
                "source": "repo-v1 active SQLite and exact Git diff",
                "read_only": True,
                "contract": "Git diff validation only; no catalog delta processing.",
            },
        }
    finally:
        conn.close()


def blocked_report(error: Step1Error) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": "pr_impact_step_1",
        "status": "blocked",
        "error": {"code": error.code, "message": error.message, **error.extra},
        "input": {},
        "preflight": {},
        "changed_files": [],
        "direct_traces": [],
        "pr_metadata": {"status": "not_provided"},
        "downstream_repositories": [],
        "impact_ranking": [],
        "gaps": [],
        "warnings": [],
        "confidence": {
            "status": "not_computed",
            "score": None,
            "reason": "analysis blocked before direct evidence collection",
        },
        "provenance": {"read_only": True},
    }


def _review_text(value: Any, fallback: str = "Not available") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _review_cell(value: Any, fallback: str = "Not available") -> str:
    return _review_text(value, fallback).replace("|", "\\|").replace("\n", " ")


def _review_items(report: Mapping[str, Any], key: str) -> list[Any]:
    value = report.get(key)
    return value if isinstance(value, list) else []


def _review_provenance(fact: Mapping[str, Any]) -> str:
    fields = (
        ("catalog_record_id", fact.get("catalog_record_id")),
        ("fact_key", fact.get("fact_key")),
        ("source_path", fact.get("source_path")),
        ("target_revision", fact.get("target_revision")),
        ("catalog_source_revision", fact.get("catalog_source_revision")),
        ("source_location", fact.get("source_location")),
        ("extractor", fact.get("extractor")),
        ("evidence", fact.get("evidence")),
        ("resolution_class", fact.get("resolution_class")),
        ("resolution_reason", fact.get("resolution_reason")),
        ("resolution_status", fact.get("resolution_status")),
        ("entity_link_status", fact.get("entity_link_status")),
    )
    return (
        "; ".join(
            f"{key}={_review_cell(value, 'Not available')}"
            for key, value in fields
            if value is not None
        )
        or "Not available"
    )


def _review_status_icon(status: Any) -> str:
    return "✓" if status == "available" else "⚠"


def _review_changed_file_key(item: Any) -> tuple[str, str, str]:
    if not isinstance(item, Mapping):
        return ("", "", "")
    return (
        str(item.get("path", "")),
        str(item.get("status", "")),
        str(item.get("old_path", "")),
    )


def _review_trace_key(trace: Any) -> tuple[str, str]:
    if not isinstance(trace, Mapping):
        return ("", "")
    return (str(trace.get("surface", "")), str(trace.get("status", "")))


def _review_fact_key(fact: Any) -> tuple[str, str, str]:
    if not isinstance(fact, Mapping):
        return ("", "", "")
    return (
        str(fact.get("source_path", "")),
        str(fact.get("catalog_record_id", fact.get("fact_key", ""))),
        _review_text(fact.get("evidence"), ""),
    )


def _review_counts(report: Mapping[str, Any]) -> tuple[str, str, str]:
    changed = report.get("changed_files")
    if report.get("status") == "blocked":
        return ("Not computed", "Not computed", "Not computed")
    if not isinstance(changed, list):
        return ("Not available", "Not available", "Not available")
    statuses = [item.get("status") for item in changed if isinstance(item, Mapping)]
    if len(statuses) != len(changed) or any(
        not isinstance(status, str) for status in statuses
    ):
        return ("Not available", "Not available", "Not available")
    return (
        str(len(changed)),
        str(sum(status == "added" for status in statuses)),
        str(sum(status == "deleted" for status in statuses)),
    )


def _review_recommendation(report: Mapping[str, Any]) -> str:
    if report.get("status") == "blocked":
        return "Request Changes ⚠"
    if _review_items(report, "gaps") or _review_items(report, "warnings"):
        return "Comment 💬"
    if report.get("status") == "complete":
        return "Approve ✓"
    return "Comment 💬"


def render_review_markdown(report: Mapping[str, Any]) -> str:
    """Render an existing Step 1 report using the canonical review template."""
    if not isinstance(report, Mapping):
        report = {}
    input_data = report.get("input") if isinstance(report.get("input"), Mapping) else {}
    provenance = (
        report.get("provenance")
        if isinstance(report.get("provenance"), Mapping)
        else {}
    )
    analysis_kind = _review_text(report.get("analysis_kind"), "Not available")
    repo_key = _review_text(input_data.get("repo_key"), "Unknown")
    base_revision = _review_text(input_data.get("base_revision"), "Unknown")
    target_revision = _review_text(input_data.get("target_revision"), "Unknown")
    scope = (
        f"Read-only {analysis_kind} for {repo_key} from {base_revision} to {target_revision}."
        if input_data
        else "Not available"
    )
    changed_count, additions, deletions = _review_counts(report)
    changed_files = sorted(
        _review_items(report, "changed_files"), key=_review_changed_file_key
    )
    traces = sorted(_review_items(report, "direct_traces"), key=_review_trace_key)
    downstream = sorted(
        _review_items(report, "downstream_repositories"),
        key=lambda item: (
            str(item.get("repository", "")) if isinstance(item, Mapping) else ""
        ),
    )
    reviewed_rows: list[str] = []
    for item in changed_files:
        if not isinstance(item, Mapping):
            reviewed_rows.append("| Not available | Git change | ⚠ | Not available |")
            continue
        old_path = item.get("old_path")
        note = (
            f"status={_review_cell(item.get('status'), 'Not available')}; "
            f"target_revision={_review_cell(target_revision, 'Unknown')}"
        )
        if old_path is not None:
            note += f"; old_path={_review_cell(old_path)}"
        reviewed_rows.append(
            f"| `{_review_cell(item.get('path'), 'Not available')}` | Git change | ✓ | {note} |"
        )
    for trace in traces:
        if not isinstance(trace, Mapping):
            reviewed_rows.append("| Not available | Direct trace | ⚠ | Not available |")
            continue
        surface = _review_text(trace.get("surface"), "Not available")
        trace_status = _review_text(trace.get("status"), "Not available")
        facts = sorted(_review_items(trace, "facts"), key=_review_fact_key)
        if facts:
            for fact in facts:
                if isinstance(fact, Mapping):
                    source_path = _review_cell(fact.get("source_path"), "Not available")
                    note = f"status={_review_cell(trace_status)}; {_review_provenance(fact)}"
                else:
                    source_path, note = "Not available", "Not available"
                reviewed_rows.append(
                    f"| `{source_path}` | {surface} | {_review_status_icon(trace.get('status'))} | {note} |"
                )
        else:
            note = f"status={_review_cell(trace_status)}"
            if trace.get("warning") is not None:
                note += f"; warning={_review_cell(trace.get('warning'))}"
            reviewed_rows.append(
                f"| Not available | {surface} | {_review_status_icon(trace.get('status'))} | {note} |"
            )
    for item in downstream:
        if not isinstance(item, Mapping):
            reviewed_rows.append(
                "| Not available | Downstream repository | ⚠ | Not available |"
            )
            continue
        repository = _review_cell(item.get("repository"), "Not available")
        status = item.get("status")
        relationships = item.get("relationships")
        relation_types = (
            ",".join(
                sorted(
                    str(relation.get("type"))
                    for relation in relationships
                    if isinstance(relation, Mapping) and relation.get("type")
                )
            )
            if isinstance(relationships, list)
            else "Not available"
        )
        note = f"status={_review_cell(status)}; typed_relationships={_review_cell(relation_types, 'none')}"
        if item.get("reason") is not None:
            note += f"; reason={_review_cell(item.get('reason'))}"
        reviewed_rows.append(
            f"| `{repository}` | Downstream repository | {_review_status_icon(status)} | {note} |"
        )
    if not reviewed_rows:
        reviewed_rows.append("| Not available | Not available | ⚠ | Not available |")

    gaps = sorted({_review_text(item) for item in _review_items(report, "gaps")})
    warnings = sorted(
        {_review_text(item) for item in _review_items(report, "warnings")}
    )
    error = report.get("error") if isinstance(report.get("error"), Mapping) else None
    critical = []
    if error is not None:
        critical.append(
            f"- **[Step 1:{_review_cell(error.get('code'), 'Not available')}]** "
            f"{_review_cell(error.get('message'), 'Not available')}"
        )
    medium = [f"- **[Step 1]** Gap: {_review_cell(item)}" for item in gaps]
    medium.extend(f"- **[Step 1]** Warning: {_review_cell(item)}" for item in warnings)
    strengths = []
    if report.get("changed_files") is not None and report.get("status") != "blocked":
        strengths.append(
            "- **[Step 1]** Exact Git changed-file evidence is present in the report."
        )
    if provenance.get("read_only") is True:
        strengths.append(
            "- **[Step 1]** The report identifies the analysis as read-only."
        )
    if provenance:
        strengths.append(
            f"- **[Step 1]** Provenance: source={_review_cell(provenance.get('source'))}; "
            f"contract={_review_cell(provenance.get('contract'))}."
        )
    for section in (critical, medium, strengths):
        if not section:
            section.append("- Not available")
    assumptions = [f"- Gap: {_review_cell(item)}" for item in gaps]
    assumptions.extend(f"- Warning: {_review_cell(item)}" for item in warnings)
    if provenance:
        assumptions.append(
            f"- Provenance: source={_review_cell(provenance.get('source'))}; "
            f"read_only={_review_cell(provenance.get('read_only'), 'Unknown')}; "
            f"contract={_review_cell(provenance.get('contract'))}."
        )
    if not assumptions:
        assumptions.append("- Not available")

    confidence = report.get("confidence")
    if isinstance(confidence, Mapping) and confidence.get("status") == "computed":
        components = confidence.get("components")
        if isinstance(components, Mapping):
            availability = components.get("evidence_availability", {})
            freshness = components.get("evidence_freshness", {})
            gaps_component = components.get("unresolved_gaps", {})
            confidence_text = (
                f"{_review_cell(confidence.get('score'))}/100 "
                f"(availability={_review_cell(availability.get('score'))}; "
                f"freshness={_review_cell(freshness.get('score'))}; "
                f"unresolved_gaps={_review_cell(gaps_component.get('count'))})"
            )
        else:
            confidence_text = f"{_review_cell(confidence.get('score'))}/100"
    else:
        confidence_text = "Not computed"

    return "\n".join(
        [
            "## 🔍 Review Summary",
            "",
            "**Type:** Not available",
            f"**Scope:** {scope}",
            "**Risk Level:** Not computed",
            "",
            "---",
            "",
            "## 📊 Changes at a Glance",
            "",
            f"- **Files:** {changed_count} changed, {additions} additions, {deletions} deletions",
            "- **Commits:** Not available (avg. message quality: Not computed)",
            "- **Coverage:** API changes [Not computed] | DB migrations [Not computed] | UI [Not computed]",
            f"- **Downstream:** {len(downstream)} repositories represented; typed relationship evidence is not inferred.",
            "",
            "---",
            "",
            "## ✅ Reviewed",
            "",
            "| File | Type | Status | Notes |",
            "|------|------|--------|-------|",
            *reviewed_rows,
            "",
            "---",
            "",
            "## 🎯 Findings",
            "",
            "### 🔴 Critical",
            *critical,
            "",
            "### 🟡 Medium Priority",
            *medium,
            "",
            "### 🟢 Nice-to-Have",
            "- Not available",
            "",
            "### ✅ Strengths",
            *strengths,
            "",
            "---",
            "",
            "## 📋 Checklist",
            "",
            "- [ ] All changed files reviewed",
            "- [ ] No dead code or unused functions",
            "- [ ] Consistency with existing patterns",
            "- [ ] Documentation/comments adequate",
            "- [ ] Tests cover new logic (if applicable)",
            "- [ ] No obvious performance issues",
            "- [ ] Follows team/language conventions",
            "",
            "---",
            "",
            "## 🎲 Confidence & Recommendation",
            "",
            f"**Confidence:** {confidence_text}",
            f"**Recommendation:** {_review_recommendation(report)}",
            "",
            "**Gaps/Assumptions:**",
            *assumptions,
            "",
            "**Next Reviewer:** Not available",
            "",
        ]
    )
