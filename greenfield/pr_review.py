"""Render Greenfield evidence into the canonical PR-review template."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from greenfield.pr_analysis_contract import validate_claims

TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "review" / "pr-review-template.md"


def render_review(*, request: dict[str, Any], discovery: dict[str, Any], assessment: dict[str, Any], ci_evidence: list[dict[str, Any]], contexts: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill stable evidence fields while retaining the canonical heading order."""

    template = TEMPLATE.read_text(encoding="utf-8")
    evidence_lines = [f"- **Source identity:** `{request['source_repository']}` base `{request['base_revision']}`, head `{request['head_revision']}`.", f"- **Assessed repositories:** {', '.join(sorted({claim['repository'] for claim in discovery.get('claims', [])})) or 'none'}."]
    owners = [owner for context in contexts for owner in context.get("owners", [])]
    owner_gap = [owner["path"] for owner in owners if owner.get("status") == "ownership_unavailable"]
    if owner_gap:
        evidence_lines.append("- **Owner evidence unavailable:** " + ", ".join(f"`{path}`" for path in owner_gap) + ".")
    ci_states = sorted({str(row.get("execution_status", "execution_unavailable")) for row in ci_evidence})
    evidence_lines.append("- **CI execution:** " + (", ".join(ci_states) if ci_states else "execution_unavailable") + ".")
    gaps = [*discovery.get("gaps", []), *assessment.get("gaps", [])]
    marker = "**Explicit gaps:**\n"
    markdown = template.replace(marker, marker + "\n" + "\n".join(evidence_lines + [f"- {gap}" for gap in gaps]) + "\n", 1)
    report = {"schema_version": "0.1", "analysis_kind": "greenfield_pr_review", "status": "partial" if gaps else "complete", "claims": discovery.get("claims", []), "markdown": markdown, "gaps": gaps, "provenance": {"template": str(TEMPLATE.relative_to(TEMPLATE.parents[2])), "read_only": True}}
    return report


def validate_review(report: dict[str, Any]) -> list[str]:
    errors = validate_claims(report, kind="greenfield_pr_review")
    if not isinstance(report.get("markdown"), str) or "## 🔍 Review Summary" not in report["markdown"]:
        errors.append("markdown must use the canonical template")
    return errors


__all__ = ["render_review", "validate_review"]
