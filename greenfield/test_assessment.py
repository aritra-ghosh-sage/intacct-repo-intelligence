"""Evidence-gated test assessment; does not generate executable PR work."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from greenfield.pr_analysis_contract import validate_claims

ASSESSMENT_STATES = frozenset({"covers", "needs_update", "missing", "unrelated", "unresolved", "not_assessed"})


def build_assessment(*, repository: str, revision: str, candidates: list[dict[str, Any]], evidence: list[dict[str, Any]], assessed: bool) -> dict[str, Any]:
    rows = []
    for candidate in candidates:
        rows.append({"repository": repository, "inspected_revision": revision, "status": "unresolved" if assessed else "not_assessed", "test": candidate, "evidence": evidence, "rationale": "candidate requires source-backed behavior review"})
    status = "partial" if rows or not assessed else "complete"
    return {"schema_version": "0.1", "analysis_kind": "greenfield_test_assessment", "status": status, "claims": [{"repository": repository, "inspected_revision": revision, "evidence_status": "candidate" if assessed else "unavailable", "evidence": evidence or [{"kind": "repository_context"}], "rationale": "test assessment is limited to evidence-gated candidates"}], "assessments": rows, "gaps": ([] if assessed else ["test_repository_not_assessed"]), "provenance": {"read_only": True, "github_writes": "none", "catalog_mutation": "none"}}


def validate_assessment(report: Mapping[str, Any]) -> list[str]:
    errors = validate_claims(report, kind="greenfield_test_assessment")
    rows = report.get("assessments")
    if not isinstance(rows, list):
        return errors + ["assessments must be a list"]
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("status") not in ASSESSMENT_STATES:
            errors.append(f"assessments[{index}].status is invalid")
    return errors


__all__ = ["ASSESSMENT_STATES", "build_assessment", "validate_assessment"]
