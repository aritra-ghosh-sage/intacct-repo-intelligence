"""Validated, evidence-bound lifecycle artifacts for Greenfield planners."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from greenfield.artifact_io import artifact_sha256

SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_analysis_plan"
TASK_TYPES = frozenset(
    {
        "inspect_changed_behavior",
        "verify_contract_target",
        "screen_repository",
        "trace_dependency",
        "inspect_test_inventory",
        "verify_ci_execution",
        "assess_coverage_gap",
        "challenge_claim",
        "synthesize_review",
    }
)
DECISIONS = frozenset({"continue", "replan", "complete", "block"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PlanningContractError(ValueError):
    """Raised when a planner lifecycle loses its captured-evidence binding."""


def build_planning_report(
    run_context: Mapping[str, Any],
    *,
    mode: str,
    planner: Mapping[str, Any],
    cycles: list[Mapping[str, Any]],
    status: str,
    stop_reason: str,
    gaps: list[str] | None = None,
    analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a hash-verified planner artifact; it is never impact evidence itself."""

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": status,
        "mode": mode,
        "source": dict(run_context["source"]),
        "run_context_sha256": run_context["context_sha256"],
        "planner": dict(planner),
        "cycles": [dict(row) for row in cycles],
        "gaps": sorted(set(gaps or [])),
        "stop_reason": stop_reason,
        "provenance": {
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
        },
    }
    if analysis is not None:
        report["analysis"] = dict(analysis)
    report["planning_sha256"] = artifact_sha256(report)
    errors = validate_planning_report(report)
    if errors:
        raise PlanningContractError("invalid planning report: " + "; ".join(errors))
    return report


