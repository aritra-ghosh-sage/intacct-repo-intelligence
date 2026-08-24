"""Categorized validation summaries for read-only Greenfield replay."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_INCOMPLETE_STATES = {
    "ambiguous",
    "dynamic",
    "not_analyzed",
    "not_modelled",
    "not_run",
    "unavailable",
    "unresolved",
}
_SUCCESSFUL_EXECUTION_RESULTS = {"passed"}


def _surface_has_incomplete_state(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("status") in _INCOMPLETE_STATES:
            return True
        if value.get("classification") in _INCOMPLETE_STATES:
            return True
        return any(_surface_has_incomplete_state(item) for item in value.values())
    if isinstance(value, list):
        return any(_surface_has_incomplete_state(item) for item in value)
    return False


def _impact_status(step3: Mapping[str, Any] | None) -> str:
    if not isinstance(step3, Mapping):
        return "unavailable"
    if (
        step3.get("status") == "complete"
        and not step3.get("gaps")
        and not _surface_has_incomplete_state(step3)
    ):
        return "complete"
    return "partial"


def _coverage_status(step4: Mapping[str, Any] | None) -> str:
    if not isinstance(step4, Mapping):
        return "unavailable"
    coverage = step4.get("coverage")
    obligations = step4.get("obligations")
    coverage_items = coverage.get("items") if isinstance(coverage, Mapping) else None
    obligation_items = (
        obligations.get("items") if isinstance(obligations, Mapping) else None
    )
    executed_coverage = isinstance(coverage_items, list) and all(
        isinstance(item, Mapping)
        and item.get("classification") in {"covered", "indirectly_covered"}
        and item.get("execution_result") in _SUCCESSFUL_EXECUTION_RESULTS
        and isinstance(item.get("evidence"), list)
        and any(
            isinstance(evidence, Mapping)
            and evidence.get("kind") in {"ci", "workflow", "check_run"}
            for evidence in item["evidence"]
        )
        for item in coverage_items
    )
    satisfied_obligations = isinstance(obligation_items, list) and all(
        isinstance(item, Mapping) and item.get("status") == "satisfied"
        for item in obligation_items
    )
    complete = (
        step4.get("status") == "complete"
        and not step4.get("gaps")
        and isinstance(coverage, Mapping)
        and coverage.get("status") == "available"
        and isinstance(obligations, Mapping)
        and obligations.get("status") == "available"
        and bool(coverage_items)
        and bool(obligation_items)
        and executed_coverage
        and satisfied_obligations
    )
    return "complete" if complete else "partial"


def validation_summary(
    *,
    artifact_integrity: str,
    provenance_revision_consistency: str,
    step3: Mapping[str, Any] | None,
    step4: Mapping[str, Any] | None,
    step7: Mapping[str, Any] | None = None,
    runtime_status: str | None = None,
    runtime_reason: str | None = None,
) -> dict[str, Any]:
    """Return status categories without upgrading evidence classifications."""

    if step7 is None:
        runtime = {
            "status": runtime_status or "unavailable",
            "reason": runtime_reason or "step7_inputs_unavailable",
        }
        eligibility = {
            "status": "not_eligible",
            "pr_eligible": False,
            "reason": "step7_not_validated",
        }
    else:
        status = step7.get("status")
        runtime = {
            "status": "passed" if status == "validated" else status or "unavailable",
        }
        if status != "validated":
            runtime["reason"] = "step7_validation_not_passed"
        eligibility = {
            "status": "eligible"
            if step7.get("pr_eligible") is True and status == "validated"
            else "not_eligible",
            "pr_eligible": step7.get("pr_eligible") is True and status == "validated",
            "reason": "step7_validated"
            if status == "validated"
            else "step7_not_validated",
        }
    return {
        "artifact_integrity": {"status": artifact_integrity},
        "provenance_revision_consistency": {"status": provenance_revision_consistency},
        "impact_completeness": {"status": _impact_status(step3)},
        "coverage_completeness": {"status": _coverage_status(step4)},
        "runtime_validation": runtime,
        "pr_eligibility": eligibility,
    }


__all__ = ["validation_summary"]
