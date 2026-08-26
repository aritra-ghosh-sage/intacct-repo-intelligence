"""Deterministic greenfield Step 5 action recommendations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from greenfield.source_identity import validate_identity_fields
from greenfield.step4_contract import validate_step4_report
from scripts.validate_greenfield_step3 import validate as validate_step3

REPORT_SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_pr_impact_step_5"
RULE_SET_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ACTION_STATUSES = {"recommended", "blocked"}
ACTION_TYPES = {
    "run_test_suite",
    "request_owner_review",
    "update_test_obligation",
    "add_integration_test",
    "block_propagation",
}


class Step5Error(ValueError):
    """Raised when Step 5 evidence cannot safely be evaluated."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def artifact_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Step5Error(f"{label} must be a non-empty string")
    return value.strip()


def _source_context(step3: Mapping[str, Any], step4: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    errors = validate_step3(step3)
    if errors:
        raise Step5Error("invalid Step 3 report: " + "; ".join(errors))
    errors = validate_step4_report(step4)
    if errors:
        raise Step5Error("invalid Step 4 report: " + "; ".join(errors))
    first = step3["input"]
    second = step4["input"]
    repository = _text(first.get("source_repository"), "Step 3 source_repository")
    revision = _text(first.get("target_revision"), "Step 3 target_revision").lower()
    if not SHA.fullmatch(revision):
        raise Step5Error("Step 3 target_revision must be a lowercase 40-character SHA")
    if second.get("source_repository") != repository:
        raise Step5Error("Step 3 and Step 4 source repositories do not match")
    if second.get("target_revision") != revision:
        raise Step5Error("Step 3 and Step 4 target revisions do not match")
    expected_step3_hash = artifact_sha256(step3)
    actual_step3_hash = step4.get("provenance", {}).get("step3_report_sha256")
    if actual_step3_hash != expected_step3_hash:
        raise Step5Error("Step 4 provenance does not match the supplied Step 3 report")
    paths = sorted({_text(path, "changed path") for path in first["changed_paths"]})
    return repository, revision, paths


def _owner(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        return {"identity": None, "status": "unavailable"}
    status = row.get("status")
    identity = row.get("owner") or row.get("owner_repository")
    if status == "available" and isinstance(identity, str) and identity.strip():
        return {"identity": identity.strip(), "status": "available"}
    if status == "unknown":
        return {"identity": None, "status": "unknown"}
    return {"identity": None, "status": "unavailable"}


def _test_owner(value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value.strip():
        return {"identity": value.strip(), "status": "available"}
    return {"identity": None, "status": "unavailable"}


def _evidence(*values: Any) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for value in values:
        if isinstance(value, Mapping):
            for item in value.get("evidence", []):
                if isinstance(item, Mapping):
                    normalized = dict(item)
                    rows[canonical_json(normalized)] = normalized
    return [rows[key] for key in sorted(rows)]


def _fallback_evidence(step3: Mapping[str, Any], step4: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"kind": "step3_report", "sha256": artifact_sha256(step3)},
        {"kind": "step4_report", "sha256": artifact_sha256(step4)},
    ]


def _make_action(
    *,
    action_type: str,
    target_repository: str,
    owner: dict[str, Any],
    scope: dict[str, Any],
    evidence: list[dict[str, Any]],
    reason: str,
    completion_condition: str,
    status: str = "recommended",
) -> dict[str, Any]:
    base = {
        "action_type": action_type,
        "status": status,
        "owner": owner,
        "target_repository": target_repository,
        "target_revision": scope.get("target_revision"),
        "scope": scope,
        "evidence": evidence,
        "reason": reason,
        "completion_condition": completion_condition,
        "rule_set_version": RULE_SET_VERSION,
    }
    base["action_id"] = hashlib.sha256(canonical_json(base).encode("utf-8")).hexdigest()
    return base


def _owner_rows(step3: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    surface = step3.get("owners", {})
    return {
        (str(row.get("target_repository")), str(row.get("interface_id"))): row
        for row in surface.get("items", [])
        if isinstance(row, Mapping)
    } if isinstance(surface, Mapping) else {}


def _interface_rows(step3: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    surface = step3.get("interfaces", {})
    return {
        (str(row.get("target_repository")), str(row.get("interface_id"))): row
        for row in surface.get("items", [])
        if isinstance(row, Mapping)
    } if isinstance(surface, Mapping) else {}


def recommend_actions(step3: Mapping[str, Any], step4: Mapping[str, Any]) -> dict[str, Any]:
    repository, revision, changed_paths = _source_context(step3, step4)
    owners = _owner_rows(step3)
    interfaces = _interface_rows(step3)
    actions: list[dict[str, Any]] = []
    fallback = _fallback_evidence(step3, step4)

    for item in step4["coverage"]["items"]:
        target = _text(item.get("target_repository"), "coverage target_repository")
        interface_id = _text(item.get("interface_id"), "coverage interface_id")
        owner = _owner(owners.get((target, interface_id)))
        test = item.get("test")
        classification = item.get("classification")
        evidence = _evidence(item) or _evidence(interfaces.get((target, interface_id))) or fallback
        if classification in {"covered", "indirectly_covered", "candidate"} and isinstance(test, Mapping):
            test_id = _text(test.get("id"), "test id")
            test_path = _text(test.get("path"), "test path")
            discovered_only = classification == "candidate"
            action = _make_action(
                action_type="run_test_suite",
                target_repository=target,
                owner=owner,
                scope={"interface_id": interface_id, "test_id": test_id, "test_path": test_path, "target_revision": item.get("target_revision")},
                evidence=evidence,
                reason=("source_ranked_test_without_execution_proof" if discovered_only else f"{classification}_test_evidence"),
                completion_condition=f"The named test {test_id} at {test_path} passes against the target revision.",
            )
            action["test_owner"] = _test_owner(item.get("test_owner"))
            action["test_command"] = item.get("test_command")
            action["approval_required"] = action["test_owner"]["status"] != "available"
            action["action_id"] = hashlib.sha256(
                canonical_json({key: value for key, value in action.items() if key != "action_id"}).encode("utf-8")
            ).hexdigest()
            actions.append(action)
        if classification in {"unavailable", "stale"}:
            actions.append(_make_action(
                action_type="block_propagation",
                target_repository=target,
                owner=owner,
                scope={"interface_id": interface_id, "target_revision": item.get("target_revision")},
                evidence=evidence,
                reason=f"test_evidence_{classification}",
                completion_condition="A target repository revision and fresh source-revision-bound evidence are available.",
                status="blocked",
            ))

    for item in step4["obligations"]["items"]:
        if item.get("status") != "missing":
            continue
        target = _text(item.get("target_repository"), "obligation target_repository")
        interface_id = _text(item.get("interface_id"), "obligation interface_id")
        change = item.get("required_change")
        action_type = "add_integration_test" if change == "integration" else "update_test_obligation"
        evidence = _evidence(interfaces.get((target, interface_id))) or fallback
        action = _make_action(
            action_type=action_type,
            target_repository=target,
            owner=_owner(owners.get((target, interface_id))),
            scope={"interface_id": interface_id, "obligation_id": item.get("obligation_id"), "test_id": item["test_id"], "test_path": item["test_path"], "required_change": change, "target_revision": None},
            evidence=evidence,
            reason="declared_test_obligation_missing",
            completion_condition=f"The declared test obligation {item['test_id']} exists at {item['test_path']} and is validated against the source revision.",
        )
        action["test_owner"] = _test_owner(item.get("test_owner"))
        action["test_command"] = item.get("test_command")
        action["approval_required"] = action["test_owner"]["status"] != "available"
        action["action_id"] = hashlib.sha256(
            canonical_json({key: value for key, value in action.items() if key != "action_id"}).encode("utf-8")
        ).hexdigest()
        actions.append(action)

    for gap in sorted({str(value) for value in step3.get("gaps", [])}):
        prefix = "repository_access_unavailable:"
        if not gap.startswith(prefix):
            continue
        target = _text(gap[len(prefix):], "unavailable target repository")
        actions.append(_make_action(
            action_type="block_propagation",
            target_repository=target,
            owner={"identity": None, "status": "unavailable"},
            scope={"interface_id": f"repository:{target}", "target_revision": None},
            evidence=fallback,
            reason="target_repository_unavailable",
            completion_condition="The target repository and an exact base revision are available for inspection.",
            status="blocked",
        ))

    for key, owner_row in sorted(owners.items()):
        target, interface_id = key
        interface = interfaces.get(key)
        if not interface or owner_row.get("status") != "available":
            continue
        actions.append(_make_action(
            action_type="request_owner_review",
            target_repository=target,
            owner=_owner(owner_row),
            scope={"interface_id": interface_id, "target_revision": None},
            evidence=_evidence(owner_row, interface) or fallback,
            reason="impacted_interface_owner_available",
            completion_condition="The declared owner reviews the impacted interface or records an explicit disposition.",
        ))

    unique = {action["action_id"]: action for action in actions}
    ordered = sorted(unique.values(), key=lambda row: (0 if row["status"] == "blocked" else 1, row["target_repository"], row["scope"].get("interface_id", ""), row["action_type"], row["scope"].get("test_id", ""), row["action_id"]))
    owner_gaps = {
        f"owner_approval_pending:{target}:{interface_id}"
        for (target, interface_id), owner_row in owners.items()
        if owner_row.get("status") != "available"
    }
    gaps = sorted(
        {str(value) for value in step3.get("gaps", [])}
        | {str(value) for value in step4.get("gaps", [])}
        | owner_gaps
    )
    source_input = {
        "source_repository": repository,
        "target_revision": revision,
        "changed_paths": changed_paths,
    }
    for field in (
        "canonical_repository",
        "source_repo_key",
        "source_pr_number",
        "base_revision",
    ):
        if field in step3["input"]:
            source_input[field] = step3["input"][field]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "complete" if not gaps else "partial",
        "input": source_input,
        "actions": ordered,
        "gaps": gaps,
        "warnings": sorted({str(value) for value in step3.get("warnings", [])} | {str(value) for value in step4.get("warnings", [])}),
        "provenance": {
            "step3_report_sha256": artifact_sha256(step3),
            "step4_report_sha256": artifact_sha256(step4),
            "rule_set_version": RULE_SET_VERSION,
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
        },
    }


def validate_step5_report(report: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report must be an object"]
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {REPORT_SCHEMA_VERSION}")
    if report.get("analysis_kind") != ANALYSIS_KIND:
        errors.append(f"analysis_kind must be {ANALYSIS_KIND}")
    if report.get("status") not in {"complete", "partial"}:
        errors.append("status must be complete or partial")
    data = report.get("input")
    if not isinstance(data, Mapping):
        errors.append("input must be an object")
    else:
        if not isinstance(data.get("source_repository"), str) or not data["source_repository"].strip():
            errors.append("input.source_repository is required")
        if not isinstance(data.get("target_revision"), str) or not SHA.fullmatch(data["target_revision"]):
            errors.append("input.target_revision must be a lowercase 40-character SHA")
        if not isinstance(data.get("changed_paths"), list) or not data["changed_paths"]:
            errors.append("input.changed_paths must be a non-empty list")
        errors.extend(validate_identity_fields(data))
    actions = report.get("actions")
    if not isinstance(actions, list):
        errors.append("actions must be a list")
        actions = []
    keys: list[tuple[Any, ...]] = []
    for index, action in enumerate(actions):
        label = f"actions[{index}]"
        if not isinstance(action, Mapping):
            errors.append(f"{label} must be an object")
            continue
        if action.get("action_type") not in ACTION_TYPES:
            errors.append(f"{label}.action_type is invalid")
        if action.get("status") not in ACTION_STATUSES:
            errors.append(f"{label}.status is invalid")
        for field in ("action_id", "target_repository", "reason", "completion_condition", "rule_set_version"):
            if not isinstance(action.get(field), str) or not action[field].strip():
                errors.append(f"{label}.{field} is required")
        if not isinstance(action.get("action_id"), str) or not SHA256.fullmatch(action["action_id"]):
            errors.append(f"{label}.action_id must be SHA-256")
        if isinstance(action, Mapping) and isinstance(action.get("action_id"), str):
            action_payload = deepcopy(dict(action))
            action_payload.pop("action_id", None)
            expected_action_id = hashlib.sha256(canonical_json(action_payload).encode("utf-8")).hexdigest()
            if action["action_id"] != expected_action_id:
                errors.append(f"{label}.action_id does not match action contents")
        owner = action.get("owner")
        if not isinstance(owner, Mapping) or owner.get("status") not in {"available", "unknown", "unavailable"}:
            errors.append(f"{label}.owner is invalid")
        elif owner.get("status") == "available" and (not isinstance(owner.get("identity"), str) or not owner["identity"].strip()):
            errors.append(f"{label}.owner.identity is required when available")
        if not isinstance(action.get("scope"), Mapping):
            errors.append(f"{label}.scope must be an object")
        evidence = action.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(item, Mapping) for item in evidence):
            errors.append(f"{label}.evidence must be a non-empty list of objects")
        keys.append((0 if action.get("status") == "blocked" else 1, str(action.get("target_repository")), str(action.get("scope", {}).get("interface_id", "") if isinstance(action.get("scope"), Mapping) else ""), str(action.get("action_type")), str(action.get("scope", {}).get("test_id", "") if isinstance(action.get("scope"), Mapping) else ""), str(action.get("action_id"))))
    if keys != sorted(keys):
        errors.append("actions must be deterministically ordered")
    if len({key[-1] for key in keys}) != len(keys):
        errors.append("actions must have unique action_id values")
    for field in ("gaps", "warnings"):
        if not isinstance(report.get(field), list) or any(not isinstance(value, str) for value in report[field]):
            errors.append(f"{field} must be a list of strings")
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
    for field in ("step3_report_sha256", "step4_report_sha256"):
        if not isinstance(provenance, Mapping) or not SHA256.fullmatch(str(provenance.get(field, ""))):
            errors.append(f"provenance.{field} must be SHA-256")
    return errors


__all__ = ["ANALYSIS_KIND", "REPORT_SCHEMA_VERSION", "RULE_SET_VERSION", "Step5Error", "artifact_sha256", "recommend_actions", "validate_step5_report"]
