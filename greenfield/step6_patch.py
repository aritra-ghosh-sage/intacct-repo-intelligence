"""Deterministic Greenfield Step 6 test-patch and AI-PR handoff generation."""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from greenfield.step4_contract import artifact_sha256 as step4_artifact_sha256
from greenfield.step4_contract import validate_step4_report
from greenfield.step5_actions import validate_step5_report
from greenfield.step6_contract import (
    APPROVAL_ROLES,
    REPORT_ANALYSIS_KIND,
    REPORT_SCHEMA_VERSION,
    RULE_SET_VERSION,
    Step6Error,
    artifact_sha256,
    sha256_bytes,
    validate_step6_request,
)
from greenfield.step6_templates import validate_template
from scripts.validate_greenfield_step1 import validate as validate_step1
from scripts.validate_greenfield_step3 import validate as validate_step3

SUPPORTED_ACTIONS = {"update_test_obligation"}
SUPPORTED_TRIGGERS = {"fixture_contract_mismatch", "api_or_schema_changed"}


def _approved_roles(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item.get("role"))
        for item in value
        if isinstance(item, Mapping) and item.get("status") == "approved"
    }


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Step6Error(f"{label} must be a non-empty string")
    return value.strip()


def _upstream_context(
    request: Mapping[str, Any],
    step1: Mapping[str, Any],
    step3: Mapping[str, Any],
    step4: Mapping[str, Any],
    step5: Mapping[str, Any],
) -> None:
    validators = (
        (validate_step1, step1, "Step 1"),
        (validate_step3, step3, "Step 3"),
        (validate_step4_report, step4, "Step 4"),
        (validate_step5_report, step5, "Step 5"),
    )
    for validator, report, label in validators:
        errors = validator(report)
        if errors:
            raise Step6Error(f"invalid {label} report: " + "; ".join(errors))

    upstream = request["upstream"]
    expected_hashes = {
        "step1_report_sha256": artifact_sha256(step1),
        "step3_report_sha256": artifact_sha256(step3),
        "step4_report_sha256": step4_artifact_sha256(step4),
        "step5_report_sha256": artifact_sha256(step5),
    }
    for field, expected in expected_hashes.items():
        if upstream[field] != expected:
            raise Step6Error(f"upstream.{field} does not match supplied report")

    source = request["source"]
    step1_input = step1["input"]
    step3_input = step3["input"]
    step4_input = step4["input"]
    if source["repository"] not in {
        step1_input.get("repository"),
        step1_input.get("repo_key"),
    }:
        raise Step6Error("source.repository does not match Step 1")
    if source["head_revision"] != step1_input.get("head_sha"):
        raise Step6Error("source.head_revision does not match Step 1")
    if source["base_revision"] != step1_input.get("base_sha"):
        raise Step6Error("source.base_revision does not match Step 1")
    if source["changed_paths"] != step1_input.get("changed_paths"):
        raise Step6Error("source.changed_paths do not match Step 1")
    for label, report_input in (("Step 3", step3_input), ("Step 4", step4_input)):
        if report_input.get("source_repository") != source["repository"]:
            raise Step6Error(f"source.repository does not match {label}")
        if report_input.get("target_revision") != source["head_revision"]:
            raise Step6Error(f"source.head_revision does not match {label}")

    action_id = request["action"]["action_id"]
    actions = [item for item in step5["actions"] if item.get("action_id") == action_id]
    if len(actions) != 1:
        raise Step6Error("action.action_id must identify exactly one Step 5 action")
    action = actions[0]
    for field in ("action_type", "target_repository"):
        if action.get(field) != request["action"][field]:
            raise Step6Error(f"action.{field} does not match Step 5")
    if (
        action.get("scope", {}).get("test_id") is not None
        and action.get("scope", {}).get("test_id") != request["action"]["test_id"]
    ):
        raise Step6Error("action.test_id does not match Step 5")
    if (
        action.get("scope", {}).get("test_path") is not None
        and action.get("scope", {}).get("test_path") != request["action"]["test_path"]
    ):
        raise Step6Error("action.test_path does not match Step 5")


