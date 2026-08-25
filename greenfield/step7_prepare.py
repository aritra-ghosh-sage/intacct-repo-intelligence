"""Deterministic preparation of Greenfield Step 7 requests."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from greenfield.step6_contract import validate_step6_report
from greenfield.step7_contract import (
    REQUEST_ANALYSIS_KIND,
    SCHEMA_VERSION,
    artifact_sha256,
)
from greenfield.step7_profiles import (
    Step7ProfileError,
    materialize_path_policy,
    profile_fingerprint,
    select_profile,
)

PREPARATION_ANALYSIS_KIND = "greenfield_pr_impact_step_7_preparation"


def _blocked(step6_report: Mapping[str, Any], code: str, detail: str) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": PREPARATION_ANALYSIS_KIND,
        "status": "blocked",
        "step6_report_sha256": artifact_sha256(step6_report),
        "request": None,
        "failures": [{"code": code, "detail": detail}],
        "provenance": {
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
            "pr_creation": "none",
        },
    }
    artifact["report_sha256"] = artifact_sha256(artifact)
    return artifact


def build_step7_request(
    step6_report: Mapping[str, Any], registry: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a profile-backed request or raise for an unavailable policy."""

    errors = validate_step6_report(
        step6_report,
        strict_target_evidence=True,
        require_approvals=True,
        require_step7_eligibility=True,
    )
    if errors:
        raise Step7ProfileError("invalid strict Step 6 report: " + "; ".join(errors))
    repository = str(step6_report["target"]["repository"])
    profile = select_profile(registry, repository)
    if profile is None:
        raise Step7ProfileError(f"no Step 7 validation profile for {repository}")
    if profile.get("enabled") is not True:
        raise Step7ProfileError(
            f"Step 7 validation profile is disabled for {repository}: "
            f"{profile.get('unavailable_reason')}"
        )
    if profile.get("profile_sha256") != profile_fingerprint(profile):
        raise Step7ProfileError(
            f"Step 7 validation profile fingerprint is invalid for {repository}"
        )
    patch_paths = sorted(
        str(row["path"])
        for row in step6_report["patch"]["files"]
        if isinstance(row, Mapping)
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": REQUEST_ANALYSIS_KIND,
        "step6_report_sha256": artifact_sha256(step6_report),
        "profile": {
            "id": profile["profile_id"],
            "version": profile["profile_version"],
            "sha256": profile["profile_sha256"],
        },
        "runner": {"required_class": profile["required_runner"]},
        "target": {
            "repository": repository,
            "base_revision": step6_report["target"]["base_revision"],
        },
        "commands": deepcopy(profile["commands"]),
        "policy": materialize_path_policy(profile, patch_paths),
    }


def prepare_step7(
    step6_report: Mapping[str, Any], registry: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        return build_step7_request(step6_report, registry)
    except Step7ProfileError as exc:
        return _blocked(step6_report, "step7_profile_unavailable", str(exc))


__all__ = [
    "PREPARATION_ANALYSIS_KIND",
    "build_step7_request",
    "prepare_step7",
]
