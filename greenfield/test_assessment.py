"""Evidence-gated test assessment; does not generate executable PR work."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from greenfield.pr_analysis_contract import validate_claims

SHA256 = re.compile(r"^[0-9a-f]{64}$")

ASSESSMENT_STATES = frozenset(
    {"covers", "needs_update", "missing", "unrelated", "unresolved", "not_assessed"}
)


def build_assessment(
    *,
    repository: str,
    revision: str,
    candidates: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    assessed: bool,
    analysis_report_sha256: str,
    canonical_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    rows = []
    state_by_action = {
        "run_test_suite": "covers",
        "update_existing_test": "needs_update",
        "add_missing_test": "missing",
    }
    for candidate in candidates:
        target_repository = str(candidate.get("target_repository") or repository)
        target_revision = candidate.get("target_revision") or revision
        rows.append(
            {
                "repository": target_repository,
                "inspected_revision": target_revision,
                "status": state_by_action.get(
                    str(candidate.get("action_type")), "unresolved"
                )
                if assessed
                else "not_assessed",
                "test": candidate,
                "evidence": evidence,
                "rationale": "assessment follows the evidence-backed analysis action",
            }
        )
    status = "partial" if rows or not assessed else "complete"
    claim_rows = rows or [{"repository": repository, "inspected_revision": revision}]
    claims = [
        {
            "repository": row["repository"],
            "inspected_revision": row["inspected_revision"],
            "evidence_status": "candidate" if assessed else "unavailable",
            "evidence": evidence or [{"kind": "repository_context"}],
            "rationale": "test assessment is limited to evidence-gated candidates",
        }
        for row in {
            (str(item["repository"]), str(item["inspected_revision"])): item
            for item in claim_rows
        }.values()
    ]
    return {
        "schema_version": "0.1",
        "analysis_kind": "greenfield_test_assessment",
        "status": status,
        "claims": claims,
        "assessments": rows,
        "gaps": ([] if assessed else ["test_repository_not_assessed"]),
        "provenance": {
            "read_only": True,
            "github_writes": "none",
            "catalog_mutation": "none",
            "analysis_report_sha256": analysis_report_sha256,
        },
        "canonical_analysis": deepcopy(dict(canonical_analysis)),
    }


def validate_assessment(report: Mapping[str, Any]) -> list[str]:
    errors = validate_claims(report, kind="greenfield_test_assessment")
    provenance = report.get("provenance")
    digest = provenance.get("analysis_report_sha256") if isinstance(provenance, Mapping) else None
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append("provenance.analysis_report_sha256 must be SHA-256")
    if not isinstance(report.get("canonical_analysis"), Mapping):
        errors.append("canonical_analysis must be an object")
    rows = report.get("assessments")
    if not isinstance(rows, list):
        return errors + ["assessments must be a list"]
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("status") not in ASSESSMENT_STATES:
            errors.append(f"assessments[{index}].status is invalid")
    return errors


__all__ = ["ASSESSMENT_STATES", "build_assessment", "validate_assessment"]
