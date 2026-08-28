"""Consolidated evidence-backed output of the Greenfield Analyze phase."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from greenfield.artifact_io import artifact_sha256
from greenfield.run_context import validate_run_context

SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_analysis_report"
EVIDENCE_STATES = frozenset(
    {
        "confirmed",
        "strong_candidate",
        "candidate",
        "unresolved",
        "unavailable",
        "no_evidence",
    }
)
ACTION_ALIASES = {
    "run_test_suite": "run_test_suite",
    "update_test_obligation": "update_existing_test",
    "update_existing_test": "update_existing_test",
    "add_integration_test": "add_missing_test",
    "add_missing_test": "add_missing_test",
    "request_owner_review": "request_owner_review",
    "block_propagation": "block_automation",
    "block_automation": "block_automation",
}
AUTOMATIC_DRAFT_STATES = frozenset({"confirmed", "strong_candidate"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AnalysisReportError(ValueError):
    """Raised when analysis loses its evidence or revision binding."""


def _evidence_state(row: Mapping[str, Any]) -> str:
    value = row.get("evidence_state") or row.get("evidence_status")
    if value in EVIDENCE_STATES:
        return str(value)
    classification = row.get("classification") or row.get("status")
    if classification in {"confirmed", "covered", "indirectly_covered", "available"}:
        return "confirmed"
    if classification in {"candidate", "likely"}:
        return "candidate"
    if classification in {"unavailable", "stale"}:
        return "unavailable"
    return "unresolved"


def _evidence(rows: Any, fallback: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = [dict(row) for row in rows or [] if isinstance(row, Mapping)]
    return result or [dict(fallback)]


def build_analysis_report(
    run_context: Mapping[str, Any],
    *,
    step2: Mapping[str, Any],
    step3: Mapping[str, Any],
    step4: Mapping[str, Any],
    step5: Mapping[str, Any],
    agent_analysis: Mapping[str, Any] | None = None,
    tool_calls: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fold compatibility step artifacts and Strands guidance into one report."""

    context_errors = validate_run_context(run_context)
    if context_errors:
        raise AnalysisReportError("invalid run context: " + "; ".join(context_errors))
    artifacts = {
        "step2": artifact_sha256(step2),
        "step3": artifact_sha256(step3),
        "step4": artifact_sha256(step4),
        "step5": artifact_sha256(step5),
    }
    fallback = {"kind": "artifact", "artifact": "step3", "sha256": artifacts["step3"]}
    repositories: dict[str, dict[str, Any]] = {}
    surface = step3.get("potentially_affected_repositories", {})
    rows = surface.get("items", []) if isinstance(surface, Mapping) else []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        repository = str(row.get("target_repository") or row.get("repository") or "")
        if not repository:
            continue
        repositories[repository] = {
            "repository": repository,
            "evidence_state": _evidence_state(row),
            "rank": None,
            "rationale": str(
                row.get("rationale") or row.get("reason") or "Step 3 impact outcome"
            ),
            "evidence": _evidence(row.get("evidence"), fallback),
        }
    actions = []
    for row in step5.get("actions", []):
        if not isinstance(row, Mapping):
            continue
        action_type = ACTION_ALIASES.get(str(row.get("action_type")))
        if action_type is None:
            continue
        state = "unavailable" if row.get("status") == "blocked" else "candidate"
        target = str(row.get("target_repository") or "")
        if target in repositories:
            state = repositories[target]["evidence_state"]
        actions.append(
            {
                "action_id": row.get("action_id"),
                "action_type": action_type,
                "compatibility_action_type": row.get("action_type"),
                "target_repository": target,
                "target_revision": row.get("target_revision"),
                "evidence_state": state,
                "scope": deepcopy(row.get("scope", {})),
                "evidence": _evidence(row.get("evidence"), fallback),
                "rationale": str(row.get("reason") or "Step 5 recommendation"),
                "completion_condition": row.get("completion_condition"),
                "draft_eligible": state in AUTOMATIC_DRAFT_STATES
                and action_type in {"update_existing_test", "add_missing_test"},
            }
        )

    supplied = agent_analysis or {}
    for row in supplied.get("repository_impacts", []):
        if not isinstance(row, Mapping):
            continue
        repository = str(row.get("repository") or "")
        if not repository:
            continue
        repositories[repository] = deepcopy(dict(row))
    if isinstance(supplied.get("actions"), list) and supplied["actions"]:
        actions = [
            deepcopy(dict(row))
            for row in supplied["actions"]
            if isinstance(row, Mapping)
        ]

    ordered_repositories = sorted(
        repositories.values(),
        key=lambda row: (
            row.get("rank") if isinstance(row.get("rank"), int) else 1_000_000,
            str(row.get("repository")),
        ),
    )
    for index, row in enumerate(ordered_repositories, 1):
        if not isinstance(row.get("rank"), int):
            row["rank"] = index
    gaps = sorted(
        {
            str(value)
            for report in (step2, step3, step4, step5)
            for value in report.get("gaps", [])
        }
        | {str(value) for value in supplied.get("gaps", [])}
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "partial" if gaps else "complete",
        "run_context_sha256": run_context["context_sha256"],
        "source": deepcopy(run_context["source"]),
        "repository_impacts": ordered_repositories,
        "coverage": deepcopy(supplied.get("coverage", step4.get("coverage", {}))),
        "actions": actions,
        "gaps": gaps,
        "recommendation": supplied.get("recommendation")
        or "Review ranked impacts and execute eligible test remediation.",
        "tool_calls": [deepcopy(dict(row)) for row in tool_calls or []],
        "provenance": {
            "artifacts": artifacts,
            "agent": deepcopy(supplied.get("agent", {"status": "not_run"})),
            "candidate_repositories": [
                row["repository"]
                for row in run_context["candidate_repositories"]
                if isinstance(row, Mapping) and row.get("repository")
            ],
            "read_only": True,
            "github_writes": "none",
            "catalog_mutation": "none",
        },
    }
    report["report_sha256"] = artifact_sha256(report)
    errors = validate_analysis_report(report)
    if errors:
        raise AnalysisReportError("invalid analysis report: " + "; ".join(errors))
    return report


