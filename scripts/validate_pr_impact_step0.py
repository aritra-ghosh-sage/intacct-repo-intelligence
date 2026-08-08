#!/usr/bin/env python3
"""Validate a PR-impact Step 0 fixture against committed local Git objects."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

# Make the documented direct ``python scripts/...`` invocation work from the
# repository root without requiring callers to set PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog.delta import ChangeType, DeltaUnavailable, collect_changed_paths
from catalog.pr_impact_manifest import resolve_manifest_repo_root
from catalog.source_snapshot import SourceSnapshotError, resolve_commit_sha


ValidationReport = dict[str, Any]
_HEX = re.compile(r"^[0-9a-f]+$")
_CHANGE_STATUSES = {"added", "modified", "deleted"}
_WARNING_STATUSES = {
    "unresolved": "unresolved_finding",
    "unavailable": "unavailable_evidence",
    "coverage_unknown": "coverage_unknown",
    "not_evidenced": "not_evidenced",
    "review_required": "review_required",
}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )


def _error(report: ValidationReport, code: str, message: str, path: str) -> None:
    report["errors"].append({"code": code, "message": message, "path": path})


def _warning(report: ValidationReport, code: str, message: str, path: str) -> None:
    report["warnings"].append({"code": code, "message": message, "path": path})


def _check(report: ValidationReport, name: str, status: str) -> None:
    report["checks"].append({"name": name, "status": status})


def _safe_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    if value.startswith("/") or "\\" in value:
        return False
    return all(part not in {"", ".", ".."} for part in PurePosixPath(value).parts)


def _object_id_length(repo: Path) -> int:
    result = _git(repo, "rev-parse", "--show-object-format")
    fmt = result.stdout.decode(errors="replace").strip()
    return {"sha1": 40, "sha256": 64}.get(fmt, 0)


def _full_revision(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and bool(_HEX.fullmatch(value))


def _load_fixture(path: Path, report: ValidationReport) -> dict[str, Any] | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        _error(report, "malformed_yaml", str(exc), "fixture")
        return None
    if not isinstance(loaded, dict):
        _error(report, "invalid_yaml_shape", "Fixture root must be a mapping", "fixture")
        return None
    return loaded


def _required_schema(document: dict[str, Any], report: ValidationReport) -> bool:
    valid = True
    if document.get("schema_version") != "0.1":
        _error(report, "unsupported_schema_version", "schema_version must be '0.1'", "schema_version")
        valid = False
    if document.get("analysis_kind") != "pr_impact_step_0":
        _error(report, "invalid_analysis_kind", "analysis_kind must be pr_impact_step_0", "analysis_kind")
        valid = False
    required_sections = (
        "pull_request",
        "changed_files",
        "changed_items",
        "affected_surfaces",
        "related_repositories",
        "test_obligations",
        "review_evidence",
        "assessment",
        "provenance",
    )
    for section in required_sections:
        if section not in document:
            _error(report, "missing_required_section", f"Missing required section: {section}", section)
            valid = False

    pull_request = document.get("pull_request")
    if not isinstance(pull_request, dict):
        _error(report, "missing_required_section", "pull_request must be a mapping", "pull_request")
        valid = False
    else:
        for key in ("repository", "number", "url", "base_revision", "target_revision"):
            if key not in pull_request:
                _error(report, "missing_required_field", f"Missing pull_request.{key}", f"pull_request.{key}")
                valid = False
        if pull_request.get("repository") != "intacct/ia-app":
            _error(report, "invalid_repository", "repository must be intacct/ia-app", "pull_request.repository")
            valid = False
        if not isinstance(pull_request.get("number"), int) or isinstance(pull_request.get("number"), bool):
            _error(report, "invalid_field_type", "pull_request.number must be an integer", "pull_request.number")
            valid = False

    list_sections = ("changed_files", "changed_items", "related_repositories")
    for section in list_sections:
        value = document.get(section)
        if not isinstance(value, list) or not value:
            _error(report, "invalid_required_section", f"{section} must be a non-empty list", section)
            valid = False

    surfaces = document.get("affected_surfaces")
    if not isinstance(surfaces, dict):
        _error(report, "invalid_required_section", "affected_surfaces must be a mapping", "affected_surfaces")
        valid = False
    else:
        for key in ("entities", "api", "ui", "database", "permissions"):
            if key not in surfaces:
                _error(report, "missing_required_field", f"Missing affected_surfaces.{key}", f"affected_surfaces.{key}")
                valid = False

    obligations = document.get("test_obligations")
    if not isinstance(obligations, dict):
        _error(report, "invalid_required_section", "test_obligations must be a mapping", "test_obligations")
        valid = False
    elif "unresolved" not in obligations or not any(
        key in obligations for key in ("existing_or_expected", "database", "runtime", "api")
    ):
        _error(report, "missing_required_field", "test_obligations needs existing/recommended content and unresolved items", "test_obligations")
        valid = False

    reviews = document.get("review_evidence")
    if not isinstance(reviews, dict):
        _error(report, "invalid_required_section", "review_evidence must be a mapping", "review_evidence")
        valid = False
    else:
        for key in ("automated", "human"):
            if not isinstance(reviews.get(key), list):
                _error(report, "missing_required_field", f"review_evidence.{key} must be a list", f"review_evidence.{key}")
                valid = False

    assessment = document.get("assessment")
    if not isinstance(assessment, dict):
        _error(report, "invalid_required_section", "assessment must be a mapping", "assessment")
        valid = False
    else:
        for key in ("confidence", "risk_level", "blockers", "unresolved"):
            if key not in assessment:
                _error(report, "missing_required_field", f"Missing assessment.{key}", f"assessment.{key}")
                valid = False

    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        _error(report, "invalid_required_section", "provenance must be a mapping", "provenance")
        valid = False
    else:
        for key in ("source_snapshot", "review_snapshot_date", "generated_from"):
            if key not in provenance:
                _error(report, "missing_required_field", f"Missing provenance.{key}", f"provenance.{key}")
                valid = False
    return valid


def _validate_changed_files(document: dict[str, Any], report: ValidationReport) -> dict[str, str]:
    result: dict[str, str] = {}
    changed = document.get("changed_files")
    if not isinstance(changed, list):
        return result
    for index, entry in enumerate(changed):
        path = f"changed_files[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            _error(report, "invalid_changed_file", "Each changed file needs a path and status", path)
            continue
        name = entry["path"]
        status = entry.get("status")
        if not _safe_path(name):
            _error(report, "unsafe_path", f"Unsafe repository path: {name!r}", f"{path}.path")
        if status not in _CHANGE_STATUSES:
            _error(report, "invalid_status", f"Unsupported changed-file status: {status!r}", f"{path}.status")
        if name in result:
            _error(report, "duplicate_path", f"Duplicate changed path: {name}", f"{path}.path")
        else:
            result[name] = status if isinstance(status, str) else ""
    return result


def _blob_lines(repo: Path, revision: str, path: str) -> int | None:
    result = _git(repo, "show", f"{revision}:{path}")
    if result.returncode:
        return None
    text = result.stdout.decode("utf-8", errors="replace")
    return len(text.splitlines()) or 1


def _blob_exists(repo: Path, revision: str, path: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{revision}:{path}").returncode == 0


def _evidence_refs(value: object, location: str = "") -> list[tuple[str, int | None, str]]:
    refs: list[tuple[str, int | None, str]] = []
    if isinstance(value, dict):
        if isinstance(value.get("evidence"), (str, list, dict)):
            refs.extend(_evidence_refs(value["evidence"], f"{location}.evidence"))
        evidence_path = value.get("path", value.get("source"))
        if isinstance(evidence_path, str) and evidence_path.startswith("app/"):
            line = value.get("line")
            if "line" in value and (not isinstance(line, int) or isinstance(line, bool)):
                line = 0
            refs.append((evidence_path, line if isinstance(line, int) else None, f"{location}.path"))
        for key, child in value.items():
            if key != "evidence" and not (key == "path" and isinstance(child, str)):
                refs.extend(_evidence_refs(child, f"{location}.{key}" if location else key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            refs.extend(_evidence_refs(child, f"{location}[{index}]"))
    elif isinstance(value, str) and value.startswith("app/"):
        refs.append((value, None, location))
    return refs


def _validate_evidence(document: dict[str, Any], repo: Path, changed: dict[str, str], target: str, base: str, report: ValidationReport) -> None:
    refs = _evidence_refs(document)
    seen: set[tuple[str, int | None]] = set()
    for path, line, location in refs:
        if (path, line) in seen:
            continue
        seen.add((path, line))
        revision = base if changed.get(path) == "deleted" else target
        if not _safe_path(path) or not _blob_exists(repo, revision, path):
            _error(report, "missing_evidence_path", f"Evidence path is absent from {revision}: {path}", location)
            continue
        if line is not None:
            if line <= 0:
                _error(report, "invalid_evidence_line", "Evidence line must be positive", location)
            else:
                count = _blob_lines(repo, revision, path)
                if count is not None and line > count:
                    _error(report, "invalid_evidence_line", f"Evidence line {line} exceeds {count} lines", location)


def _validate_reviews(document: dict[str, Any], repo: Path, target: str, report: ValidationReport) -> None:
    reviews = document.get("review_evidence")
    if not isinstance(reviews, dict):
        _warning(report, "review_required", "Review evidence is unavailable", "review_evidence")
        return
    warning = False
    for group, entries in reviews.items():
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            location = f"review_evidence.{group}[{index}]"
            if entry.get("type") in {"pull_request_review", "inline_review_comments"}:
                if not entry.get("object_id") and not entry.get("url"):
                    _warning(report, "missing_external_reference", "Review entry has no external ID or URL", location)
                    warning = True
                reviewed = entry.get("reviewed_revision")
                revisions = entry.get("reviewed_revisions", [])
                values = ([reviewed] if reviewed else []) + (revisions if isinstance(revisions, list) else [])
                for revision in values:
                    try:
                        resolved = resolve_commit_sha(repo, revision)
                    except (SourceSnapshotError, TypeError):
                        _warning(report, "stale_review_evidence", "Review revision is unavailable locally", f"{location}.reviewed_revision")
                        warning = True
                    else:
                        if resolved != target:
                            _warning(report, "stale_review_evidence", "Review evidence was recorded for a different revision", f"{location}.reviewed_revision")
                            warning = True
    if warning:
        _check(report, "review_evidence", "warning")
    else:
        _check(report, "review_evidence", "pass")


def _status_warnings(value: object, location: str, report: ValidationReport) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "status" and isinstance(child, str) and child in _WARNING_STATUSES:
                _warning(report, _WARNING_STATUSES[child], f"Finding status is {child}", f"{location}.status")
            _status_warnings(child, f"{location}.{key}" if location else key, report)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _status_warnings(child, f"{location}[{index}]", report)


def validate_fixture(
    fixture_path: Path,
    manifest_path: Path,
    repo_key: str,
) -> ValidationReport:
    report: ValidationReport = {
        "schema_version": "0.1",
        "fixture": str(fixture_path),
        "repository": "intacct/ia-app",
        "manifest": str(manifest_path),
        "repo_key": repo_key,
        "status": "fail",
        "errors": [],
        "warnings": [],
        "checks": [],
    }
    try:
        repo_root = resolve_manifest_repo_root(manifest_path, repo_key)
    except ValueError as exc:
        code, _, message = str(exc).partition(": ")
        _error(report, code or "manifest_invalid", message or str(exc), "repo_key")
        _check(report, "manifest", "error")
        return report
    report["repo_root"] = str(repo_root)
    _check(report, "manifest", "pass")
    document = _load_fixture(fixture_path, report)
    if document is None:
        _check(report, "yaml_schema", "error")
        return report
    schema_ok = _required_schema(document, report)
    _check(report, "yaml_schema", "pass" if schema_ok else "error")
    changed = _validate_changed_files(document, report)
    pr = document.get("pull_request") if isinstance(document.get("pull_request"), dict) else {}
    length = _object_id_length(repo_root)
    base_value, target_value = pr.get("base_revision"), pr.get("target_revision")
    revisions_ok = length > 0 and _full_revision(base_value, length) and _full_revision(target_value, length)
    if not revisions_ok:
        _error(report, "invalid_git_revision", "base_revision and target_revision must be full Git object IDs", "pull_request")
    base = target = ""
    if revisions_ok:
        try:
            base = resolve_commit_sha(repo_root, base_value)
            target = resolve_commit_sha(repo_root, target_value)
        except SourceSnapshotError as exc:
            _error(report, "invalid_git_revision", str(exc), "pull_request")
        else:
            if base != base_value or target != target_value:
                _error(report, "invalid_git_revision", "Revision is not an unambiguous full commit ID", "pull_request")
    _check(report, "git_revisions", "pass" if not any(e["code"] == "invalid_git_revision" for e in report["errors"]) else "error")
    if base and target:
        try:
            actual = collect_changed_paths(repo_root, base, target)
        except DeltaUnavailable as exc:
            _error(report, "git_diff_error", str(exc), "changed_files")
            _check(report, "changed_files", "error")
        else:
            if any(change.change_type == ChangeType.RENAMED for change in actual):
                _error(report, "unsupported_change_type", "Renamed paths are not supported by Step 0", "changed_files")
            actual_map = {change.path: change.change_type.value for change in actual if change.change_type != ChangeType.RENAMED}
            if actual_map != changed:
                _error(report, "changed_files_mismatch", f"Fixture changed_files does not match committed diff: expected {actual_map!r}", "changed_files")
            _check(report, "changed_files", "pass" if not any(e["path"] == "changed_files" for e in report["errors"]) else "error")
            _validate_evidence(document, repo_root, changed, target, base, report)
            _check(report, "evidence_paths", "pass" if not any(e["code"].startswith("missing_evidence") or e["code"] == "invalid_evidence_line" for e in report["errors"]) else "error")
    _status_warnings(document, "", report)
    assessment = document.get("assessment")
    if isinstance(assessment, dict) and isinstance(assessment.get("unresolved"), list) and assessment["unresolved"]:
        _warning(report, "unresolved_finding", "Assessment contains unresolved findings", "assessment.unresolved")
    _validate_reviews(document, repo_root, target, report) if target else None
    report["status"] = "pass" if not report["errors"] else "fail"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=Path("config/workspace_repos.yaml")
    )
    parser.add_argument("--repo-key", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    report = validate_fixture(args.fixture, args.manifest, args.repo_key)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(f"{report['status'].upper()}: {report['fixture']}")
        for item in report["errors"] + report["warnings"]:
            print(f"{item['code']}: {item['message']} ({item['path']})")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
