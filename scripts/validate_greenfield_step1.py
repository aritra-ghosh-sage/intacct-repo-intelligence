#!/usr/bin/env python3
"""Validate a materialized greenfield Step 1 report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.step1_capture import (
    ANALYSIS_KIND,
    EVIDENCE_STATUSES,
    FILE_STATUSES,
    REPORT_SCHEMA_VERSION,
    evidence_fingerprint,
)

SHA = re.compile(r"^[0-9a-f]{40}$")
STATUSES = {"complete", "partial", "blocked"}
TOP_LEVEL_KEYS = {
    "schema_version",
    "analysis_kind",
    "status",
    "error",
    "input",
    "changed_files",
    "pr_metadata",
    "linked_issues",
    "workflow_runs",
    "workflow_jobs",
    "check_runs",
    "evidence_status",
    "gaps",
    "warnings",
    "provenance",
}


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _records(value: Any, label: str, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    rows = [item for item in value if isinstance(item, dict)]
    if len(rows) != len(value):
        errors.append(f"{label} entries must be objects")
    return rows


def validate(report: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report must be an object"]
    unexpected = sorted(set(report) - TOP_LEVEL_KEYS)
    if unexpected:
        errors.append("unexpected top-level sections: " + ", ".join(unexpected))
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {REPORT_SCHEMA_VERSION}")
    if report.get("analysis_kind") != ANALYSIS_KIND:
        errors.append("invalid analysis_kind")
    status = report.get("status")
    if status not in STATUSES:
        errors.append("invalid status")

    input_data = report.get("input")
    if not isinstance(input_data, dict):
        errors.append("input must be an object")
        input_data = {}
    changed_files = _records(report.get("changed_files"), "changed_files", errors)
    if status != "blocked":
        for key in ("repository", "repo_key"):
            if not _text(input_data.get(key)):
                errors.append(f"input.{key} must be a non-empty string")
        pr_number = input_data.get("pr_number")
        if (
            isinstance(pr_number, bool)
            or not isinstance(pr_number, int)
            or pr_number <= 0
        ):
            errors.append("input.pr_number must be a positive integer")
        for key in ("base_sha", "head_sha", "base_revision", "target_revision"):
            if not _sha(input_data.get(key)):
                errors.append(f"input.{key} must be a 40-character lowercase SHA")
        if input_data.get("base_sha") != input_data.get("base_revision"):
            errors.append("input base SHA and base revision must match")
        if input_data.get("head_sha") != input_data.get("target_revision"):
            errors.append("input head SHA and target revision must match")
        if not changed_files:
            errors.append("changed_files must be non-empty unless blocked")

    changed_keys: list[tuple[str, str, str]] = []
    for row in changed_files:
        path = row.get("path")
        status_value = row.get("status")
        previous = row.get("previous_filename", "")
        if not _text(path) or row.get("filename") != path:
            errors.append(
                "changed file path and filename must be equal non-empty strings"
            )
        if status_value not in FILE_STATUSES:
            errors.append(f"invalid changed file status: {status_value}")
        if not isinstance(previous, str):
            errors.append("changed file previous_filename must be a string")
        key = (
            str(path),
            str(status_value),
            previous if isinstance(previous, str) else "",
        )
        changed_keys.append(key)
    if len(changed_keys) != len(set(changed_keys)):
        errors.append("changed_files must be unique")
    if changed_keys != sorted(changed_keys):
        errors.append("changed_files must be deterministically sorted")
    if isinstance(input_data.get("changed_paths"), list):
        paths = input_data["changed_paths"]
        if paths != sorted(set(paths)):
            errors.append("input.changed_paths must be sorted and unique")
        if [row.get("path") for row in changed_files] != paths:
            errors.append("input.changed_paths must match changed_files")
    elif status != "blocked":
        errors.append("input.changed_paths must be a list")

    pr_metadata = report.get("pr_metadata")
    if not isinstance(pr_metadata, dict):
        errors.append("pr_metadata must be an object")
    elif status != "blocked":
        if pr_metadata.get("number") != input_data.get("pr_number"):
            errors.append("pr_metadata.number must match input.pr_number")
        if pr_metadata.get("base_revision") != input_data.get("base_sha"):
            errors.append("pr_metadata.base_revision must match input.base_sha")
        if pr_metadata.get("target_revision") != input_data.get("head_sha"):
            errors.append("pr_metadata.target_revision must match input.head_sha")

    workflow_runs = _records(report.get("workflow_runs"), "workflow_runs", errors)
    check_runs = _records(report.get("check_runs"), "check_runs", errors)
    workflow_jobs = _records(report.get("workflow_jobs"), "workflow_jobs", errors)
    head_sha = input_data.get("head_sha")
    for label, rows in (("workflow run", workflow_runs), ("check run", check_runs)):
        for row in rows:
            if row.get("head_sha") != head_sha:
                errors.append(f"{label} must be bound to input.head_sha")
    run_ids = {row.get("id") for row in workflow_runs}
    for row in workflow_jobs:
        if row.get("workflow_run_id") not in run_ids:
            errors.append("workflow job references a missing workflow run")

    evidence_status = report.get("evidence_status")
    if not isinstance(evidence_status, dict):
        errors.append("evidence_status must be an object")
    else:
        invalid = set(evidence_status.values()) - EVIDENCE_STATUSES
        if invalid:
            errors.append("invalid evidence statuses: " + ", ".join(sorted(invalid)))
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    else:
        fingerprint = provenance.get("evidence_sha256")
        if not isinstance(fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", fingerprint
        ):
            errors.append("provenance.evidence_sha256 must be a SHA-256 hex digest")
        elif fingerprint != evidence_fingerprint(report):
            errors.append("provenance.evidence_sha256 does not match report evidence")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"greenfield_step1_invalid: {exc}", file=sys.stderr)
        return 2
    errors = validate(report)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    print(json.dumps({"status": "valid"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
