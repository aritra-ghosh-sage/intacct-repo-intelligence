"""Deterministic greenfield PR-impact candidate resolution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from greenfield.step2_contract import artifact_sha256

REPORT_SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_pr_impact_step_2"
RULE_SET_VERSION = "0.1"
_CLASSIFICATION_ORDER = {"confirmed": 0, "candidate": 1, "unresolved": 2, "stale": 3, "unavailable": 4, "unknown": 5}


class CandidateError(ValueError):
    """Raised when the Step 1 input cannot be used for candidate resolution."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateError(f"{label} must be a non-empty string")
    return value.strip()


def _step1_context(step1: Mapping[str, Any]) -> tuple[str, str, list[str], str]:
    data = step1.get("input")
    if not isinstance(data, Mapping):
        raise CandidateError("Step 1 input must be an object")
    repository = _text(data.get("repo_key") or data.get("repository"), "Step 1 repository")
    revision = _text(data.get("target_revision") or data.get("head_sha"), "Step 1 target revision").lower()
    changed = step1.get("changed_files")
    if not isinstance(changed, list) or not changed:
        raise CandidateError("Step 1 changed_files must be non-empty")
    paths: list[str] = []
    for item in changed:
        if not isinstance(item, Mapping):
            raise CandidateError("Step 1 changed_files entries must be objects")
        paths.append(_text(item.get("path") or item.get("filename"), "changed file path"))
    return repository, revision, sorted(set(paths)), artifact_sha256(step1)


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(candidate.get("target_repository", "")),
        str(candidate.get("interface_id", "")),
        str(candidate.get("relationship_type", "")),
        str(candidate.get("classification", "")),
    )


