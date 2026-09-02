"""Render Greenfield evidence into the canonical PR-review template."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.analysis_report import canonical_analysis_projection
from greenfield.artifact_io import artifact_sha256
from greenfield.pr_analysis_contract import validate_claims

TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "review" / "pr-review-template.md"


def _evidence_label(row: dict[str, Any]) -> str:
    evidence = row.get("evidence", [])
    labels = []
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            label = item.get("tool_call_id") or item.get("artifact") or item.get("kind")
            if label:
                labels.append(f"`{label}`")
    return ", ".join(dict.fromkeys(labels)) or "unbound"


def _projection_lines(
    *,
    analysis: dict[str, Any] | None,
    behavior_impact: dict[str, Any] | None,
    step2: dict[str, Any] | None,
    step3: dict[str, Any] | None,
    step4: dict[str, Any] | None,
    step5: dict[str, Any] | None,
    assessment: dict[str, Any],
    discovery: dict[str, Any],
    ci_evidence: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    if isinstance(behavior_impact, dict):
        lines.append("**Affected behaviors and interfaces:**")
        behaviors = behavior_impact.get("behaviors", [])
        for behavior in behaviors if isinstance(behaviors, list) else []:
            if not isinstance(behavior, dict):
                continue
            implementation = behavior.get("implementation", {})
            locators = implementation.get("locators", []) if isinstance(implementation, dict) else []
            locations = ", ".join(
                f"`{item.get('path')}:{item.get('line')}`"
                if item.get("line") is not None
                else f"`{item.get('path')}`"
                for item in locators
                if isinstance(item, dict) and item.get("path")
            )
            lines.append(
                f"- `{behavior.get('behavior_id')}` ({behavior.get('status')}): "
                f"{behavior.get('description')}" + (f" [source: {locations}]" if locations else "")
            )

    lines.append("**Repository impact evidence:**")
    impact_rows = analysis.get("repository_impacts", []) if isinstance(analysis, dict) else []
    if not impact_rows and isinstance(step3, dict):
        impact = step3.get("potentially_affected_repositories", {})
        impact_rows = impact.get("items", []) if isinstance(impact, dict) else []
    if not impact_rows and isinstance(step2, dict):
        impact_rows = step2.get("candidates", [])
    for row in impact_rows if isinstance(impact_rows, list) else []:
        if isinstance(row, dict):
            repository = row.get("repository") or row.get("target_repository")
            state = row.get("evidence_state") or row.get("status") or row.get("classification")
            lines.append(
                f"- `{repository}` ({state}): {row.get('rationale') or row.get('reason') or 'impact recorded'} "
                f"[evidence: {_evidence_label(row)}]"
            )
    if not impact_rows:
        lines.append("- No repository impact rows were recorded.")

    lines.append("**Coverage and obligations:**")
    coverage_rows: list[dict[str, Any]] = []
    if isinstance(step4, dict):
        coverage = step4.get("coverage", {})
        obligations = step4.get("obligations", {})
        for section in (coverage, obligations):
            if isinstance(section, dict) and isinstance(section.get("items"), list):
                coverage_rows.extend(row for row in section["items"] if isinstance(row, dict))
    if isinstance(assessment, dict):
        coverage_rows.extend(
            row for row in assessment.get("assessments", []) if isinstance(row, dict)
        )
    for row in coverage_rows:
        label = row.get("interface_id") or row.get("test") or row.get("repository") or "coverage row"
        status = row.get("status") or row.get("classification") or row.get("coverage_status")
        lines.append(f"- `{label}` ({status}): {_evidence_label(row)}")
    if not coverage_rows:
        lines.append("- No coverage or obligation rows were recorded.")

    lines.append("**Executed CI evidence:**")
    if ci_evidence:
        for row in ci_evidence:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('source_repository') or row.get('repository')}` "
                f"({row.get('execution_status', 'execution_unavailable')}) "
                f"at `{row.get('source_revision') or row.get('revision') or 'unbound'}`"
            )
    else:
        lines.append("- No exact CI execution evidence was supplied.")

    lines.append("**Recommended actions:**")
    action_rows = analysis.get("actions", []) if isinstance(analysis, dict) else []
    if not action_rows and isinstance(step5, dict):
        action_rows = step5.get("actions", [])
    for row in action_rows if isinstance(action_rows, list) else []:
        if isinstance(row, dict):
            lines.append(
                f"- `{row.get('action_type')}` in `{row.get('target_repository')}` "
                f"({row.get('evidence_state') or row.get('status')}): "
                f"{row.get('rationale') or row.get('reason') or 'action recorded'} "
                f"[evidence: {_evidence_label(row)}]"
            )
    if not action_rows:
        lines.append("- No actions were recorded.")

    findings = discovery.get("findings", []) if isinstance(discovery, dict) else []
    if findings:
        lines.append("**Review findings:**")
        lines.extend(f"- {finding}" for finding in findings if isinstance(finding, str))
    return lines


def _rendered_template(
    template: str, *, request: Mapping[str, Any], gaps: list[str]
) -> str:
    """Replace sample template content with evidence-bound values or explicit unknowns."""

    changed_paths = request.get("changed_paths", [])
    paths = [str(path) for path in changed_paths if isinstance(path, str)]
    reviewed_rows = "\n".join(
        f"| `{path}` | Changed source | Not assessed | No complete evidence-backed code finding. |"
        for path in paths
    ) or "| Not recorded | Not assessed | Not assessed | No changed-path evidence was supplied. |"
    replacements = {
        "**Type:** Bug Fix | Feature | Refactor | Docs | Chore": "**Type:** Not classified from retained evidence",
        "**Scope:** [1-2 sentence description]": f"**Scope:** {len(paths)} changed file(s) retained in the source request" if paths else "**Scope:** Not computed; changed-path evidence unavailable",
        "**Risk Level:** Low | Medium | High | Critical": "**Risk Level:** Not computed; incomplete evidence is not a risk classification",
        "**Evidence identity:** Source repository `[repo]` | Base `[sha]` | Head `[sha]`": f"**Evidence identity:** Source repository `{request['source_repository']}` | Base `{request['base_revision']}` | Head `{request['head_revision']}`",
        "**Assessment boundary:** Repositories assessed `[list]` | Owner evidence `[available/unavailable]` | CI execution `[status]`": "**Assessment boundary:** See retained evidence and explicit gaps below.",
        "- **Files:** X changed, Y additions, Z deletions": f"- **Files:** {len(paths)} changed; additions/deletions not computed by this projection",
        "- **Commits:** N (avg. message quality: good/needs work)": "- **Commits:** Not assessed",
        "- **Coverage:** API changes [Y/N] | DB migrations [Y/N] | UI [Y/N]": "- **Coverage:** Not assessed from retained evidence",
        "| Confirmed | [API/workflow/database/permission] | `path/to/entity.ent` or flow path | Confirmed | [catalog record/revision] |\n| Candidate | Caller chain | `path/to/caller.cls` | Candidate | [relationship/revision] |": "| Not assessed | No inferred surface | Not assessed | Partial | Retained evidence projection below |",
        "- [Missing, unavailable, stale, ambiguous, not-modelled, or not-recorded-in-PR evidence]": "- Explicit gaps are rendered from retained artifacts below.",
        "| `path/to/file1.cls` | Logic | ✓ | [brief comment] |\n| `path/to/file2.js` | UI | ⚠ | [specific concern] |": reviewed_rows,
        "| `repo/key` | `feature > scenario` | Confirmed / Candidate / Uncovered | Keep / Update / Add / Review | [revision and lines] |": "| Not assessed | No revision-bound test evidence | not_assessed | Provide bound test evidence | Retained analysis |",
        "- [Exact missing, stale, unavailable, or weak coverage]\n- [Use `not_assessed` when a nominated test repository lacks a confirmed relation and revision-bound test evidence]": "- Coverage remains `not_assessed` until revision-bound test and CI evidence is retained.",
        "- **[File:Line]** Exact issue with reproducible impact and fix": "- No evidence-backed critical finding recorded.",
        "- **[File:Line]** Pattern/inconsistency; recommend action": "- No evidence-backed medium-priority finding recorded.",
        "- **[File:Line]** Suggestion for improvement": "- No evidence-backed nice-to-have finding recorded.",
        "- **[File:Line]** What was done well": "- No strength claim is made without retained evidence.",
        "**Confidence:** [score or `Not computed`; describe evidence scope, not business risk]": "**Confidence:** Not computed; describe only retained evidence scope.",
        "**Recommendation:** Approve ✓ / Request Changes ⚠ / Comment 💬": "**Recommendation:** Comment; evidence is partial or unavailable.",
        "- [Explicit unresolved, unavailable, stale, or deferred evidence]\n- [Evidence scope and target revision limitation]\n- [AI guidance files are advisory context and never establish impact, ownership, or coverage]": "- See explicit gaps and retained source identity above.",
        "**Next Reviewer:** @team-compliance (domain experts for e-invoicing logic)": "**Next Reviewer:** Not assigned by retained evidence",
    }
    for source, replacement in replacements.items():
        template = template.replace(source, replacement)
    if gaps:
        template = template.replace(
            "---\n\n## ✅ Reviewed",
            "**Required evidence to continue:** Provide revision-bound source, test, and CI evidence for the explicit gaps; unavailable evidence remains unavailable.\n\n---\n\n## ✅ Reviewed",
            1,
        )
    return template


def render_review(
    *,
    request: dict[str, Any],
    discovery: dict[str, Any],
    assessment: dict[str, Any],
    ci_evidence: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    behavior_impact: dict[str, Any] | None = None,
    step2: dict[str, Any] | None = None,
    step3: dict[str, Any] | None = None,
    step4: dict[str, Any] | None = None,
    step5: dict[str, Any] | None = None,
    planning: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    for artifact in (analysis, step2, step3, step4, step5, planning):
        if isinstance(artifact, dict):
            gaps.extend(str(value) for value in artifact.get("gaps", []))
    if isinstance(analysis, dict):
        impacts = analysis.get("repository_impacts", [])
        evidence_lines.append(
            "- **Ranked impact:** "
            + (", ".join(
                f"`{row.get('repository')}` ({row.get('evidence_state')})"
                for row in impacts
                if isinstance(row, dict)
            ) or "none")
            + "."
        )
        coverage = analysis.get("coverage", {})
        evidence_lines.append(
            "- **Coverage assessment:** " + str(coverage.get("status", "not_assessed"))
            + "."
        )
    if isinstance(planning, dict):
        evidence_lines.append(
            "- **Planner lifecycle:** "
            + str(planning.get("status", "not_run"))
            + "; "
            + str(len(planning.get("cycles", [])))
            + " retained cycle(s)."
        )
    if isinstance(behavior_impact, dict):
        evidence_lines.append(
            "- **Behavior projection:** revision-bound artifact retained."
        )
    evidence_lines.extend(
        _projection_lines(
            analysis=analysis,
            behavior_impact=behavior_impact,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            assessment=assessment,
            discovery=discovery,
            ci_evidence=ci_evidence,
        )
    )
    gaps = sorted(set(gaps))
    marker = "**Explicit gaps:**\n"
    markdown = _rendered_template(template, request=request, gaps=gaps)
    markdown = markdown.replace(marker, marker + "\n" + "\n".join(evidence_lines + [f"- {gap}" for gap in gaps]) + "\n", 1)
    report = {"schema_version": "0.1", "analysis_kind": "greenfield_pr_review", "status": "partial" if gaps else "complete", "claims": discovery.get("claims", []), "markdown": markdown, "gaps": gaps, "provenance": {"template": str(TEMPLATE.relative_to(TEMPLATE.parents[2])), "read_only": True, "analysis_report_sha256": analysis.get("report_sha256") if isinstance(analysis, dict) else None, "behavior_impact_sha256": behavior_impact.get("handbook_sha256") if isinstance(behavior_impact, dict) else None, "planning_sha256": planning.get("planning_sha256") if isinstance(planning, dict) else None}, **({"canonical_analysis": canonical_analysis_projection(analysis)} if isinstance(analysis, dict) else {})}
    report["review_sha256"] = artifact_sha256(report)
    return report


def validate_review(report: dict[str, Any]) -> list[str]:
    errors = validate_claims(report, kind="greenfield_pr_review")
    if not isinstance(report.get("markdown"), str) or "## 🔍 Review Summary" not in report["markdown"]:
        errors.append("markdown must use the canonical template")
    provenance = report.get("provenance")
    if isinstance(report.get("canonical_analysis"), dict):
        digest = provenance.get("analysis_report_sha256") if isinstance(provenance, dict) else None
        if not isinstance(digest, str) or len(digest) != 64:
            errors.append("provenance.analysis_report_sha256 must be SHA-256")
    digest = report.get("review_sha256")
    unsigned = dict(report)
    unsigned.pop("review_sha256", None)
    if not isinstance(digest, str) or artifact_sha256(unsigned) != digest:
        errors.append("review_sha256 does not match report")
    return errors


__all__ = ["render_review", "validate_review"]