def validate_planning_report(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return ["planning report must be an object"]
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if value.get("analysis_kind") != ANALYSIS_KIND:
        errors.append(f"analysis_kind must be {ANALYSIS_KIND}")
    if value.get("status") not in {"complete", "partial", "blocked", "unavailable"}:
        errors.append("status is invalid")
    if value.get("mode") not in {"default", "shadow", "active"}:
        errors.append("mode is invalid")
    if not isinstance(value.get("run_context_sha256"), str) or not SHA256.fullmatch(
        str(value.get("run_context_sha256"))
    ):
        errors.append("run_context_sha256 is invalid")
    if not isinstance(value.get("planner"), Mapping):
        errors.append("planner must be an object")
    cycles = value.get("cycles")
    if not isinstance(cycles, list):
        errors.append("cycles must be a list")
        cycles = []
    seen: set[str] = set()
    completed: set[str] = set()
    synthesis_ids: set[str] = set()
    challenge_ids: set[str] = set()
    for index, cycle in enumerate(cycles):
        if not isinstance(cycle, Mapping):
            errors.append(f"cycles[{index}] must be an object")
            continue
        task = cycle.get("task")
        if not isinstance(task, Mapping):
            errors.append(f"cycles[{index}].task must be an object")
            continue
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            errors.append(f"cycles[{index}].task.task_id is required")
        elif task_id in seen:
            errors.append(f"duplicate planner task_id: {task_id}")
        else:
            seen.add(task_id)
        if task.get("task_type") not in TASK_TYPES:
            errors.append(f"cycles[{index}].task.task_type is invalid")
        if not isinstance(task.get("question"), str) or not task["question"].strip():
            errors.append(f"cycles[{index}].task.question is required")
        if cycle.get("decision") not in DECISIONS:
            errors.append(f"cycles[{index}].decision is invalid")
        cycle_status = cycle.get("status", "complete")
        if cycle_status not in {"complete", "partial", "unavailable", "failed"}:
            errors.append(f"cycles[{index}].status is invalid")
        if cycle_status == "complete" and isinstance(task_id, str):
            completed.add(task_id)
        if task.get("task_type") == "synthesize_review" and isinstance(task_id, str):
            synthesis_ids.add(task_id)
        if task.get("task_type") == "challenge_claim" and isinstance(task_id, str):
            challenge_ids.add(task_id)
        references = cycle.get("evidence_refs", [])
        if not isinstance(references, list):
            errors.append(f"cycles[{index}].evidence_refs must be a list")
        else:
            for ref in references:
                if not isinstance(ref, Mapping) or not isinstance(
                    ref.get("tool_call_id"), str
                ):
                    errors.append(
                        f"cycles[{index}].evidence_refs requires tool_call_id"
                    )
                elif ref.get("result_sha256") is not None and (
                    not isinstance(ref["result_sha256"], str)
                    or not SHA256.fullmatch(ref["result_sha256"])
                ):
                    errors.append(
                        f"cycles[{index}].evidence_refs has invalid result_sha256"
                    )
    analysis = value.get("analysis")
    if value.get("status") in {"complete", "partial"} or analysis is not None:
        if not isinstance(analysis, Mapping):
            errors.append("completed or partial planning reports require analysis")
        else:
            for field in ("repository_impacts", "actions", "gaps"):
                if not isinstance(analysis.get(field), list):
                    errors.append(f"analysis.{field} must be a list")
            if not isinstance(analysis.get("agent"), Mapping):
                errors.append("analysis.agent must be an object")
            for index, row in enumerate(analysis.get("repository_impacts", [])):
                if not isinstance(row, Mapping):
                    errors.append(
                        f"analysis.repository_impacts[{index}] must be an object"
                    )
                    continue
                if row.get("evidence_state") in {"confirmed", "strong_candidate"}:
                    if value.get("status") != "complete":
                        errors.append(
                            f"incomplete planning report retains automatic impact claim: {index}"
                        )
                        continue
                    expected = f"challenge-impact-{row.get('repository')}"
                    if expected not in completed:
                        errors.append(
                            f"analysis impact lacks completed challenge: {expected}"
                        )
                    elif not any(
                        isinstance(cycle.get("evidence_refs"), list)
                        and cycle.get("task", {}).get("task_id") == expected
                        and cycle.get("evidence_refs")
                        for cycle in cycles
                        if isinstance(cycle, Mapping)
                    ):
                        errors.append(
                            f"challenge task lacks toolbox evidence: {expected}"
                        )
            for index, row in enumerate(analysis.get("actions", [])):
                if not isinstance(row, Mapping):
                    errors.append(f"analysis.actions[{index}] must be an object")
                    continue
                if row.get("draft_eligible") is True:
                    if value.get("status") != "complete":
                        errors.append(
                            f"incomplete planning report retains draft action: {index}"
                        )
                        continue
                    expected = f"challenge-action-{row.get('action_id')}"
                    if expected not in completed:
                        errors.append(
                            f"analysis action lacks completed challenge: {expected}"
                        )
                    elif not any(
                        isinstance(cycle.get("evidence_refs"), list)
                        and cycle.get("task", {}).get("task_id") == expected
                        and cycle.get("evidence_refs")
                        for cycle in cycles
                        if isinstance(cycle, Mapping)
                    ):
                        errors.append(
                            f"challenge task lacks toolbox evidence: {expected}"
                        )
    if value.get("status") == "complete":
        if not synthesis_ids:
            errors.append("complete planning report requires synthesis")
        if not synthesis_ids.issubset(completed):
            errors.append("mandatory synthesis task did not complete")
        if not challenge_ids.issubset(completed):
            errors.append("challenge task did not complete")
    unsigned = dict(value)
    digest = unsigned.pop("planning_sha256", None)
    if (
        not isinstance(digest, str)
        or not SHA256.fullmatch(digest)
        or artifact_sha256(unsigned) != digest
    ):
        errors.append("planning_sha256 does not match report")
    return errors


__all__ = [
    "ANALYSIS_KIND",
    "DECISIONS",
    "TASK_TYPES",
    "PlanningContractError",
    "build_planning_report",
    "validate_planning_report",
]