def resolve_candidates(
    step1: Mapping[str, Any],
    contracts: Iterable[Mapping[str, Any]] = (),
    ci_evidence: Iterable[Mapping[str, Any]] = (),
    inventory_evidence: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    contracts = list(contracts)
    ci_evidence = list(ci_evidence)
    inventory_evidence = list(inventory_evidence)
    source_repository, target_revision, changed_paths, step1_hash = _step1_context(step1)
    candidates: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    gaps: list[str] = []
    warnings: list[str] = []

    for contract in contracts:
        if contract.get("repository") != source_repository:
            gaps.append(f"contract:source_repository_mismatch:{contract.get('repository')}")
            continue
        if contract.get("revision") != target_revision:
            gaps.append(f"contract:stale:{contract.get('evidence', {}).get('path', 'unknown')}")
            continue
        for relation in contract.get("relations", []):
            if relation.get("status") != "active":
                continue
            matched = sorted(set(changed_paths) & set(relation.get("source_paths", [])))
            if not matched:
                continue
            candidate = {
                "target_repository": relation["consumer_repository"],
                "interface_id": relation["interface_id"],
                "relationship_type": "explicit_contract",
                "classification": "confirmed",
                "reason": "exact_active_contract",
                "owner": relation.get("owner"),
                "changed_paths": matched,
                "evidence": [{
                    "kind": "contract",
                    "path": contract["evidence"]["path"],
                    "sha256": contract["evidence"]["sha256"],
                    "repository": contract["repository"],
                    "revision": contract["revision"],
                }],
            }
            candidates[_candidate_key(candidate)] = candidate

    known_interfaces = {candidate["interface_id"] for candidate in candidates.values()}
    for evidence in ci_evidence:
        if evidence.get("source_repository") != source_repository:
            gaps.append(f"ci:source_repository_mismatch:{evidence.get('evidence_id', 'unknown')}")
            continue
        if evidence.get("source_revision") != target_revision:
            gaps.append(f"ci:stale:{evidence.get('evidence_id', 'unknown')}")
            continue
        status = evidence.get("status")
        if status != "available":
            gaps.append(f"ci:{status}:{evidence.get('evidence_id', 'unknown')}")
            continue
        interface_id = evidence["interface_id"]
        reason = "ci_supports_confirmed_contract" if interface_id in known_interfaces else "ci_observed_exact_source_revision"
        candidate = {
            "target_repository": evidence["repository"],
            "interface_id": interface_id,
            "relationship_type": "ci_observed",
            "classification": "candidate",
            "reason": reason,
            "changed_paths": [],
            "tests": evidence.get("tests", []),
            "evidence": [{
                "kind": "ci",
                "evidence_id": evidence["evidence_id"],
                "path": evidence["evidence"]["path"],
                "sha256": evidence["evidence"]["sha256"],
                "repository": evidence["repository"],
                "commit_sha": evidence["commit_sha"],
                "source_revision": evidence["source_revision"],
            }],
        }
        key = _candidate_key(candidate)
        if key not in candidates:
            candidates[key] = candidate
        else:
            candidates[key]["evidence"].extend(candidate["evidence"])
            candidates[key]["tests"] = sorted(
                {json.dumps(test, sort_keys=True): test for test in candidates[key].get("tests", []) + candidate.get("tests", [])}.values(),
                key=lambda value: json.dumps(value, sort_keys=True),
            )

    interfaces_by_repository: dict[str, list[str]] = {}
    for candidate in candidates.values():
        if candidate["relationship_type"] == "explicit_contract":
            interfaces_by_repository.setdefault(candidate["target_repository"], []).append(
                candidate["interface_id"]
            )
    for repository, interfaces in interfaces_by_repository.items():
        interfaces_by_repository[repository] = sorted(set(interfaces))

    for inventory in inventory_evidence:
        repository = inventory.get("repository", "unknown")
        if inventory.get("source_repository") != source_repository:
            gaps.append(f"inventory:source_repository_mismatch:{repository}")
            continue
        if inventory.get("source_revision") != target_revision:
            gaps.append(f"inventory:stale:{repository}")
            continue
        status = inventory.get("status")
        if status != "available":
            gaps.append(
                f"repository_access_unavailable:{repository}"
                if status == "unavailable"
                else f"repository_inventory_empty:{repository}"
            )
            continue
        for inventory_gap in inventory.get("gaps", []):
            if isinstance(inventory_gap, str) and inventory_gap:
                gaps.append(f"{repository}:{inventory_gap}")
        workflow_rows = [
            row for row in inventory.get("workflows", []) if isinstance(row, Mapping)
        ]
        has_test_execution = any(row.get("has_test_execution") is True for row in workflow_rows)
        has_artifact = inventory.get("artifact_status") == "available"
        interface_ids = interfaces_by_repository.get(repository, []) or [
            f"repository:{repository}"
        ]
        for interface_id in interface_ids:
            reason = "repository_inventory_only"
            if not has_test_execution:
                reason = "workflow_has_no_test_execution"
            elif has_artifact:
                reason = "ci_artifact_present_not_normalized"
            candidate = {
                "target_repository": repository,
                "interface_id": interface_id,
                "relationship_type": "repository_inventory",
                "classification": "candidate",
                "reason": reason,
                "changed_paths": changed_paths,
                "workflow_paths": inventory.get("workflow_paths", []),
                "inventory_paths": inventory.get("inventory_paths", []),
                "workflows": workflow_rows,
                "evidence": [{
                    "kind": "repository_inventory",
                    "path": inventory.get("evidence_path", "<in-memory>"),
                    "response_sha256": inventory["provenance"]["response_sha256"],
                    "repository": repository,
                    "inspected_revision": inventory["inspected_revision"],
                    "source_revision": inventory["source_revision"],
                    "artifact_status": inventory["artifact_status"],
                }],
            }
            candidates[_candidate_key(candidate)] = candidate
        if inventory.get("artifact_status") != "available":
            gaps.append(f"ci_artifact_unavailable:{repository}")
        if not has_test_execution:
            gaps.append(f"workflow_has_no_test_execution:{repository}")

    rows = sorted(candidates.values(), key=lambda item: (
        _CLASSIFICATION_ORDER[item["classification"]],
        item["target_repository"],
        item["interface_id"],
        item["relationship_type"],
    ))
    if not rows:
        warnings.append("no confirmed or candidate impact evidence was resolved")
    target_repositories = {row["target_repository"] for row in rows}
    if not target_repositories:
        blast_radius = "unknown" if gaps else "local"
    elif any(row["classification"] == "confirmed" for row in rows):
        blast_radius = "multi_repo"
    else:
        blast_radius = "boundary"
    evidence_sources = []
    if contracts:
        evidence_sources.append("explicit_contract")
    if ci_evidence:
        evidence_sources.append("ci_observed")
    if inventory_evidence:
        evidence_sources.append("repository_inventory")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "complete" if not gaps else "partial",
        "input": {
            "source_repository": source_repository,
            "target_revision": target_revision,
            "changed_paths": changed_paths,
        },
        "candidates": rows,
        "blast_radius": blast_radius,
        "gaps": sorted(set(gaps)),
        "warnings": sorted(set(warnings)),
        "provenance": {
            "step1_report_sha256": step1_hash,
            "rule_set_version": RULE_SET_VERSION,
            "evidence_sources": evidence_sources,
            "read_only": True,
            "catalog_mutation": "none",
        },
    }


def canonical_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def report_sha256(report: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
