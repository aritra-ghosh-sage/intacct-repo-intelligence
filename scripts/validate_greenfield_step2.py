"""Validate a greenfield Step 2 candidate report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.source_identity import validate_identity_fields
from greenfield.step2_candidates import (
    ANALYSIS_KIND,
    REPORT_SCHEMA_VERSION,
    _evidence_score,
)

SHA256 = re.compile(r"^[0-9a-f]{64}$")
CLASSIFICATIONS = {
    "confirmed",
    "candidate",
    "unresolved",
    "stale",
    "unavailable",
    "unknown",
}
BLAST_RADIUS = {"local", "boundary", "multi_repo", "systemic", "unknown"}
CLASSIFICATION_ORDER = {
    "confirmed": 0,
    "candidate": 1,
    "unresolved": 2,
    "stale": 3,
    "unavailable": 4,
    "unknown": 5,
}
LIKELY_CONFIDENCES = {"high", "medium", "low"}


def validate(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["report must be an object"]
    errors: list[str] = []
    required = {
        "schema_version",
        "analysis_kind",
        "status",
        "input",
        "candidates",
        "blast_radius",
        "gaps",
        "warnings",
        "provenance",
    }
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
        errors.extend(validate_identity_fields(data))
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        errors.append("candidates must be a list")
        candidates = []
    candidate_keys: list[tuple[str, str, str, str]] = []
    score_presence = {"evidence_score" in candidate for candidate in candidates if isinstance(candidate, dict)}
    if score_presence == {True, False}:
        errors.append("evidence_score must be present on every candidate or none")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            errors.append("candidate must be an object")
            continue
        for key in (
            "target_repository",
            "interface_id",
            "relationship_type",
            "classification",
            "reason",
            "evidence",
        ):
            if key not in candidate:
                errors.append(f"candidate missing field: {key}")
        if candidate.get("classification") not in CLASSIFICATIONS:
            errors.append("candidate classification is invalid")
        if not isinstance(candidate.get("evidence"), list) or not candidate.get(
            "evidence"
        ):
            errors.append("candidate evidence must be non-empty")
        for evidence in candidate.get("evidence", []):
            if not isinstance(evidence, dict):
                errors.append("candidate evidence item must be an object")
                continue
            if evidence.get("kind") == "repository_inventory":
                response_sha = evidence.get("response_sha256")
                if not isinstance(response_sha, str) or not SHA256.fullmatch(
                    response_sha
                ):
                    errors.append(
                        "repository inventory evidence requires response_sha256"
                    )
                for key in (
                    "repository",
                    "inspected_revision",
                    "source_revision",
                    "artifact_status",
                    "ci_linkage_status",
                ):
                    if key not in evidence:
                        errors.append(f"repository inventory evidence missing {key}")
                if evidence.get("ci_linkage_status") not in {
                    "available",
                    "unavailable",
                }:
                    errors.append("repository inventory ci_linkage_status is invalid")
                if (
                    evidence.get("artifact_status") == "available"
                    and evidence.get("ci_linkage_status") != "available"
                ):
                    errors.append(
                        "repository inventory artifact requires available ci linkage"
                    )
            if evidence.get("kind") == "semantic_index":
                digest = evidence.get("index_sha256")
                if not isinstance(digest, str) or not SHA256.fullmatch(digest):
                    errors.append("semantic index evidence requires index_sha256")
        if (
            any(
                isinstance(evidence, dict)
                and evidence.get("kind") == "repository_inventory"
                for evidence in candidate.get("evidence", [])
            )
            and candidate.get("classification") != "candidate"
        ):
            errors.append("repository inventory evidence can only classify a candidate")
        if (
            any(
                isinstance(evidence, dict) and evidence.get("kind") == "semantic_index"
                for evidence in candidate.get("evidence", [])
            )
            and not any(
                isinstance(evidence, dict)
                and evidence.get("kind") in {"contract", "ci"}
                for evidence in candidate.get("evidence", [])
            )
            and candidate.get("classification") != "candidate"
        ):
            errors.append("semantic index evidence can only classify a candidate")
        _validate_source_anchors(candidate.get("source_anchors"), errors)
        _validate_likely_tests(candidate, errors)
        if "evidence_score" in candidate:
            expected_score = _evidence_score(candidate)
            if candidate.get("evidence_score") != expected_score:
                errors.append("candidate evidence_score does not match evidence")
        candidate_keys.append(
            (
                str(candidate.get("target_repository")),
                str(candidate.get("interface_id")),
                str(candidate.get("relationship_type")),
                str(candidate.get("classification")),
            )
        )
    expected_keys = sorted(
        candidate_keys, key=lambda key: (CLASSIFICATION_ORDER.get(key[3], 99), *key[:3])
    )
    if candidate_keys != expected_keys:
        errors.append("candidates must be deterministically ordered")
    if len(candidate_keys) != len(set(candidate_keys)):
        errors.append("candidates must be unique")
    if report.get("blast_radius") not in BLAST_RADIUS:
        errors.append("blast_radius is invalid")
    for key in ("gaps", "warnings"):
        if not isinstance(report.get(key), list) or any(
            not isinstance(item, str) for item in report[key]
        ):
            errors.append(f"{key} must be a list of strings")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
    digest = (
        provenance.get("step1_report_sha256") if isinstance(provenance, dict) else None
    )
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append("provenance.step1_report_sha256 must be lowercase SHA-256")
    return errors


def _validate_source_anchors(value: Any, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        errors.append("candidate source_anchors must be a list")
        return
    prior: tuple[str, str, str] | None = None
    for anchor in value:
        if not isinstance(anchor, dict):
            errors.append("source anchor must be an object")
            continue
        for key in ("source_path", "entity", "source_revision", "interfaces", "evidence"):
            if key not in anchor:
                errors.append(f"source anchor missing {key}")
        path = anchor.get("source_path")
        if not isinstance(path, str) or not path:
            errors.append("source anchor requires source_path")
        revision = anchor.get("source_revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            errors.append("source anchor requires lowercase source_revision")
        interfaces = anchor.get("interfaces")
        if not isinstance(interfaces, list):
            errors.append("source anchor interfaces must be a list")
        else:
            for mapping in interfaces:
                if not isinstance(mapping, dict):
                    errors.append("source interface mapping must be an object")
                    continue
                if not isinstance(mapping.get("interface_id"), str) or not mapping["interface_id"]:
                    errors.append("source interface mapping requires interface_id")
                if mapping.get("mapping_kind") not in {
                    "semantic_source_contract",
                    "explicit_source_contract",
                }:
                    errors.append("source interface mapping has invalid mapping_kind")
        evidence = anchor.get("evidence")
        if not isinstance(evidence, list):
            errors.append("source anchor evidence must be a list")
        key = (str(path), str(anchor.get("source_symbol", "")), str(anchor.get("entity", "")))
        if prior is not None and key < prior:
            errors.append("source_anchors must be deterministically ordered")
        prior = key


def _validate_likely_tests(candidate: dict[str, Any], errors: list[str]) -> None:
    value = candidate.get("likely_tests")
    if value is None:
        return
    if not isinstance(value, list):
        errors.append("candidate likely_tests must be a list")
        return
    inventory = candidate.get("inventory_paths")
    inventory_paths = set(inventory) if isinstance(inventory, list) else None
    prior: tuple[int, str] | None = None
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            errors.append("likely test must be an object")
            continue
        path = item.get("path")
        score = item.get("score")
        if not isinstance(path, str) or not path:
            errors.append("likely test requires path")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            errors.append("likely test score must be an integer between 0 and 100")
        if item.get("score_rule_set_version") != "0.1":
            errors.append("likely test has unsupported score rule set")
        if item.get("confidence") not in LIKELY_CONFIDENCES:
            errors.append("likely test confidence is invalid")
        if not isinstance(item.get("reasons"), list) or any(
            not isinstance(reason, str) or not reason for reason in item["reasons"]
        ):
            errors.append("likely test reasons must be non-empty strings")
        if not isinstance(item.get("evidence"), list) or not item["evidence"]:
            errors.append("likely test evidence must be non-empty")
        if item.get("basis") not in {"contract_backed", "source_ranked"}:
            errors.append("likely test basis is invalid")
        if isinstance(path, str) and inventory_paths is not None and path not in inventory_paths:
            errors.append("likely test path must be present in inventory_paths")
        if isinstance(path, str):
            if path in seen:
                errors.append("likely_tests contains duplicate paths")
            seen.add(path)
        if isinstance(score, int) and isinstance(path, str):
            key = (-score, path)
            if prior is not None and key < prior:
                errors.append("likely_tests must be deterministically ordered")
            prior = key


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