def _apply_operations(request: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    files = {
        str(item["path"]): str(item["content"]) for item in request["target"]["files"]
    }
    for operation in request["edit_operations"]:
        path = str(operation["path"])
        content = files[path]
        old_text = str(operation["old_text"])
        count = content.count(old_text)
        if count != 1:
            raise Step6Error(
                f"edit fragment must occur exactly once in {path}; observed {count}"
            )
        files[path] = content.replace(old_text, str(operation["new_text"]), 1)
    return {
        path: {"before": before, "after": files[path]}
        for path, before in {
            str(item["path"]): str(item["content"])
            for item in request["target"]["files"]
        }.items()
        if files[path] != before
    }


def _unified_diff(changes: Mapping[str, Mapping[str, str]]) -> str:
    chunks: list[str] = []
    for path in sorted(changes):
        before = changes[path]["before"].splitlines(keepends=True)
        after = changes[path]["after"].splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    return "".join(chunks)


def _base_report(
    request: Mapping[str, Any],
    *,
    status: str,
    reason: str,
    files: list[dict[str, Any]] | None = None,
    unified_diff: str = "",
) -> dict[str, Any]:
    source = request["source"]
    target = request["target"]
    template = request["template"]
    patch_sha = sha256_bytes(unified_diff.encode("utf-8"))
    action = request["action"]
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": REPORT_ANALYSIS_KIND,
        "status": status,
        "eligibility_profile": "step7"
        if request.get("_step7_eligibility") is True
        else "replay",
        "proposal_id": "0" * 64,
        "reason": reason,
        "source": {
            "repository": source["repository"],
            "pr_number": source["pr_number"],
            **({"pr_url": source["pr_url"]} if source.get("pr_url") else {}),
            "base_revision": source["base_revision"],
            "head_revision": source["head_revision"],
            "changed_paths": source["changed_paths"],
            "diff": source["diff"],
            "diff_sha256": source["diff_sha256"],
        },
        "target": {
            "repository": target["repository"],
            "base_revision": target["base_revision"],
            "allowed_paths": target["allowed_paths"],
        },
        "justification": {
            "trigger": request["trigger"]["kind"],
            "step5_action_id": action["action_id"],
            "interface_id": action["interface_id"],
            "test_id": action["test_id"],
            "test_path": action["test_path"],
            "evidence": request["trigger"]["evidence"],
        },
        "patch": {
            "format": "unified_diff",
            "unified_diff": unified_diff,
            "files": files or [],
            "patch_sha256": patch_sha,
            "generator": {"id": template["id"], "version": template["version"]},
        },
        "pr_request": {
            "draft": True,
            "title": f"Update tests for {action['interface_id']}",
            "body_sections": [
                "Source change and deterministic justification",
                "Generated test changes",
                "Validation requirements",
                "Evidence and remaining uncertainty",
            ],
        },
        "validation_plan": request["validation_plan"],
        "agent_policy": {
            "must_use_target_base_revision": True,
            "must_change_only_allowed_paths": True,
            "must_not_change_target_base_revision": True,
            "must_not_add_files": True,
            "must_not_alter_evidence": True,
            "must_not_merge": True,
            "must_not_approve": True,
        },
        "provenance": {
            **request["upstream"],
            "rule_set_version": RULE_SET_VERSION,
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
        },
    }
    if "approvals" in request:
        report["approvals"] = deepcopy(request["approvals"])
    if "target_evidence" in request:
        report["target_evidence"] = deepcopy(request["target_evidence"])
        report["target"]["files"] = deepcopy(target.get("files", []))
    report["idempotency_key"] = artifact_sha256(
        {
            "source_repository": source["repository"],
            "pr_number": source["pr_number"],
            "source_revision": source["head_revision"],
            "target_repository": target["repository"],
            "target_revision": target["base_revision"],
            "template": template,
            "patch_sha256": patch_sha,
        }
    )
    unsigned = deepcopy(report)
    unsigned.pop("proposal_id")
    report["proposal_id"] = artifact_sha256(unsigned)
    return report


def generate_step6(
    request: Mapping[str, Any],
    step1: Mapping[str, Any],
    step3: Mapping[str, Any],
    step4: Mapping[str, Any],
    step5: Mapping[str, Any],
    *,
    strict_target_evidence: bool = False,
    require_approvals: bool = False,
) -> dict[str, Any]:
    errors = validate_step6_request(
        request, strict_target_evidence=strict_target_evidence
    )
    if errors:
        raise Step6Error("invalid Step 6 request: " + "; ".join(errors))
    _upstream_context(request, step1, step3, step4, step5)
    action = request["action"]
    if require_approvals:
        missing_roles = sorted(
            APPROVAL_ROLES - _approved_roles(request.get("approvals"))
        )
        if missing_roles:
            return _base_report(
                request,
                status="blocked",
                reason="owner_approval_pending:" + ",".join(missing_roles),
            )
    if action["status"] != "recommended":
        return _base_report(request, status="blocked", reason="step5_action_blocked")
    if action["action_type"] not in SUPPORTED_ACTIONS:
        return _base_report(
            request, status="not_generated", reason="action_not_supported_in_v1"
        )
    if request["trigger"]["kind"] not in SUPPORTED_TRIGGERS:
        return _base_report(
            request, status="not_generated", reason="trigger_not_supported_in_v1"
        )
    try:
        validate_template(request)
        changes = _apply_operations(request)
        if not changes:
            return _base_report(
                request, status="not_generated", reason="edit_produced_no_change"
            )
        file_rows = [
            {
                "path": path,
                "status": "modified",
                "before": changes[path]["before"],
                "after": changes[path]["after"],
                "before_sha256": sha256_bytes(changes[path]["before"].encode("utf-8")),
                "after_sha256": sha256_bytes(changes[path]["after"].encode("utf-8")),
            }
            for path in sorted(changes)
        ]
        return _base_report(
            request,
            status="ready_for_ai_pr",
            reason="deterministic_template_generated_patch",
            files=file_rows,
            unified_diff=_unified_diff(changes),
        )
    except Step6Error:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return _base_report(request, status="generation_failed", reason=str(exc))


__all__ = ["generate_step6"]
