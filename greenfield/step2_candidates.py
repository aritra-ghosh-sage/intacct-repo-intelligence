"""Deterministic greenfield PR-impact candidate resolution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from greenfield.source_identity import repository_matches, source_identity
from greenfield.step2_contract import artifact_sha256, ci_execution_binding_status
from greenfield.step2_likelihood import (
    attach_explicit_contract_anchors,
    build_source_anchors,
    rank_likely_tests,
)

REPORT_SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_pr_impact_step_2"
RULE_SET_VERSION = "0.1"
TRUST_EVIDENCE_PROFILE = "trust_foundation_v1"
_CLASSIFICATION_ORDER = {
    "confirmed": 0,
    "candidate": 1,
    "unresolved": 2,
    "stale": 3,
    "unavailable": 4,
    "unknown": 5,
}


class CandidateError(ValueError):
    """Raised when the Step 1 input cannot be used for candidate resolution."""


def _confidence_summary(
    rows: list[Mapping[str, Any]],
    gaps: list[str],
    *,
    contracts: list[Mapping[str, Any]],
    ci_evidence: list[Mapping[str, Any]],
    inventory_evidence: list[Mapping[str, Any]],
    semantic_indexes: list[Mapping[str, Any]],
    source_revision: str,
    source_pr_number: int | None,
) -> dict[str, Any]:
    confirmed = sum(row.get("classification") == "confirmed" for row in rows)
    bound_ci = sum(
        ci_execution_binding_status(
            evidence, source_revision=source_revision, source_pr_number=source_pr_number
        )
        == "bound"
        for evidence in ci_evidence
        if evidence.get("status") == "available"
    )
    stale_or_unbound = sum(
        ci_execution_binding_status(
            evidence, source_revision=source_revision, source_pr_number=source_pr_number
        )
        != "bound"
        for evidence in ci_evidence
        if evidence.get("status") == "available"
    )
    if confirmed:
        band = "confirmed"
    elif bound_ci:
        band = "strong_candidate"
    elif rows:
        band = "candidate"
    elif any(value.startswith(("contract:", "ci:", "inventory:", "semantic_index:")) for value in gaps):
        band = "unresolved"
    else:
        band = "unavailable"
    return {
        "band": band,
        "rule_set_version": RULE_SET_VERSION,
        "components": {
            "exact_source_evidence": bool(contracts),
            "behavior_contract_match": any(
                row.get("declared_relationship_type") == "behavior_contract"
                and row.get("classification") == "confirmed"
                for row in rows
            ),
            "revision_freshness": not any("stale" in gap for gap in gaps),
            "source_runtime_trace": bool(contracts or semantic_indexes),
            "ci_execution": "bound" if bound_ci else ("unbound" if stale_or_unbound else "none"),
            "inventory_completeness": bool(inventory_evidence),
            "unresolved_or_unavailable_gaps": len(gaps),
        },
    }


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateError(f"{label} must be a non-empty string")
    return value.strip()


def _step1_context(
    step1: Mapping[str, Any],
) -> tuple[str, str, str, list[str], str, int | None, str | None, bool]:
    data = step1.get("input")
    if not isinstance(data, Mapping):
        raise CandidateError("Step 1 input must be an object")
    try:
        canonical_repository, repo_key = source_identity(data)
    except ValueError as exc:
        raise CandidateError(str(exc)) from exc
    revision = _text(
        data.get("target_revision") or data.get("head_sha"), "Step 1 target revision"
    ).lower()
    changed = step1.get("changed_files")
    if not isinstance(changed, list) or not changed:
        raise CandidateError("Step 1 changed_files must be non-empty")
    paths: list[str] = []
    for item in changed:
        if not isinstance(item, Mapping):
            raise CandidateError("Step 1 changed_files entries must be objects")
        paths.append(
            _text(item.get("path") or item.get("filename"), "changed file path")
        )
    pr_number = data.get("pr_number")
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1:
        pr_number = None
    base_revision = data.get("base_revision") or data.get("base_sha")
    if not isinstance(base_revision, str) or not base_revision.strip():
        base_revision = None
    profile = data.get("evidence_profile")
    if profile is not None and profile != TRUST_EVIDENCE_PROFILE:
        raise CandidateError(f"unsupported evidence_profile: {profile}")
    return (
        repo_key,
        canonical_repository,
        revision,
        sorted(set(paths)),
        artifact_sha256(step1),
        pr_number,
        base_revision.lower() if base_revision else None,
        profile == TRUST_EVIDENCE_PROFILE,
    )


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(candidate.get("target_repository", "")),
        str(candidate.get("interface_id", "")),
        str(candidate.get("relationship_type", "")),
        str(candidate.get("classification", "")),
    )


def _evidence_score(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explainable evidence-strength score, not a probability."""

    score = 0
    components: dict[str, int] = {}
    evidence_kinds = {
        str(item.get("kind"))
        for item in candidate.get("evidence", [])
        if isinstance(item, Mapping)
    }
    if "contract" in evidence_kinds:
        components["exact_contract"] = 60
    if candidate.get("declared_relationship_type") == "behavior_contract":
        components["behavior_contract"] = 20
    if "ci" in evidence_kinds:
        components["source_revision_ci"] = 25
    if "semantic_index" in evidence_kinds:
        components["semantic_source_evidence"] = 15
    if "repository_inventory" in evidence_kinds:
        components["repository_inventory"] = 5
    score = min(100, sum(components.values()))
    return {
        "score": score,
        "rule_set_version": "0.1",
        "components": components,
        "meaning": "evidence_strength_not_probability",
    }


