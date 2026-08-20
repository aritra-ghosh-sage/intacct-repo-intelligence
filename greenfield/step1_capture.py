"""Capture and validate the repository-neutral greenfield PR evidence artifact."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from catalog.github_pr_metadata import GitHubPrMetadataError, fetch_pr_metadata

REPORT_SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_pr_impact_step_1"
SHA = re.compile(r"^[0-9a-f]{40}$")
FILE_STATUSES = {
    "added",
    "copied",
    "deleted",
    "modified",
    "renamed",
    "changed",
    "unchanged",
}
EVIDENCE_STATUSES = {"available", "empty", "unavailable", "not_requested"}


class CaptureError(ValueError):
    """The source PR evidence cannot safely become a Step 1 artifact."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_fingerprint(report: Mapping[str, Any]) -> str:
    """Hash stable evidence while excluding fetch time and the stored hash."""

    value = copy.deepcopy(dict(report))
    provenance = value.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("fetched_at", None)
        provenance.pop("evidence_sha256", None)
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def capture_failure_code(error: Exception) -> str:
    """Classify a capture failure for clearer blocked Step 1 diagnostics."""

    message = str(error).lower()
    if any(
        token in message
        for token in (
            "no github provider is available",
            "github providers failed",
            "gh executable is unavailable",
            "gh api failed",
            "github http api failed",
            "github_provider_unavailable",
        )
    ):
        return "provider_unavailable"
    if any(
        token in message
        for token in (
            "repo_not_found:",
            "manifest_invalid:",
            "repo_root_unavailable:",
            "origin remote does not match configured github identity",
            "origin remote does not match configured canonical git url",
            "repository not found in manifest",
        )
    ):
        return "manifest_identity_mismatch"
    return "capture_failed"


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaptureError(f"{label} must be a non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA.fullmatch(result):
        raise CaptureError(f"{label} must be a 40-character lowercase SHA")
    return result


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CaptureError(f"{label} must be an object")
    return value


def _records(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise CaptureError(f"{label} must be a list")
    if any(not isinstance(item, dict) for item in value):
        raise CaptureError(f"{label} entries must be objects")
    return [copy.deepcopy(item) for item in value]


def _changed_files(value: Any) -> list[dict[str, Any]]:
    rows = _records(value, "changed_files")
    if not rows:
        raise CaptureError("changed_files must be non-empty")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        path = _text(row.get("filename") or row.get("path"), "changed file path")
        status = _text(row.get("status"), f"changed file status for {path}").lower()
        if status not in FILE_STATUSES:
            raise CaptureError(f"unsupported changed file status: {status}")
        previous = row.get("previous_filename") or ""
        if not isinstance(previous, str):
            raise CaptureError(
                f"changed file previous_filename must be a string: {path}"
            )
        key = (path, status, previous)
        if key in seen:
            raise CaptureError(f"duplicate changed file: {path}")
        seen.add(key)
        normalized_row = {"path": path, "filename": path, "status": status}
        if previous:
            normalized_row["previous_filename"] = previous
        for field in ("additions", "deletions", "changes", "blob_url", "contents_url"):
            if field in row:
                normalized_row[field] = row[field]
        normalized.append(normalized_row)
    return sorted(
        normalized,
        key=lambda row: (
            row["path"],
            row["status"],
            row.get("previous_filename", ""),
        ),
    )


def _sha_bound_records(
    rows: list[dict[str, Any]], *, head_sha: str, label: str
) -> list[dict[str, Any]]:
    for row in rows:
        row_sha = row.get("head_sha")
        if row_sha is None:
            raise CaptureError(f"{label} record is missing head_sha")
        if _sha(row_sha, f"{label} head_sha") != head_sha:
            raise CaptureError(f"{label} record is bound to a different head_sha")
    return sorted(
        rows,
        key=lambda row: tuple(
            str(row.get(key, "")) for key in ("name", "id", "head_sha")
        ),
    )


def _workflow_jobs(
    rows: list[dict[str, Any]], workflow_runs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    run_ids = {row.get("id") for row in workflow_runs}
    for row in rows:
        if row.get("workflow_run_id") not in run_ids:
            raise CaptureError("workflow_jobs references a missing workflow run")
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("workflow_run_id", "")),
            str(row.get("id", "")),
            str(row.get("name", "")),
        ),
    )