def _has_evidence_binding(
    row: Mapping[str, Any],
    tool_calls: Mapping[str, Mapping[str, Any]],
    *,
    strict: bool,
) -> bool:
    evidence = row.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        call_id = item.get("tool_call_id")
        call = tool_calls.get(str(call_id)) if call_id else None
        if call is not None:
            result = call.get("result")
            result_digest = call.get("result_sha256")
            if (
                isinstance(result, Mapping)
                and isinstance(result_digest, str)
                and artifact_sha256(result) == result_digest
                and (
                    item.get("result_sha256") is None
                    or item.get("result_sha256") == result_digest
                )
            ):
                return True
        if (
            not strict
            and item.get("kind") in {"artifact", "source", "tool"}
            and (item.get("sha256") or item.get("source_revision"))
        ):
            return True
    return False


def _tool_call_map(calls: Any, errors: list[str]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(calls, list):
        errors.append("tool_calls must be a list")
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(calls):
        if not isinstance(row, Mapping):
            errors.append(f"tool_calls[{index}] must be an object")
            continue
        call_id = row.get("tool_call_id")
        digest = row.get("result_sha256")
        tool_result = row.get("result")
        if not isinstance(call_id, str) or not call_id:
            errors.append(f"tool_calls[{index}].tool_call_id is required")
        elif call_id in result:
            errors.append(f"tool_calls contains duplicate tool_call_id: {call_id}")
        else:
            result[call_id] = row
        if not isinstance(tool_result, Mapping) or not isinstance(digest, str):
            errors.append(f"tool_calls[{index}] must retain its result and digest")
        elif artifact_sha256(tool_result) != digest:
            errors.append(f"tool_calls[{index}].result_sha256 does not match result")
    return result


def validate_analysis_report(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return ["analysis report must be an object"]
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if value.get("analysis_kind") != ANALYSIS_KIND:
        errors.append(f"analysis_kind must be {ANALYSIS_KIND}")
    calls = value.get("tool_calls", [])
    call_map = _tool_call_map(calls, errors)
    provenance = value.get("provenance", {})
    agent = provenance.get("agent", {}) if isinstance(provenance, Mapping) else {}
    strict_agent = isinstance(agent, Mapping) and agent.get("status") == "complete"
    allowed_repositories = {
        str(value).lower()
        for value in ([value.get("repository")] if isinstance(value, Mapping) else [])
    }
    if isinstance(provenance, Mapping):
        allowed_repositories.update(
            str(repository).lower()
            for repository in provenance.get("candidate_repositories", [])
        )
    impacts = value.get("repository_impacts")
    if not isinstance(impacts, list):
        errors.append("repository_impacts must be a list")
        impacts = []
    for index, row in enumerate(impacts):
        if not isinstance(row, Mapping):
            errors.append(f"repository_impacts[{index}] must be an object")
            continue
        state = row.get("evidence_state")
        if state not in EVIDENCE_STATES:
            errors.append(f"repository_impacts[{index}].evidence_state is invalid")
        if (
            strict_agent
            and str(row.get("repository", "")).lower() not in allowed_repositories
        ):
            errors.append(
                f"repository_impacts[{index}].repository is outside captured scope"
            )
        if state in AUTOMATIC_DRAFT_STATES and not _has_evidence_binding(
            row, call_map, strict=strict_agent
        ):
            errors.append(f"repository_impacts[{index}] lacks bound evidence")
    actions = value.get("actions")
    if not isinstance(actions, list):
        errors.append("actions must be a list")
        actions = []
    for index, row in enumerate(actions):
        if not isinstance(row, Mapping):
            errors.append(f"actions[{index}] must be an object")
            continue
        if row.get("action_type") not in set(ACTION_ALIASES.values()):
            errors.append(f"actions[{index}].action_type is invalid")
        state = row.get("evidence_state")
        if state not in EVIDENCE_STATES:
            errors.append(f"actions[{index}].evidence_state is invalid")
        if (
            strict_agent
            and str(row.get("target_repository", "")).lower()
            not in allowed_repositories
        ):
            errors.append(
                f"actions[{index}].target_repository is outside captured scope"
            )
        expected = state in AUTOMATIC_DRAFT_STATES and row.get("action_type") in {
            "update_existing_test",
            "add_missing_test",
        }
        if row.get("draft_eligible") is not expected:
            errors.append(f"actions[{index}].draft_eligible is inconsistent")
        if expected and not _has_evidence_binding(row, call_map, strict=strict_agent):
            errors.append(f"actions[{index}] lacks bound evidence")
    digest = value.get("report_sha256")
    unsigned = dict(value)
    unsigned.pop("report_sha256", None)
    if (
        not isinstance(digest, str)
        or not SHA256.fullmatch(digest)
        or artifact_sha256(unsigned) != digest
    ):
        errors.append("report_sha256 does not match report")
    return errors


__all__ = [
    "ACTION_ALIASES",
    "ANALYSIS_KIND",
    "EVIDENCE_STATES",
    "AnalysisReportError",
    "build_analysis_report",
    "validate_analysis_report",
]
