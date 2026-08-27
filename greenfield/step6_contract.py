"""Contracts and validation helpers for Greenfield PR-impact Step 6."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPORT_SCHEMA_VERSION = "0.1"
REQUEST_SCHEMA_VERSION = "0.1"
REPORT_ANALYSIS_KIND = "greenfield_pr_impact_step_6"
REQUEST_ANALYSIS_KIND = "greenfield_pr_impact_step_6_request"
RULE_SET_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
STATUSES = {"ready_for_ai_pr", "blocked", "not_generated", "generation_failed"}
TRIGGERS = {
    "fixture_contract_mismatch",
    "api_or_schema_changed",
    "required_test_category_missing",
    "renamed_or_removed_symbol",
    "confirmed_compatibility_failure",
}
ACTION_TYPES = {
    "update_test_obligation",
    "add_integration_test",
    "run_test_suite",
    "request_owner_review",
    "block_propagation",
}
TEMPLATE_IDS = {
    "gwdata_gl_existing_case_update_v1",
    "restapi_existing_case_update_v1",
    "strands_bounded_test_edit_v1",
}
TARGET_EVIDENCE_PROVIDERS = {"github_git_api", "git_object_database"}
APPROVAL_ROLES = {"source_interface_owner", "consumer_test_owner"}
ELIGIBILITY_PROFILES = {"replay", "step7"}


class Step6Error(ValueError):
    """Raised when Step 6 evidence cannot safely be evaluated."""


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def artifact_sha256(value: object) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Step6Error(f"{label} must be a non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA.fullmatch(result):
        raise Step6Error(f"{label} must be a lowercase 40-character SHA")
    return result


def _sha256(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA256.fullmatch(result):
        raise Step6Error(f"{label} must be a lowercase SHA-256")
    return result


def _path(value: Any, label: str) -> str:
    result = _text(value, label)
    path = Path(result)
    if path.is_absolute() or ".." in path.parts or "*" in result or "?" in result:
        raise Step6Error(f"{label} must be an exact safe relative path")
    return result


def _sorted_unique_strings(
    value: Any, label: str, *, paths: bool = False, allow_empty: bool = False
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        required = "" if allow_empty else "non-empty "
        raise Step6Error(f"{label} must be a {required}list")
    result = [
        (_path(item, f"{label} item") if paths else _text(item, f"{label} item"))
        for item in value
    ]
    if result != sorted(set(result)):
        raise Step6Error(f"{label} must be sorted and unique")
    return result


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Step6Error(f"{label} must be an object")
    return value


def _validate_file_rows(
    files: Any, label: str, *, allow_empty: bool = False
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(files, list) or (not allow_empty and not files):
        required = "" if allow_empty else "non-empty "
        raise Step6Error(f"{label} must be a {required}list")
    rows: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(files):
        row = _object(item, f"{label}[{index}]")
        path = _path(row.get("path"), f"{label}[{index}].path")
        if path in rows:
            raise Step6Error(f"duplicate target file: {path}")
        content = row.get("content")
        if not isinstance(content, str):
            raise Step6Error(f"{label}[{index}].content must be a string")
        digest = _sha256(row.get("sha256"), f"{label}[{index}].sha256")
        if sha256_bytes(content.encode("utf-8")) != digest:
            raise Step6Error(f"{label}[{index}].sha256 does not match content")
        rows[path] = row
    if list(rows) != sorted(rows):
        raise Step6Error(f"{label} must be deterministically ordered")
    return rows


def _synthetic_sha(value: str) -> bool:
    return len(set(value)) <= 1


def _validate_approvals(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [f"{label} must be a list"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        row = item if isinstance(item, Mapping) else {}
        role = row.get("role")
        if role not in APPROVAL_ROLES:
            errors.append(f"{label}[{index}].role is invalid")
        elif role in seen:
            errors.append(f"{label} contains duplicate role: {role}")
        else:
            seen.add(role)
        if row.get("status") not in {"approved", "pending", "unavailable"}:
            errors.append(f"{label}[{index}].status is invalid")
        if row.get("status") == "approved" and (
            not isinstance(row.get("approver"), str) or not row["approver"].strip()
        ):
            errors.append(f"{label}[{index}].approver is required when approved")
        if row.get("status") == "approved":
            evidence = row.get("approval_evidence")
            if not isinstance(evidence, Mapping):
                errors.append(
                    f"{label}[{index}].approval_evidence is required when approved"
                )
            else:
                if (
                    not isinstance(evidence.get("provider"), str)
                    or not evidence["provider"].strip()
                ):
                    errors.append(
                        f"{label}[{index}].approval_evidence.provider is required"
                    )
                if (
                    not isinstance(evidence.get("record_id"), str)
                    or not evidence["record_id"].strip()
                ):
                    errors.append(
                        f"{label}[{index}].approval_evidence.record_id is required"
                    )
                if not isinstance(evidence.get("sha256"), str) or not SHA256.fullmatch(
                    evidence["sha256"]
                ):
                    errors.append(
                        f"{label}[{index}].approval_evidence.sha256 is invalid"
                    )
            approval_digest = row.get("approval_sha256")
            if not isinstance(approval_digest, str) or not SHA256.fullmatch(
                approval_digest
            ):
                errors.append(f"{label}[{index}].approval_sha256 is invalid")
            elif (
                artifact_sha256(
                    {
                        "role": row.get("role"),
                        "status": row.get("status"),
                        "approver": row.get("approver"),
                        "approval_evidence": row.get("approval_evidence"),
                    }
                )
                != approval_digest
            ):
                errors.append(
                    f"{label}[{index}].approval_sha256 does not match approval"
                )
    return errors


def _validate_target_evidence(
    value: Any, target: Mapping[str, Any], label: str, *, strict: bool
) -> list[str]:
    if value is None:
        return [f"{label} is required in strict evidence mode"] if strict else []
    if not isinstance(value, Mapping):
        return [f"{label} must be an object"]
    errors: list[str] = []
    if value.get("provider") not in TARGET_EVIDENCE_PROVIDERS:
        errors.append(
            f"{label}.provider must be one of {sorted(TARGET_EVIDENCE_PROVIDERS)}"
        )
    if value.get("repository") != target.get("repository"):
        errors.append(f"{label}.repository must match target.repository")
    revision = value.get("revision")
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        errors.append(f"{label}.revision must be a lowercase 40-character SHA")
    elif revision != target.get("base_revision"):
        errors.append(f"{label}.revision must match target.base_revision")
    elif _synthetic_sha(revision):
        errors.append(f"{label}.revision must not be synthetic")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        errors.append(f"{label}.files must be a non-empty list")
        files = []
    target_rows = target.get("files", [])
    if not isinstance(target_rows, list):
        target_rows = []
    if not target_rows:
        patch = target.get("patch_files", [])
        target_rows = patch if isinstance(patch, list) else []
    target_files = {
        row.get("path"): row for row in target_rows if isinstance(row, Mapping)
    }
    paths: list[str] = []
    for index, item in enumerate(files):
        row = item if isinstance(item, Mapping) else {}
        path = row.get("path")
        if not isinstance(path, str) or not path.strip():
            errors.append(f"{label}.files[{index}].path is required")
            continue
        paths.append(path)
        if path not in target_files:
            errors.append(f"{label}.files[{index}].path is not in target.files: {path}")
        digest = row.get("content_sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append(f"{label}.files[{index}].content_sha256 is invalid")
        elif target_files.get(path, {}).get("sha256") != digest:
            errors.append(
                f"{label}.files[{index}].content_sha256 does not match target file"
            )
        if (
            not isinstance(row.get("blob_or_response_id"), str)
            or not row["blob_or_response_id"].strip()
        ):
            errors.append(f"{label}.files[{index}].blob_or_response_id is required")
    if paths != sorted(set(paths)):
        errors.append(f"{label}.files must be sorted and unique")
    if set(paths) != set(target_files):
        errors.append(f"{label}.files must exactly match target.files")
    supplied = dict(value)
    supplied.pop("evidence_sha256", None)
    digest = value.get("evidence_sha256")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append(f"{label}.evidence_sha256 must be SHA-256")
    elif artifact_sha256(supplied) != digest:
        errors.append(f"{label}.evidence_sha256 does not match evidence")
    return errors


def _approved_roles(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item.get("role"))
        for item in value
        if isinstance(item, Mapping) and item.get("status") == "approved"
    }


def validate_step6_request(
    request: Any, *, strict_target_evidence: bool = False
) -> list[str]:
    try:
        _validate_request(request, strict_target_evidence=strict_target_evidence)
    except Step6Error as exc:
        return [str(exc)]
    return []


def _validate_request(request: Any, *, strict_target_evidence: bool = False) -> None:
    root = _object(request, "request")
    if root.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise Step6Error(f"schema_version must be {REQUEST_SCHEMA_VERSION}")
    if root.get("analysis_kind") != REQUEST_ANALYSIS_KIND:
        raise Step6Error(f"analysis_kind must be {REQUEST_ANALYSIS_KIND}")

    source = _object(root.get("source"), "source")
    _text(source.get("repository"), "source.repository")
    pr_number = source.get("pr_number")
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
        raise Step6Error("source.pr_number must be a positive integer")
    if source.get("pr_url") is not None:
        _text(source.get("pr_url"), "source.pr_url")
    _sha(source.get("base_revision"), "source.base_revision")
    _sha(source.get("head_revision"), "source.head_revision")
    _sorted_unique_strings(
        source.get("changed_paths"), "source.changed_paths", paths=True
    )
    diff = source.get("diff")
    if not isinstance(diff, str) or not diff:
        raise Step6Error("source.diff must be a non-empty string")
    if sha256_bytes(diff.encode("utf-8")) != _sha256(
        source.get("diff_sha256"), "source.diff_sha256"
    ):
        raise Step6Error("source.diff_sha256 does not match source.diff")

    upstream = _object(root.get("upstream"), "upstream")
    for name in (
        "step1_report_sha256",
        "step3_report_sha256",
        "step4_report_sha256",
        "step5_report_sha256",
    ):
        _sha256(upstream.get(name), f"upstream.{name}")

    action = _object(root.get("action"), "action")
    _sha256(action.get("action_id"), "action.action_id")
    if action.get("action_type") not in ACTION_TYPES:
        raise Step6Error("action.action_type is invalid")
    if action.get("status") not in {"recommended", "blocked"}:
        raise Step6Error("action.status must be recommended or blocked")
    _text(action.get("target_repository"), "action.target_repository")
    _text(action.get("interface_id"), "action.interface_id")
    _text(action.get("test_id"), "action.test_id")
    _path(action.get("test_path"), "action.test_path")

    trigger = _object(root.get("trigger"), "trigger")
    if trigger.get("kind") not in TRIGGERS:
        raise Step6Error("trigger.kind is invalid")
    evidence = trigger.get("evidence")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(not isinstance(item, Mapping) for item in evidence)
    ):
        raise Step6Error("trigger.evidence must be a non-empty list of objects")

    target = _object(root.get("target"), "target")
    target_repository = _text(target.get("repository"), "target.repository")
    if target_repository != action.get("target_repository"):
        raise Step6Error("target.repository must match action.target_repository")
    _sha(target.get("base_revision"), "target.base_revision")
    unsupported_action = action.get("action_type") != "update_test_obligation"
    files = _validate_file_rows(
        target.get("files"), "target.files", allow_empty=unsupported_action
    )
    allowed = _sorted_unique_strings(
        target.get("allowed_paths"),
        "target.allowed_paths",
        paths=True,
        allow_empty=unsupported_action,
    )
    if set(allowed) != set(files):
        raise Step6Error("target.allowed_paths must exactly match target.files")
    target_for_evidence = dict(target)
    patch_for_evidence = root.get("patch")
    if "files" not in target_for_evidence and isinstance(patch_for_evidence, Mapping):
        target_for_evidence["patch_files"] = [
            {
                "path": row.get("path"),
                "sha256": row.get("before_sha256"),
            }
            for row in patch_for_evidence.get("files", [])
            if isinstance(row, Mapping)
        ]
    target_evidence_errors = _validate_target_evidence(
        root.get("target_evidence"),
        target_for_evidence,
        "target_evidence",
        strict=strict_target_evidence,
    )
    if target_evidence_errors:
        raise Step6Error("; ".join(target_evidence_errors))
    approval_errors = _validate_approvals(root.get("approvals"), "approvals")
    if approval_errors:
        raise Step6Error("; ".join(approval_errors))

    template = _object(root.get("template"), "template")
    template_id = _text(template.get("id"), "template.id")
    if template_id not in TEMPLATE_IDS:
        raise Step6Error("template.id is not registered")
    _text(template.get("version"), "template.version")

    operations = root.get("edit_operations")
    if not isinstance(operations, list) or (not operations and not unsupported_action):
        raise Step6Error("edit_operations must be a non-empty list")
    operation_paths: list[str] = []
    for index, item in enumerate(operations):
        operation = _object(item, f"edit_operations[{index}]")
        path = _path(operation.get("path"), f"edit_operations[{index}].path")
        if path not in allowed:
            raise Step6Error(f"edit path is outside allowed_paths: {path}")
        old_text = operation.get("old_text")
        new_text = operation.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise Step6Error(f"edit_operations[{index}].old_text must be non-empty")
        if not isinstance(new_text, str):
            raise Step6Error(f"edit_operations[{index}].new_text must be a string")
        if operation.get("expected_occurrences") != 1:
            raise Step6Error("edit_operations.expected_occurrences must be exactly 1")
        operation_paths.append(path)
        if path not in files:
            raise Step6Error(f"edit path has no target file evidence: {path}")
    if operation_paths != sorted(operation_paths):
        raise Step6Error("edit_operations must be deterministically ordered")

    validation_plan = root.get("validation_plan")
    if not isinstance(validation_plan, list) or any(
        not isinstance(item, str) or not item.strip() for item in validation_plan
    ):
        raise Step6Error("validation_plan must be a list of command/check strings")


def validate_step6_report(
    report: Any,
    *,
    strict_target_evidence: bool = False,
    require_approvals: bool = False,
    require_step7_eligibility: bool = False,
) -> list[str]:
    try:
        _validate_report(
            report,
            strict_target_evidence=strict_target_evidence,
            require_approvals=require_approvals,
            require_step7_eligibility=require_step7_eligibility,
        )
    except Step6Error as exc:
        return [str(exc)]
    return []


def _validate_report(
    report: Any,
    *,
    strict_target_evidence: bool = False,
    require_approvals: bool = False,
    require_step7_eligibility: bool = False,
) -> None:
    root = _object(report, "report")
    if root.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise Step6Error(f"schema_version must be {REPORT_SCHEMA_VERSION}")
    if root.get("analysis_kind") != REPORT_ANALYSIS_KIND:
        raise Step6Error(f"analysis_kind must be {REPORT_ANALYSIS_KIND}")
    status = root.get("status")
    if status not in STATUSES:
        raise Step6Error("status is invalid")
    _text(root.get("reason"), "reason")
    _sha256(root.get("proposal_id"), "proposal_id")
    eligibility = root.get("eligibility_profile", "replay")
    if eligibility not in ELIGIBILITY_PROFILES:
        raise Step6Error("eligibility_profile is invalid")
    if require_step7_eligibility and eligibility != "step7":
        raise Step6Error("report is not marked step7 eligible")

    source = _object(root.get("source"), "source")
    _text(source.get("repository"), "source.repository")
    pr_number = source.get("pr_number")
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
        raise Step6Error("source.pr_number must be a positive integer")
    if source.get("pr_url") is not None:
        _text(source.get("pr_url"), "source.pr_url")
    _sha(source.get("base_revision"), "source.base_revision")
    _sha(source.get("head_revision"), "source.head_revision")
    _sorted_unique_strings(
        source.get("changed_paths"), "source.changed_paths", paths=True
    )
    _sha256(source.get("diff_sha256"), "source.diff_sha256")
    source_diff = source.get("diff")
    if not isinstance(source_diff, str) or not source_diff:
        raise Step6Error("source.diff must be a non-empty string")
    if sha256_bytes(source_diff.encode("utf-8")) != source["diff_sha256"]:
        raise Step6Error("source.diff_sha256 does not match source.diff")
    target = _object(root.get("target"), "target")
    _text(target.get("repository"), "target.repository")
    _sha(target.get("base_revision"), "target.base_revision")
    allowed = _sorted_unique_strings(
        target.get("allowed_paths"),
        "target.allowed_paths",
        paths=True,
        allow_empty=status != "ready_for_ai_pr",
    )
    target_for_evidence = dict(target)
    patch_for_evidence = root.get("patch")
    if "files" not in target_for_evidence and isinstance(patch_for_evidence, Mapping):
        target_for_evidence["patch_files"] = [
            {
                "path": row.get("path"),
                "sha256": row.get("before_sha256"),
            }
            for row in patch_for_evidence.get("files", [])
            if isinstance(row, Mapping)
        ]
    target_evidence_errors = _validate_target_evidence(
        root.get("target_evidence"),
        target_for_evidence,
        "target_evidence",
        strict=strict_target_evidence or require_step7_eligibility,
    )
    if target_evidence_errors:
        raise Step6Error("; ".join(target_evidence_errors))
    approval_errors = _validate_approvals(root.get("approvals"), "approvals")
    if approval_errors:
        raise Step6Error("; ".join(approval_errors))
    if require_approvals and status == "ready_for_ai_pr":
        missing = sorted(APPROVAL_ROLES - _approved_roles(root.get("approvals")))
        if missing:
            raise Step6Error("approved owner roles are missing: " + ", ".join(missing))

    justification = _object(root.get("justification"), "justification")
    _sha256(justification.get("step5_action_id"), "justification.step5_action_id")
    if justification.get("trigger") not in TRIGGERS:
        raise Step6Error("justification.trigger is invalid")
    evidence = justification.get("evidence")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(not isinstance(item, Mapping) for item in evidence)
    ):
        raise Step6Error("justification.evidence must be a non-empty list of objects")

    patch = _object(root.get("patch"), "patch")
    if patch.get("format") != "unified_diff":
        raise Step6Error("patch.format must be unified_diff")
    _sha256(patch.get("patch_sha256"), "patch.patch_sha256")
    template = _object(patch.get("generator"), "patch.generator")
    if template.get("id") not in TEMPLATE_IDS:
        raise Step6Error("patch.generator.id is invalid")
    _text(template.get("version"), "patch.generator.version")
    files = patch.get("files")
    if not isinstance(files, list):
        raise Step6Error("patch.files must be a list")
    paths: list[str] = []
    for index, item in enumerate(files):
        row = _object(item, f"patch.files[{index}]")
        path = _path(row.get("path"), f"patch.files[{index}].path")
        if path not in allowed:
            raise Step6Error(f"patch path is outside allowed_paths: {path}")
        if row.get("status") != "modified":
            raise Step6Error("patch files may only be modified")
        _sha256(row.get("before_sha256"), f"patch.files[{index}].before_sha256")
        _sha256(row.get("after_sha256"), f"patch.files[{index}].after_sha256")
        if not isinstance(row.get("before"), str) or not isinstance(
            row.get("after"), str
        ):
            raise Step6Error("patch file before and after content must be strings")
        if sha256_bytes(row["before"].encode("utf-8")) != row["before_sha256"]:
            raise Step6Error(
                f"patch.files[{index}].before_sha256 does not match content"
            )
        if sha256_bytes(row["after"].encode("utf-8")) != row["after_sha256"]:
            raise Step6Error(
                f"patch.files[{index}].after_sha256 does not match content"
            )
        paths.append(path)
    if paths != sorted(set(paths)):
        raise Step6Error("patch.files must be sorted and unique")

    pr_request = _object(root.get("pr_request"), "pr_request")
    if pr_request.get("draft") is not True:
        raise Step6Error("pr_request.draft must be true")
    _text(pr_request.get("title"), "pr_request.title")
    body_sections = pr_request.get("body_sections")
    if not isinstance(body_sections, list) or any(
        not isinstance(item, str) or not item.strip() for item in body_sections
    ):
        raise Step6Error("pr_request.body_sections must be a list of strings")

    policy = _object(root.get("agent_policy"), "agent_policy")
    for field in (
        "must_use_target_base_revision",
        "must_change_only_allowed_paths",
        "must_not_change_target_base_revision",
        "must_not_add_files",
        "must_not_alter_evidence",
        "must_not_merge",
        "must_not_approve",
    ):
        if policy.get(field) is not True:
            raise Step6Error(f"agent_policy.{field} must be true")

    provenance = _object(root.get("provenance"), "provenance")
    for field in (
        "step1_report_sha256",
        "step3_report_sha256",
        "step4_report_sha256",
        "step5_report_sha256",
    ):
        _sha256(provenance.get(field), f"provenance.{field}")
    if (
        provenance.get("read_only") is not True
        or provenance.get("catalog_mutation") != "none"
        or provenance.get("github_writes") != "none"
    ):
        raise Step6Error(
            "provenance must declare read-only, no catalog mutation, and no GitHub writes"
        )

    if status == "ready_for_ai_pr":
        if not files:
            raise Step6Error("ready_for_ai_pr requires patch files")
        _text(patch.get("unified_diff"), "patch.unified_diff")
    elif files or patch.get("unified_diff"):
        raise Step6Error("non-ready reports must not contain a patch")
    if (
        sha256_bytes(str(patch.get("unified_diff", "")).encode("utf-8"))
        != patch["patch_sha256"]
    ):
        raise Step6Error("patch.patch_sha256 does not match unified_diff")
    expected_diff = _unified_diff_from_files(files)
    if patch.get("unified_diff", "") != expected_diff:
        raise Step6Error("patch.unified_diff does not match patch file contents")

    unsigned = dict(root)
    unsigned.pop("proposal_id", None)
    if artifact_sha256(unsigned) != root["proposal_id"]:
        raise Step6Error("proposal_id does not match report contents")
    expected_idempotency = artifact_sha256(
        {
            "source_repository": source["repository"],
            "pr_number": source.get("pr_number"),
            "source_revision": source["head_revision"],
            "target_repository": target["repository"],
            "target_revision": target["base_revision"],
            "template": template,
            "patch_sha256": patch["patch_sha256"],
        }
    )
    if root.get("idempotency_key") != expected_idempotency:
        raise Step6Error("idempotency_key does not match report contents")


def _unified_diff_from_files(files: list[Mapping[str, Any]]) -> str:
    chunks: list[str] = []
    for row in files:
        path = row["path"]
        chunks.extend(
            difflib.unified_diff(
                row["before"].splitlines(keepends=True),
                row["after"].splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    return "".join(chunks)


def load_json(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Step6Error(f"{label}_read_failed: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise Step6Error(f"{label} must be an object")
    return value


__all__ = [
    "REPORT_ANALYSIS_KIND",
    "REPORT_SCHEMA_VERSION",
    "REQUEST_ANALYSIS_KIND",
    "REQUEST_SCHEMA_VERSION",
    "RULE_SET_VERSION",
    "TEMPLATE_IDS",
    "TRIGGERS",
    "Step6Error",
    "artifact_sha256",
    "canonical_json",
    "load_json",
    "sha256_bytes",
    "validate_step6_report",
    "validate_step6_request",
]