def build_report(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Build a greenfield Step 1 report from normalized PR metadata."""

    source = _object(dict(metadata), "metadata")
    pull_request = _object(source.get("pull_request"), "metadata.pull_request")
    repository = _text(source.get("repository"), "metadata.repository")
    repo_key = _text(source.get("repo_key"), "metadata.repo_key")
    pr_number = pull_request.get("number")
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
        raise CaptureError("metadata.pull_request.number must be a positive integer")
    base_sha = _sha(pull_request.get("base_revision"), "base_revision")
    head_sha = _sha(pull_request.get("target_revision"), "target_revision")
    changed_files = _changed_files(source.get("changed_files"))
    workflow_runs = _sha_bound_records(
        _records(source.get("workflow_runs", []), "workflow_runs"),
        head_sha=head_sha,
        label="workflow run",
    )
    check_runs = _sha_bound_records(
        _records(source.get("check_runs", []), "check_runs"),
        head_sha=head_sha,
        label="check run",
    )
    workflow_jobs = _workflow_jobs(
        _records(source.get("workflow_jobs", []), "workflow_jobs"), workflow_runs
    )
    evidence_status = source.get("evidence_status", {})
    if not isinstance(evidence_status, dict):
        raise CaptureError("metadata.evidence_status must be an object")
    invalid_statuses = set(evidence_status.values()) - EVIDENCE_STATUSES
    if invalid_statuses:
        raise CaptureError(
            "invalid evidence status: " + ", ".join(sorted(invalid_statuses))
        )
    statuses = {
        "linked_issues": "empty"
        if not source.get("linked_issues", [])
        else "available",
        "workflow_runs": "empty" if not workflow_runs else "available",
        "workflow_jobs": "empty" if not workflow_jobs else "available",
        "check_runs": "empty" if not check_runs else "available",
    }
    statuses.update(evidence_status)
    optional_unavailable = any(
        statuses.get(key) == "unavailable"
        for key in ("linked_issues", "workflow_runs", "workflow_jobs", "check_runs")
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "partial" if optional_unavailable else "complete",
        "input": {
            "repository": repository,
            "repo_key": repo_key,
            "pr_number": pr_number,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "base_revision": base_sha,
            "target_revision": head_sha,
            "changed_paths": [row["path"] for row in changed_files],
        },
        "changed_files": changed_files,
        "pr_metadata": pull_request,
        "linked_issues": _records(source.get("linked_issues", []), "linked_issues"),
        "workflow_runs": workflow_runs,
        "workflow_jobs": workflow_jobs,
        "check_runs": check_runs,
        "evidence_status": statuses,
        "gaps": sorted(
            f"{key}:unavailable"
            for key, value in statuses.items()
            if value == "unavailable"
        ),
        "warnings": sorted(
            f"{key}:empty" for key, value in statuses.items() if value == "empty"
        ),
        "provenance": {
            "source_metadata_schema_version": source.get("schema_version"),
            "provider": source.get("provenance", {}).get("provider")
            if isinstance(source.get("provenance"), dict)
            else None,
            "endpoints": source.get("provenance", {}).get("endpoints", [])
            if isinstance(source.get("provenance"), dict)
            else [],
            "fetched_at": source.get("provenance", {}).get("fetched_at")
            if isinstance(source.get("provenance"), dict)
            else None,
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
        },
    }
    report["provenance"]["evidence_sha256"] = evidence_fingerprint(report)
    return report


def blocked_report(error: Exception, *, code: str = "capture_failed") -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "blocked",
        "error": {"code": code, "message": str(error)},
        "input": {},
        "changed_files": [],
        "pr_metadata": {},
        "linked_issues": [],
        "workflow_runs": [],
        "workflow_jobs": [],
        "check_runs": [],
        "evidence_status": {},
        "gaps": ["source_pr_evidence_unavailable"],
        "warnings": [],
        "provenance": {
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
        },
    }
    report["provenance"]["evidence_sha256"] = evidence_fingerprint(report)
    return report


def capture_pr(
    *, repo_key: str, manifest_path: str | Path, pr_number: int
) -> dict[str, Any]:
    try:
        metadata = fetch_pr_metadata(
            repo_key=repo_key, manifest_path=manifest_path, pr_number=pr_number
        )
    except GitHubPrMetadataError as exc:
        raise CaptureError(str(exc)) from exc
    return build_report(metadata)
