"""Strict, revision-bound contracts for the Greenfield PR analysis flow."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from greenfield.artifact_io import artifact_sha256

SCHEMA_VERSION = "0.1"
EVIDENCE_STATES = frozenset({"confirmed", "candidate", "unavailable", "unresolved"})
SHA = re.compile(r"^[0-9a-f]{40}$")


class PRAnalysisContractError(ValueError):
    """Raised when a PR-analysis artifact would lose its evidence binding."""


def make_request(step1: Mapping[str, Any]) -> dict[str, Any]:
    """Create the immutable request handoff from validated Step 1 evidence."""

    source = step1.get("input")
    if not isinstance(source, Mapping):
        raise PRAnalysisContractError("Step 1 input is required")
    repository = source.get("repository") or source.get("canonical_repository") or source.get("repo_key")
    head = source.get("target_revision") or source.get("head_sha")
    base = source.get("base_revision") or source.get("base_sha")
    if not isinstance(repository, str) or not repository:
        raise PRAnalysisContractError("source repository is required")
    if not isinstance(head, str) or not SHA.fullmatch(head):
        raise PRAnalysisContractError("source head revision must be a lowercase SHA")
    if not isinstance(base, str) or not SHA.fullmatch(base):
        raise PRAnalysisContractError("source base revision must be a lowercase SHA")
    request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": "greenfield_pr_analysis_request",
        "source_repository": repository,
        "source_repo_key": source.get("repo_key"),
        "pr_number": source.get("pr_number"),
        "base_revision": base,
        "head_revision": head,
        "changed_paths": sorted(
            str(row.get("path") or row.get("filename"))
            for row in step1.get("changed_files", [])
            if isinstance(row, Mapping) and (row.get("path") or row.get("filename"))
        ),
        "evidence": [{"artifact": "step1.json", "sha256": artifact_sha256(step1)}],
        "provenance": {"read_only": True, "github_writes": "none", "catalog_mutation": "none"},
    }
    request["request_sha256"] = artifact_sha256(request)
    return request


def validate_claims(report: Mapping[str, Any], *, kind: str) -> list[str]:
    """Validate common discovery, assessment, and review evidence claims."""

    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 0.1")
    if report.get("analysis_kind") != kind:
        errors.append(f"analysis_kind must be {kind}")
    if report.get("status") not in {"complete", "partial", "blocked"}:
        errors.append("status is invalid")
    claims = report.get("claims", [])
    if not isinstance(claims, list):
        return errors + ["claims must be a list"]
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            errors.append(f"claims[{index}] must be an object")
            continue
        for field in ("repository", "inspected_revision", "evidence_status", "evidence", "rationale"):
            if not claim.get(field):
                errors.append(f"claims[{index}].{field} is required")
        if claim.get("evidence_status") not in EVIDENCE_STATES:
            errors.append(f"claims[{index}].evidence_status is invalid")
        if not isinstance(claim.get("inspected_revision"), str) or not SHA.fullmatch(str(claim.get("inspected_revision"))):
            errors.append(f"claims[{index}].inspected_revision must be a lowercase SHA")
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"claims[{index}].evidence must be non-empty")
    return errors


__all__ = ["EVIDENCE_STATES", "PRAnalysisContractError", "make_request", "validate_claims"]
