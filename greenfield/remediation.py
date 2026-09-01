"""Turn an eligible Analyze-phase action into an exact Step 6 request."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.analysis_report import canonical_remediation_actions
from greenfield.artifact_io import artifact_sha256
from greenfield.step4_contract import artifact_sha256 as step4_artifact_sha256
from greenfield.step6_contract import Step6Error

ACTION_TO_COMPATIBILITY = {
    "update_existing_test": "update_test_obligation",
    "add_missing_test": "add_integration_test",
}


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise Step6Error(
            "automatic remediation Git evidence failed: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return result.stdout


def _candidate(run_context: Mapping[str, Any], repository: str) -> Mapping[str, Any]:
    rows = [
        row
        for row in run_context.get("candidate_repositories", [])
        if isinstance(row, Mapping) and row.get("repository") == repository
    ]
    if len(rows) != 1:
        raise Step6Error("automatic remediation target is outside captured candidates")
    return rows[0]


def _selected_action(
    analysis: Mapping[str, Any], step5: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    compatibility = {
        str(row.get("action_id")): row
        for row in step5.get("actions", [])
        if isinstance(row, Mapping)
    }
    for action in canonical_remediation_actions(analysis):
        if (
            not isinstance(action, Mapping)
            or action.get("draft_eligible") is not True
            or action.get("action_type") not in ACTION_TO_COMPATIBILITY
        ):
            continue
        matched = compatibility.get(str(action.get("action_id")))
        if matched is not None:
            return action, matched
    return None


def build_automatic_step6_request(
    analysis: Mapping[str, Any],
    run_context: Mapping[str, Any],
    step1: Mapping[str, Any],
    step3: Mapping[str, Any],
    step4: Mapping[str, Any],
    step5: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build a bounded request, or return None when no action meets the draft gate."""

    selected = _selected_action(analysis, step5)
    if selected is None:
        return None
    action, compatibility = selected
    repository = str(action["target_repository"])
    candidate = _candidate(run_context, repository)
    revision = str(
        action.get("target_revision") or candidate.get("inspected_revision") or ""
    )
    if revision != candidate.get("inspected_revision"):
        raise Step6Error(
            "automatic remediation target revision is not the captured revision"
        )
    root = Path(str(candidate.get("local_root") or "")).resolve()
    if not root.is_dir():
        raise Step6Error("automatic remediation target checkout is unavailable")

    scope = action.get("scope")
    if not isinstance(scope, Mapping):
        raise Step6Error("automatic remediation action scope is required")
    operations = scope.get("edit_operations")
    if not isinstance(operations, list) or not operations:
        raise Step6Error("automatic remediation requires bounded edit_operations")
    paths = sorted(
        {
            str(row.get("path"))
            for row in operations
            if isinstance(row, Mapping) and row.get("path")
        }
    )
    allowed_paths = scope.get("allowed_paths", paths)
    if allowed_paths != paths:
        raise Step6Error("automatic remediation allowed_paths must exactly match edits")
    files = []
    evidence_files = []
    for path in paths:
        content = _git(root, "show", f"{revision}:{path}").decode(
            "utf-8", errors="strict"
        )
        content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        blob = _git(root, "rev-parse", f"{revision}:{path}").decode().strip()
        files.append({"path": path, "content": content, "sha256": content_sha})
        evidence_files.append(
            {
                "path": path,
                "content_sha256": content_sha,
                "blob_or_response_id": blob,
            }
        )
    target_evidence: dict[str, Any] = {
        "provider": "git_object_database",
        "repository": repository,
        "revision": revision,
        "files": evidence_files,
    }
    target_evidence["evidence_sha256"] = artifact_sha256(target_evidence)

    source = step1["input"]
    source_root = Path(str(run_context["source"].get("local_root") or "")).resolve()
    diff = _git(
        source_root,
        "diff",
        "--no-ext-diff",
        str(source["base_sha"]),
        str(source["head_sha"]),
        "--",
        *source["changed_paths"],
    ).decode("utf-8", errors="replace")
    if not diff:
        raise Step6Error("automatic remediation source diff is empty")
    compatibility_scope = compatibility.get("scope", {})
    trigger = (
        "required_test_category_missing"
        if action["action_type"] == "add_missing_test"
        else "api_or_schema_changed"
    )
    request: dict[str, Any] = {
        "schema_version": "0.1",
        "analysis_kind": "greenfield_pr_impact_step_6_request",
        "source": {
            "repository": step3["input"]["source_repository"],
            "pr_number": source["pr_number"],
            "pr_url": step1.get("pr_metadata", {}).get("url"),
            "base_revision": source["base_sha"],
            "head_revision": source["head_sha"],
            "changed_paths": source["changed_paths"],
            "diff": diff,
            "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        },
        "upstream": {
            "step1_report_sha256": artifact_sha256(step1),
            "step3_report_sha256": artifact_sha256(step3),
            "step4_report_sha256": step4_artifact_sha256(step4),
            "step5_report_sha256": artifact_sha256(step5),
            **(
                {"analysis_report_sha256": analysis.get("report_sha256")}
                if isinstance(analysis, Mapping)
                else {}
            ),
        },
        "action": {
            "action_id": compatibility["action_id"],
            "action_type": ACTION_TO_COMPATIBILITY[action["action_type"]],
            "status": compatibility["status"],
            "target_repository": repository,
            "interface_id": compatibility_scope.get("interface_id"),
            "test_id": compatibility_scope.get("test_id"),
            "test_path": compatibility_scope.get("test_path"),
        },
        "trigger": {"kind": trigger, "evidence": action["evidence"]},
        "target": {
            "repository": repository,
            "base_revision": revision,
            "files": files,
            "allowed_paths": paths,
        },
        "target_evidence": target_evidence,
        "template": {"id": "strands_bounded_test_edit_v1", "version": "0.1"},
        "edit_operations": sorted(
            [dict(row) for row in operations if isinstance(row, Mapping)],
            key=lambda row: str(row.get("path")),
        ),
        "validation_plan": list(scope.get("validation_plan", [])),
        "_step7_eligibility": True,
    }
    return request


__all__ = ["build_automatic_step6_request"]