def resolve_candidates(
    step1: Mapping[str, Any],
    contracts: Iterable[Mapping[str, Any]] = (),
    ci_evidence: Iterable[Mapping[str, Any]] = (),
    inventory_evidence: Iterable[Mapping[str, Any]] = (),
    semantic_indexes: Iterable[Mapping[str, Any]] = (),
    *,
    include_evidence_scores: bool = False,
) -> dict[str, Any]:
    contracts = list(contracts)
    ci_evidence = list(ci_evidence)
    inventory_evidence = list(inventory_evidence)
    semantic_indexes = list(semantic_indexes)
    (
        source_repo_key,
        canonical_repository,
        target_revision,
        changed_paths,
        step1_hash,
        source_pr_number,
        base_revision,
        strict_evidence,
    ) = _step1_context(step1)
    candidates: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    gaps: list[str] = []
    warnings: list[str] = []

    valid_semantic_indexes = [
        index
        for index in semantic_indexes
        if repository_matches(index.get("repository"), canonical_repository, source_repo_key)
        and index.get("revision") == target_revision
    ]
    source_anchors = build_source_anchors(changed_paths, valid_semantic_indexes)
    eligible_contracts: list[Mapping[str, Any]] = []
    for contract in contracts:
        if contract.get("artifact_kind") != "generated_behavior_contract":
            eligible_contracts.append(contract)
            continue
        generated_input = contract.get("input")
        generated_paths = (
            generated_input.get("changed_paths")
            if isinstance(generated_input, Mapping)
            else None
        )
        if not isinstance(generated_input, Mapping) or (
            generated_input.get("repository") not in {canonical_repository, source_repo_key}
            or generated_input.get("repo_key") != source_repo_key
            or generated_input.get("head_sha") != target_revision
            or not isinstance(generated_paths, list)
            or sorted(generated_paths) != sorted(changed_paths)
        ):
            gaps.append(
                f"contract:generated_input_mismatch:{contract.get('evidence', {}).get('path', 'unknown')}"
            )
            continue
        eligible_contracts.append(contract)
    active_relations = [
        relation
        for contract in eligible_contracts
        if repository_matches(contract.get("repository"), canonical_repository, source_repo_key)
        and contract.get("revision") == target_revision
        for relation in contract.get("relations", [])
        if relation.get("status") == "active"
    ]
    source_anchors = attach_explicit_contract_anchors(
        source_anchors, changed_paths, active_relations, target_revision
    )

    for contract in eligible_contracts:
        if not repository_matches(
            contract.get("repository"), canonical_repository, source_repo_key
        ):
            gaps.append(
                f"contract:source_repository_mismatch:{contract.get('repository')}"
            )
            continue
        if contract.get("revision") != target_revision:
            gaps.append(
                f"contract:stale:{contract.get('evidence', {}).get('path', 'unknown')}"
            )
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
                "declared_relationship_type": relation.get("relationship_type"),
                "classification": "confirmed",
                "reason": "exact_active_contract",
                "owner_repository": relation.get("owner_repository"),
                "owner": relation.get("owner"),
                "source_symbols": relation.get("source_symbols", []),
                "changed_paths": matched,
                "evidence": [
                    {
                        "kind": "contract",
                        "path": contract["evidence"]["path"],
                        "sha256": contract["evidence"]["sha256"],
                        "repository": contract["repository"],
                        "revision": contract["revision"],
                    }
                ],
            }
            candidates[_candidate_key(candidate)] = candidate

    known_interfaces = {candidate["interface_id"] for candidate in candidates.values()}
    for evidence in ci_evidence:
        if not repository_matches(
            evidence.get("source_repository"), canonical_repository, source_repo_key
        ):
            gaps.append(
                f"ci:source_repository_mismatch:{evidence.get('evidence_id', 'unknown')}"
            )
            continue
        if evidence.get("source_revision") != target_revision:
            gaps.append(f"ci:stale:{evidence.get('evidence_id', 'unknown')}")
            continue
        status = evidence.get("status")
        if status != "available":
            gaps.append(f"ci:{status}:{evidence.get('evidence_id', 'unknown')}")
            continue
        interface_id = evidence["interface_id"]
        reason = (
            "ci_supports_confirmed_contract"
            if interface_id in known_interfaces
            else "ci_observed_exact_source_revision"
        )
        candidate = {
            "target_repository": evidence["repository"],
            "interface_id": interface_id,
            "relationship_type": "ci_observed",
            "classification": "candidate",
            "reason": reason,
            "changed_paths": [],
            "tests": evidence.get("tests", []),
            "evidence": [
                {
                    "kind": "ci",
                    "evidence_id": evidence["evidence_id"],
                    "path": evidence["evidence"]["path"],
                    "sha256": evidence["evidence"]["sha256"],
                    "repository": evidence["repository"],
                    "commit_sha": evidence["commit_sha"],
                    "source_revision": evidence["source_revision"],
                }
            ],
        }
        key = _candidate_key(candidate)
        if key not in candidates:
            candidates[key] = candidate
        else:
            candidates[key]["evidence"].extend(candidate["evidence"])
            candidates[key]["tests"] = sorted(
                {
                    json.dumps(test, sort_keys=True): test
                    for test in candidates[key].get("tests", [])
                    + candidate.get("tests", [])
                }.values(),
                key=lambda value: json.dumps(value, sort_keys=True),
            )

    interfaces_by_repository: dict[str, list[str]] = {}
    for candidate in candidates.values():
        if candidate["relationship_type"] == "explicit_contract":
            interfaces_by_repository.setdefault(
                candidate["target_repository"], []
            ).append(candidate["interface_id"])
    for repository, interfaces in interfaces_by_repository.items():
        interfaces_by_repository[repository] = sorted(set(interfaces))

    for inventory in inventory_evidence:
        repository = inventory.get("repository", "unknown")
        if not repository_matches(
            inventory.get("source_repository"), canonical_repository, source_repo_key
        ):
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
            if status == "unavailable" and strict_evidence:
                candidate = {
                    "target_repository": repository,
                    "interface_id": f"repository:{repository}",
                    "relationship_type": "repository_inventory",
                    "classification": "unresolved",
                    "reason": "repository_inventory_unavailable",
                    "changed_paths": changed_paths,
                    "evidence": [{
                        "kind": "repository_inventory_unavailable",
                        "path": inventory.get("evidence_path", "<in-memory>"),
                        "response_sha256": inventory.get("provenance", {}).get("response_sha256"),
                        "repository": repository,
                        "inspected_revision": inventory.get("inspected_revision"),
                        "source_revision": inventory.get("source_revision"),
                        "artifact_status": inventory.get("artifact_status", "empty"),
                        "ci_linkage_status": inventory.get("ci_linkage", {}).get("status", "unavailable"),
                    }],
                }
                candidates[_candidate_key(candidate)] = candidate
            continue
        for inventory_gap in inventory.get("gaps", []):
            if isinstance(inventory_gap, str) and inventory_gap:
                gaps.append(f"{repository}:{inventory_gap}")
        workflow_rows = [
            row for row in inventory.get("workflows", []) if isinstance(row, Mapping)
        ]
        has_test_execution = any(
            row.get("has_test_execution") is True for row in workflow_rows
        )
        workflow_classifications = {
            str(row.get("classification")) for row in workflow_rows
        }
        metadata_only = bool(workflow_rows) and workflow_classifications == {
            "metadata_only"
        }
        has_artifact = inventory.get("artifact_status") == "available"
        ci_linkage = inventory.get("ci_linkage", {})
        ci_linkage_available = (
            isinstance(ci_linkage, Mapping) and ci_linkage.get("status") == "available"
        )
        interface_ids = interfaces_by_repository.get(repository, []) or [
            f"repository:{repository}"
        ]
        for interface_id in interface_ids:
            reason = "repository_inventory_only"
            if not has_test_execution:
                reason = (
                    "workflow_metadata_only"
                    if metadata_only
                    else "workflow_has_no_test_execution"
                )
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
                "evidence": [
                    {
                        "kind": "repository_inventory",
                        "path": inventory.get("evidence_path", "<in-memory>"),
                        "response_sha256": inventory["provenance"]["response_sha256"],
                        "repository": repository,
                        "inspected_revision": inventory["inspected_revision"],
                        "source_revision": inventory["source_revision"],
                        "artifact_status": inventory["artifact_status"],
                        "ci_linkage_status": ci_linkage.get("status", "unavailable")
                        if isinstance(ci_linkage, Mapping)
                        else "unavailable",
                    }
                ],
            }
            candidates[_candidate_key(candidate)] = candidate
        if inventory.get("artifact_status") != "available":
            gaps.append(f"ci_artifact_unavailable:{repository}")
        if not ci_linkage_available:
            gaps.append(f"ci_linkage_unavailable:{repository}")
        if not has_test_execution:
            gaps.append(f"workflow_has_no_test_execution:{repository}")
        if metadata_only:
            gaps.append(f"workflow_metadata_only:{repository}")

    # Static semantic evidence can identify the changed contract surface, but
    # it cannot identify a consumer repository by itself.  An active contract
    # supplies that cross-repository target; the semantic index only upgrades
    # the evidence path to an explicit candidate and never to confirmed CI.
    semantic_provenance: list[str] = []
    for index in semantic_indexes:
        repository = index.get("repository")
        revision = index.get("revision")
        if not repository_matches(repository, canonical_repository, source_repo_key):
            gaps.append(f"semantic_index:source_repository_mismatch:{repository}")
            continue
        if revision != target_revision:
            gaps.append(f"semantic_index:stale:{index.get('evidence_path', 'unknown')}")
            continue
        raw_provenance = index.get("provenance", {})
        provenance = raw_provenance if isinstance(raw_provenance, Mapping) else {}
        if provenance.get("index_sha256"):
            semantic_provenance.append(str(provenance["index_sha256"]))
        nodes = {
            node.get("key"): node
            for node in index.get("nodes", [])
            if isinstance(node, Mapping) and isinstance(node.get("key"), str)
        }
        changed_edges = []
        index_interface_ids: set[str] = set()
        for edge in index.get("edges", []):
            if not isinstance(edge, Mapping):
                continue
            if any(
                isinstance(evidence, Mapping)
                and evidence.get("source_path") in changed_paths
                for evidence in edge.get("evidence", [])
            ):
                changed_edges.append(edge)
                for endpoint in (edge.get("source"), edge.get("target")):
                    node = nodes.get(endpoint)
                    if not isinstance(node, Mapping):
                        continue
                    if node.get("kind") in {"api_object", "entity"}:
                        interface_id = f"{node['kind']}:{node['identity']}"
                        index_interface_ids.add(interface_id)
        if not changed_edges:
            gaps.append(
                f"semantic_index_no_changed_edge:{index.get('evidence_path', 'unknown')}"
            )
            continue
        if not index_interface_ids:
            gaps.append(
                "semantic_index_no_interface_for_changed_edge:"
                f"{index.get('evidence_path', 'unknown')}"
            )
            continue
        active_interface_ids = {
            relation.get("interface_id") for relation in active_relations
        }
        for interface_id in sorted(index_interface_ids):
            if interface_id not in active_interface_ids:
                gap_kind = (
                    "semantic_index_unmatched_interface"
                    if active_relations
                    else "semantic_index_missing_active_contract"
                )
                gaps.append(f"{gap_kind}:{interface_id}")
        for contract in contracts:
            if not repository_matches(
                contract.get("repository"), canonical_repository, source_repo_key
            ):
                continue
            if contract.get("revision") != target_revision:
                continue
            for relation in contract.get("relations", []):
                if relation.get("status") != "active":
                    continue
                relation_id = relation.get("interface_id")
                if relation_id not in index_interface_ids and not (
                    set(relation.get("source_paths", [])) & set(changed_paths)
                ):
                    continue
                target = relation["consumer_repository"]
                existing_confirmed = any(
                    candidate.get("target_repository") == target
                    and candidate.get("interface_id") == relation_id
                    and candidate.get("classification") == "confirmed"
                    for candidate in candidates.values()
                )
                if existing_confirmed:
                    continue
                candidate = {
                    "target_repository": target,
                    "interface_id": relation_id,
                    "relationship_type": "semantic_static",
                    "classification": "candidate",
                    "reason": "semantic_index_supports_contract",
                    "changed_paths": changed_paths,
                    "evidence": [
                        {
                            "kind": "semantic_index",
                            "path": index.get("evidence_path", "<in-memory>"),
                            "index_sha256": provenance.get("index_sha256"),
                            "repository": repository,
                            "revision": revision,
                            "interface_ids": sorted(index_interface_ids),
                        }
                    ],
                }
                candidates[_candidate_key(candidate)] = candidate

    inventories_by_repository = {
        str(inventory.get("repository")): inventory
        for inventory in inventory_evidence
        if isinstance(inventory, Mapping)
        and inventory.get("source_revision") == target_revision
        and inventory.get("status") == "available"
    }
    for candidate in candidates.values():
        candidate["source_anchors"] = source_anchors
        candidate["likely_tests"] = rank_likely_tests(
            candidate,
            inventories_by_repository.get(str(candidate.get("target_repository"))),
            source_anchors,
            active_relations,
        )
        if include_evidence_scores:
            candidate["evidence_score"] = _evidence_score(candidate)

    rows = sorted(
        candidates.values(),
        key=lambda item: (
            _CLASSIFICATION_ORDER[item["classification"]],
            item["target_repository"],
            item["interface_id"],
            item["relationship_type"],
        ),
    )
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
    if semantic_indexes:
        evidence_sources.append("semantic_index")
    source_input = {
        "source_repository": source_repo_key,
        "canonical_repository": canonical_repository,
        "source_repo_key": source_repo_key,
        "target_revision": target_revision,
        "changed_paths": changed_paths,
    }
    if source_pr_number is not None:
        source_input["source_pr_number"] = source_pr_number
    if base_revision is not None:
        source_input["base_revision"] = base_revision
    if strict_evidence:
        source_input["evidence_profile"] = TRUST_EVIDENCE_PROFILE
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "complete" if not gaps else "partial",
        "input": source_input,
        "candidates": rows,
        "blast_radius": blast_radius,
        **(
            {
                "confidence": _confidence_summary(
                    rows,
                    sorted(set(gaps)),
                    contracts=contracts,
                    ci_evidence=ci_evidence,
                    inventory_evidence=inventory_evidence,
                    semantic_indexes=semantic_indexes,
                    source_revision=target_revision,
                    source_pr_number=source_pr_number,
                )
            }
            if strict_evidence
            else {}
        ),
        "gaps": sorted(set(gaps)),
        "warnings": sorted(set(warnings)),
        "provenance": {
            "step1_report_sha256": step1_hash,
            "rule_set_version": RULE_SET_VERSION,
            "evidence_sources": evidence_sources,
            "semantic_index_sha256": sorted(set(semantic_provenance)),
            "read_only": True,
            "catalog_mutation": "none",
            "source_pr_number": source_pr_number,
            "base_revision": base_revision,
        },
    }


def canonical_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def report_sha256(report: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
