"""Validate a greenfield Step 2 candidate report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.step2_candidates import ANALYSIS_KIND, REPORT_SCHEMA_VERSION

SHA256 = re.compile(r"^[0-9a-f]{64}$")
CLASSIFICATIONS = {"confirmed", "candidate", "unresolved", "stale", "unavailable", "unknown"}
BLAST_RADIUS = {"local", "boundary", "multi_repo", "systemic", "unknown"}
CLASSIFICATION_ORDER = {"confirmed": 0, "candidate": 1, "unresolved": 2, "stale": 3, "unavailable": 4, "unknown": 5}


def validate(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["report must be an object"]
    errors: list[str] = []
    required = {"schema_version", "analysis_kind", "status", "input", "candidates", "blast_radius", "gaps", "warnings", "provenance"}
    errors.extend(f"missing field: {key}" for key in sorted(required - set(report)))
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {REPORT_SCHEMA_VERSION}")
    if report.get("analysis_kind") != ANALYSIS_KIND:
        errors.append("invalid analysis_kind")
    if report.get("status") not in {"complete", "partial"}:
        errors.append("status must be complete or partial")
    data = report.get("input")
    if not isinstance(data, dict):
        errors.append("input must be an object")
    else:
        for key in ("source_repository", "target_revision", "changed_paths"):
            if key not in data:
                errors.append(f"missing input field: {key}")
        if not isinstance(data.get("changed_paths"), list):
            errors.append("input.changed_paths must be a list")
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        errors.append("candidates must be a list")
        candidates = []
    candidate_keys: list[tuple[str, str, str, str]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            errors.append("candidate must be an object")
            continue
        for key in ("target_repository", "interface_id", "relationship_type", "classification", "reason", "evidence"):
            if key not in candidate:
                errors.append(f"candidate missing field: {key}")
        if candidate.get("classification") not in CLASSIFICATIONS:
            errors.append("candidate classification is invalid")
        if not isinstance(candidate.get("evidence"), list) or not candidate.get("evidence"):
            errors.append("candidate evidence must be non-empty")
        for evidence in candidate.get("evidence", []):
            if not isinstance(evidence, dict):
                errors.append("candidate evidence item must be an object")
                continue
            if evidence.get("kind") == "repository_inventory":
                response_sha = evidence.get("response_sha256")
                if not isinstance(response_sha, str) or not SHA256.fullmatch(response_sha):
                    errors.append("repository inventory evidence requires response_sha256")
                for key in ("repository", "inspected_revision", "source_revision", "artifact_status"):
                    if key not in evidence:
                        errors.append(f"repository inventory evidence missing {key}")
        if any(
            isinstance(evidence, dict)
            and evidence.get("kind") == "repository_inventory"
            for evidence in candidate.get("evidence", [])
        ) and candidate.get("classification") != "candidate":
            errors.append("repository inventory evidence can only classify a candidate")
        candidate_keys.append((str(candidate.get("target_repository")), str(candidate.get("interface_id")), str(candidate.get("relationship_type")), str(candidate.get("classification"))))
    expected_keys = sorted(candidate_keys, key=lambda key: (CLASSIFICATION_ORDER.get(key[3], 99), *key[:3]))
    if candidate_keys != expected_keys:
        errors.append("candidates must be deterministically ordered")
    if len(candidate_keys) != len(set(candidate_keys)):
        errors.append("candidates must be unique")
    if report.get("blast_radius") not in BLAST_RADIUS:
        errors.append("blast_radius is invalid")
    for key in ("gaps", "warnings"):
        if not isinstance(report.get(key), list) or any(not isinstance(item, str) for item in report[key]):
            errors.append(f"{key} must be a list of strings")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
    digest = provenance.get("step1_report_sha256") if isinstance(provenance, dict) else None
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append("provenance.step1_report_sha256 must be lowercase SHA-256")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid report: {exc}", file=sys.stderr)
        return 2
    errors = validate(report)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
