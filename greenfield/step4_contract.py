"""Contracts and validation helpers for greenfield PR-impact Step 4."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.semantic_contract import load_index
from greenfield.artifact_io import artifact_sha256
from greenfield.step2_contract import (
    load_ci_evidence,
    load_contract,
    load_repository_inventory,
)
from greenfield.source_identity import validate_identity_fields

REPORT_SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_pr_impact_step_4"
RULE_SET_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CLASSIFICATIONS = {
    "covered",
    "indirectly_covered",
    "candidate",
    "stale",
    "missing",
    "unavailable",
    "unknown",
}
CLASSIFICATION_ORDER = {
    "covered": 0,
    "indirectly_covered": 1,
    "candidate": 2,
    "stale": 3,
    "missing": 4,
    "unavailable": 5,
    "unknown": 6,
}


class Step4Error(ValueError):
    """Raised when Step 4 evidence cannot be evaluated safely."""


def load_step3_report(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Step4Error(f"step3_report_read_failed: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise Step4Error("Step 3 report must be an object")
    return value


def load_contract_evidence(path: str | Path) -> dict[str, Any]:
    return load_contract(path)


def load_ci_evidence_file(path: str | Path) -> dict[str, Any]:
    return load_ci_evidence(path)


def load_inventory_evidence(path: str | Path) -> dict[str, Any]:
    return load_repository_inventory(path)


def load_semantic_evidence(path: str | Path) -> dict[str, Any]:
    try:
        value = load_index(path)
    except ValueError as exc:
        raise Step4Error(str(exc)) from exc
    normalized = dict(value)
    normalized["evidence_path"] = Path(path).as_posix()
    return normalized


def evidence_digest(value: Mapping[str, Any]) -> str:
    provenance = value.get("provenance")
    if isinstance(provenance, Mapping):
        for key in ("index_sha256", "response_sha256"):
            digest = provenance.get(key)
            if isinstance(digest, str) and SHA256.fullmatch(digest):
                return digest
    evidence = value.get("evidence")
    if isinstance(evidence, Mapping):
        digest = evidence.get("sha256")
        if isinstance(digest, str) and SHA256.fullmatch(digest):
            return digest
    return artifact_sha256(value)


def validate_step4_report(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["report must be an object"]
    errors: list[str] = []
    required = {
        "schema_version",
        "analysis_kind",
        "status",
        "input",
        "coverage",
        "obligations",
        "gaps",
        "warnings",
        "provenance",
    }
    errors.extend(f"missing field: {key}" for key in sorted(required - set(report)))
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {REPORT_SCHEMA_VERSION}")
    if report.get("analysis_kind") != ANALYSIS_KIND:
        errors.append("analysis_kind is invalid")
    if report.get("status") not in {"complete", "partial"}:
        errors.append("status must be complete or partial")
    data = report.get("input")
    if not isinstance(data, dict):
        errors.append("input must be an object")
    else:
        if not isinstance(data.get("source_repository"), str) or not data["source_repository"].strip():
            errors.append("input.source_repository is required")
        if not isinstance(data.get("target_revision"), str) or not SHA.fullmatch(data["target_revision"]):
            errors.append("input.target_revision must be a lowercase 40-character SHA")
        if not isinstance(data.get("changed_paths"), list) or not data["changed_paths"]:
            errors.append("input.changed_paths must be a non-empty list")
        errors.extend(validate_identity_fields(data))
    for surface_name in ("coverage", "obligations"):
        surface = report.get(surface_name)
        if not isinstance(surface, dict):
            errors.append(f"{surface_name} must be an object")
            continue
        if surface.get("status") not in {
            "available",
            "partial",
            "unavailable",
            "not_modelled",
            "unknown",
        }:
            errors.append(f"{surface_name}.status is invalid")
        if not isinstance(surface.get("items"), list):
            errors.append(f"{surface_name}.items must be a list")
    coverage = report.get("coverage", {}).get("items", []) if isinstance(report.get("coverage"), dict) else []
    keys: list[tuple[Any, ...]] = []
    for index, item in enumerate(coverage):
        label = f"coverage.items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        classification = item.get("classification")
        if classification not in CLASSIFICATIONS:
            errors.append(f"{label}.classification is invalid")
        for field in ("target_repository", "interface_id", "source_repository", "source_revision", "reason"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{label}.{field} is required")
        if not SHA.fullmatch(str(item.get("source_revision", ""))):
            errors.append(f"{label}.source_revision is invalid")
        test = item.get("test")
        if test is not None and (
            not isinstance(test, dict)
            or not isinstance(test.get("id"), str)
            or not test["id"].strip()
            or not isinstance(test.get("path"), str)
            or not test["path"].strip()
        ):
            errors.append(f"{label}.test is invalid")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(value, dict) for value in evidence):
            errors.append(f"{label}.evidence must be a non-empty list of objects")
        keys.append((CLASSIFICATION_ORDER.get(classification, 99), str(item.get("target_repository")), str(item.get("interface_id")), str(item.get("behavior_id", "")), json.dumps(test, sort_keys=True)))
    if keys != sorted(keys):
        errors.append("coverage.items must be deterministically ordered")
    if len(keys) != len(set(keys)):
        errors.append("coverage.items must be unique")
    obligations = report.get("obligations", {}).get("items", []) if isinstance(report.get("obligations"), dict) else []
    for index, item in enumerate(obligations):
        label = f"obligations.items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        if item.get("status") not in {"satisfied", "missing", "unavailable", "unknown"}:
            errors.append(f"{label}.status is invalid")
        for field in ("target_repository", "interface_id", "test_id", "test_path"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{label}.{field} is required")
    for key in ("gaps", "warnings"):
        if not isinstance(report.get(key), list) or any(not isinstance(value, str) for value in report[key]):
            errors.append(f"{key} must be a list of strings")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
    if isinstance(provenance, dict):
        digest = provenance.get("step3_report_sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append("provenance.step3_report_sha256 must be lowercase SHA-256")
    return errors


__all__ = [
    "ANALYSIS_KIND",
    "CLASSIFICATIONS",
    "CLASSIFICATION_ORDER",
    "REPORT_SCHEMA_VERSION",
    "RULE_SET_VERSION",
    "SHA",
    "SHA256",
    "Step4Error",
    "artifact_sha256",
    "evidence_digest",
    "load_ci_evidence_file",
    "load_contract_evidence",
    "load_inventory_evidence",
    "load_semantic_evidence",
    "load_step3_report",
    "validate_step4_report",
]
