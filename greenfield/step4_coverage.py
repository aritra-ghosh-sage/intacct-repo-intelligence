"""Deterministic, read-only greenfield Step 4 test-coverage mapping."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from greenfield.semantic_contract import validate_index
from greenfield.artifact_io import artifact_sha256
from greenfield.source_identity import repository_matches, source_identity
from greenfield.step4_contract import (
    ANALYSIS_KIND,
    CLASSIFICATION_ORDER,
    REPORT_SCHEMA_VERSION,
    RULE_SET_VERSION,
    SHA,
    Step4Error,
    evidence_digest,
    validate_step4_report,
)
from scripts.validate_greenfield_step3 import validate as validate_step3


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Step4Error(f"{label} must be a non-empty string")
    return value.strip()


def _source_context(
    step3: Mapping[str, Any],
) -> tuple[str, str, str, str, list[str], str]:
    errors = validate_step3(step3)
    if errors:
        raise Step4Error("invalid Step 3 report: " + "; ".join(errors))
    data = step3["input"]
    repository = _text(data["source_repository"], "Step 3 source_repository")
    try:
        canonical_repository, repo_key = source_identity(data)
    except ValueError as exc:
        raise Step4Error(str(exc)) from exc
    revision = _text(data["target_revision"], "Step 3 target_revision").lower()
    if not SHA.fullmatch(revision):
        raise Step4Error("Step 3 target_revision must be a 40-character SHA")
    paths = sorted({_text(path, "changed path") for path in data["changed_paths"]})
    return repository, canonical_repository, repo_key, revision, paths, artifact_sha256(step3)


def _evidence(value: Mapping[str, Any], kind: str, **extra: Any) -> dict[str, Any]:
    result = {"kind": kind, "digest": evidence_digest(value)}
    result.update(extra)
    return result


def _ci_evidence(
    value: Mapping[str, Any], test: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    extra: dict[str, Any] = {"evidence_id": value.get("evidence_id")}
    for field in ("workflow_run_id", "workflow_job_id", "check_run_id", "artifact_id"):
        if value.get(field) is not None:
            extra[field] = value[field]
    if isinstance(test, Mapping) and test.get("execution_result") is not None:
        extra["execution_result"] = test["execution_result"]
    return [_evidence(value, "ci", **extra)]


def _relation_scopes(
    contracts: Iterable[Mapping[str, Any]],
    canonical_repository: str,
    repo_key: str,
    source_revision: str,
    changed_paths: list[str],
    gaps: set[str],
) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = []
    for contract in contracts:
        if not repository_matches(contract.get("repository"), canonical_repository, repo_key):
            gaps.add(f"contract:source_repository_mismatch:{contract.get('repository', 'unknown')}")
            continue
        if contract.get("revision") != source_revision:
            gaps.add(f"contract:stale:{contract.get('evidence', {}).get('path', 'unknown')}")
            continue
        for relation in contract.get("relations", []):
            if relation.get("status") != "active":
                continue
            matched = sorted(set(changed_paths) & set(relation.get("source_paths", [])))
            if not matched:
                continue
            scopes.append({
                "target_repository": relation["consumer_repository"],
                "interface_id": relation["interface_id"],
                "relationship_type": relation.get("relationship_type", "declared"),
                "changed_paths": matched,
                "test_obligations": relation.get("test_obligations", []),
                "evidence": [_evidence(contract, "contract", path=contract["evidence"]["path"], revision=contract["revision"])],
            })
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for scope in scopes:
        key = (scope["target_repository"], scope["interface_id"], scope["relationship_type"])
        current = unique.get(key)
        if current is None:
            unique[key] = scope
        else:
            current["evidence"] = sorted(current["evidence"] + scope["evidence"], key=lambda value: json.dumps(value, sort_keys=True))
            current["test_obligations"] = sorted(current["test_obligations"] + scope["test_obligations"], key=lambda value: (str(value.get("id")), str(value.get("path"))))
    return sorted(unique.values(), key=lambda value: (value["target_repository"], value["interface_id"], value["relationship_type"]))


def _ci_tests(
    evidence: Mapping[str, Any],
    source_repository: str,
    canonical_repository: str,
    repo_key: str,
    source_revision: str,
    scope: Mapping[str, Any],
    gaps: set[str],
) -> list[dict[str, Any]]:
    status = evidence.get("status")
    target_repository = str(evidence.get("repository", scope["target_repository"]))
    if not repository_matches(evidence.get("source_repository"), canonical_repository, repo_key):
        gaps.add(f"ci:source_repository_mismatch:{target_repository}")
        return []
    if evidence.get("interface_id") != scope["interface_id"]:
        gaps.add(f"ci:interface_mismatch:{target_repository}")
        return []
    if evidence.get("source_revision") != source_revision:
        gaps.add(f"ci:stale:{evidence.get('evidence_id', target_repository)}")
        return [{
            "target_repository": target_repository,
            "interface_id": scope["interface_id"],
            "classification": "stale",
            "reason": "ci_evidence_source_revision_mismatch",
            "test": None,
            "source_repository": source_repository,
            "source_revision": source_revision,
            "evidence": _ci_evidence(evidence),
        }]
    if status in {"unavailable", "empty"}:
        gaps.add(f"ci:{status}:{target_repository}")
        return [{
            "target_repository": target_repository,
            "interface_id": scope["interface_id"],
            "classification": "unavailable" if status == "unavailable" else "candidate",
            "reason": f"ci_evidence_{status}",
            "test": None,
            "source_repository": source_repository,
            "source_revision": source_revision,
            "evidence": _ci_evidence(evidence),
        }]
    if status != "available":
        gaps.add(f"ci:unknown_status:{target_repository}")
        return []
    rows: list[dict[str, Any]] = []
    for test in evidence.get("tests", []):
        evidence_ref = _ci_evidence(evidence, test if isinstance(test, Mapping) else None)
        if not isinstance(test, dict) or not isinstance(test.get("id"), str) or not test.get("id", "").strip() or not isinstance(test.get("path"), str) or not test.get("path", "").strip():
            gaps.add(f"ci:malformed_test:{target_repository}")
            rows.append({
                "target_repository": target_repository,
                "interface_id": scope["interface_id"],
                "classification": "unknown",
                "reason": "malformed_test_evidence",
                "test": None,
                "source_repository": source_repository,
                "source_revision": source_revision,
                "evidence": evidence_ref,
            })
            continue
        indirect = test.get("coverage") == "indirect" or test.get("relationship") == "dependent_behavior"
        row = {
            "target_repository": target_repository,
            "interface_id": scope["interface_id"],
            "classification": "indirectly_covered" if indirect else "covered",
            "reason": "exact_source_revision_ci_evidence",
            "test": {"id": test["id"], "path": test["path"]},
            "source_repository": source_repository,
            "source_revision": source_revision,
            "target_revision": evidence.get("commit_sha"),
            "evidence": evidence_ref,
        }
        if isinstance(test.get("test_owner"), str) and test["test_owner"].strip():
            row["test_owner"] = test["test_owner"].strip()
        if isinstance(test.get("test_command"), str) and test["test_command"].strip():
            row["test_command"] = test["test_command"].strip()
        if test.get("execution_result") is not None:
            row["execution_result"] = test["execution_result"]
        if test.get("required_change") is not None:
            row["required_change"] = test["required_change"]
        if test.get("behavior_id") is not None:
            row["behavior_id"] = test["behavior_id"]
        rows.append(row)
    if not rows:
        gaps.add(f"ci:no_normalized_test_evidence:{target_repository}")
        rows.append(
            {
                "target_repository": target_repository,
                "interface_id": scope["interface_id"],
                "classification": "unavailable",
                "reason": "no_normalized_test_evidence",
                "test": None,
                "source_repository": source_repository,
                "source_revision": source_revision,
                "evidence": _ci_evidence(evidence),
            }
        )
    return rows


def _step3_test_rows(step3: Mapping[str, Any], scopes: list[Mapping[str, Any]], step3_hash: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scope_by_key = {(scope["target_repository"], scope["interface_id"]): scope for scope in scopes}
    surface = step3.get("test_suites", {})
    for item in surface.get("items", []) if isinstance(surface, Mapping) else []:
        if not isinstance(item, Mapping):
            continue
        key = (item.get("target_repository"), item.get("interface_id"))
        scope = scope_by_key.get(key)
        test = item.get("test")
        if scope is None or not isinstance(test, Mapping) or not isinstance(test.get("id"), str) or not isinstance(test.get("path"), str):
            continue
        rows.append({
            "target_repository": scope["target_repository"],
            "interface_id": scope["interface_id"],
            "classification": "candidate",
            "reason": "step3_test_evidence_without_execution_proof",
            "test": {"id": test["id"], "path": test["path"]},
            "source_repository": step3["input"]["source_repository"],
            "source_revision": step3["input"]["target_revision"],
            "evidence": [{"kind": "step3_report", "sha256": step3_hash}],
        })
    return rows


def _semantic_rows(semantic_indexes: Iterable[Mapping[str, Any]], source_repository: str, canonical_repository: str, repo_key: str, source_revision: str, changed_paths: list[str], gaps: set[str], scopes: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in semantic_indexes:
        if not repository_matches(index.get("repository"), canonical_repository, repo_key) or index.get("revision") != source_revision:
            gaps.add(f"semantic_index:stale_or_mismatched:{index.get('evidence_path', 'unknown')}")
            continue
        errors = validate_index(index)
        if errors:
            raise Step4Error("invalid semantic index: " + "; ".join(errors))
        for edge in index.get("edges", []):
            if not isinstance(edge, Mapping) or edge.get("resolution") not in {"explicit_source", "resolved_exact", "candidate_static"}:
                continue
            if not any(isinstance(value, Mapping) and value.get("source_path") in changed_paths for value in edge.get("evidence", [])):
                continue
            for scope in scopes:
                rows.append({
                    "target_repository": scope["target_repository"],
                    "interface_id": scope["interface_id"],
                    "behavior_id": edge.get("target") or edge.get("source"),
                    "classification": "candidate",
                    "reason": "semantic_index_candidate_without_ci_execution",
                    "test": None,
                    "source_repository": source_repository,
                    "source_revision": source_revision,
                    "evidence": [{"kind": "semantic_index", "sha256": evidence_digest(index), "resolution": edge.get("resolution")}],
                })
    return rows


def map_test_coverage(
    step3: Mapping[str, Any],
    contracts: Iterable[Mapping[str, Any]] = (),
    ci_evidence: Iterable[Mapping[str, Any]] = (),
    inventory_evidence: Iterable[Mapping[str, Any]] = (),
    semantic_indexes: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    source_repository, canonical_repository, repo_key, source_revision, changed_paths, step3_hash = _source_context(step3)
    contracts = list(contracts)
    ci_evidence = list(ci_evidence)
    inventory_evidence = list(inventory_evidence)
    semantic_indexes = list(semantic_indexes)
    gaps: set[str] = {str(value) for value in step3.get("gaps", [])}
    warnings: set[str] = set()
    scopes = _relation_scopes(contracts, canonical_repository, repo_key, source_revision, changed_paths, gaps)
    coverage = _step3_test_rows(step3, scopes, step3_hash)
    obligations: list[dict[str, Any]] = []
    for scope in scopes:
        matching_ci = [item for item in ci_evidence if item.get("repository") == scope["target_repository"]]
        for evidence in matching_ci:
            coverage.extend(_ci_tests(evidence, source_repository, canonical_repository, repo_key, source_revision, scope, gaps))
        for obligation in scope.get("test_obligations", []):
            if not isinstance(obligation, Mapping):
                gaps.add(f"obligation:unknown:{scope['interface_id']}")
                continue
            test_id = str(obligation.get("id"))
            test_path = str(obligation.get("path"))
            matches = [row for row in coverage if row.get("target_repository") == scope["target_repository"] and isinstance(row.get("test"), Mapping) and row["test"].get("id") == test_id and row["test"].get("path") == test_path and row["classification"] in {"covered", "indirectly_covered"}]
            if matches:
                status = "satisfied"
            elif any(item.get("repository") == scope["target_repository"] and item.get("status") == "unavailable" for item in inventory_evidence):
                status = "unavailable"
            else:
                status = "missing"
                gaps.add(f"test_obligation_missing:{scope['interface_id']}:{test_id}")
            obligation_row = {
                "target_repository": scope["target_repository"],
                "interface_id": scope["interface_id"],
                "obligation_id": f"{scope['interface_id']}:{test_id}:{test_path}",
                "test_id": test_id,
                "test_path": test_path,
                "status": status,
            }
            for field in ("test_owner", "test_command"):
                if isinstance(obligation.get(field), str) and obligation[field].strip():
                    obligation_row[field] = obligation[field].strip()
            if obligation.get("required_change") is not None:
                obligation_row["required_change"] = obligation["required_change"]
            if obligation.get("behavior_id") is not None:
                obligation_row["behavior_id"] = obligation["behavior_id"]
            obligations.append(obligation_row)
            if status == "missing":
                coverage.append(
                    {
                        "target_repository": scope["target_repository"],
                        "interface_id": scope["interface_id"],
                        "classification": "missing",
                        "reason": "declared_test_obligation_missing",
                        "test": {"id": test_id, "path": test_path},
                        "source_repository": source_repository,
                        "source_revision": source_revision,
                        "evidence": scope["evidence"],
                        "required_change": obligation.get("required_change"),
                        "behavior_id": obligation.get("behavior_id"),
                        "obligation_id": f"{scope['interface_id']}:{test_id}:{test_path}",
                    }
                )
        if not matching_ci and not scope.get("test_obligations"):
            coverage.append({
                "target_repository": scope["target_repository"],
                "interface_id": scope["interface_id"],
                "classification": "candidate",
                "reason": "declared_contract_without_test_evidence",
                "test": None,
                "source_repository": source_repository,
                "source_revision": source_revision,
                "evidence": scope["evidence"],
            })
    coverage.extend(_semantic_rows(semantic_indexes, source_repository, canonical_repository, repo_key, source_revision, changed_paths, gaps, scopes))
    if not scopes:
        gaps.add("test_coverage_unscoped:no_active_changed_contract")
    if not ci_evidence:
        gaps.add("ci_evidence_not_provided")
    if not contracts:
        gaps.add("contract_evidence_not_provided")
    if not inventory_evidence:
        gaps.add("repository_inventory_not_provided")
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in coverage:
        key = (row["target_repository"], row["interface_id"], row.get("behavior_id", ""), json.dumps(row.get("test"), sort_keys=True), row["classification"], row["reason"])
        unique[key] = row
    coverage = sorted(unique.values(), key=lambda row: (CLASSIFICATION_ORDER.get(row["classification"], 99), row["target_repository"], row["interface_id"], str(row.get("behavior_id", "")), json.dumps(row.get("test"), sort_keys=True), row["reason"]))
    obligations.sort(key=lambda row: (row["target_repository"], row["interface_id"], row["test_id"], row["test_path"]))
    coverage_status = "available" if coverage and not any(value.startswith(("ci:", "semantic_index:", "test_obligation_missing")) for value in gaps) else ("partial" if coverage else "unavailable")
    obligation_status = "available" if obligations and all(row["status"] == "satisfied" for row in obligations) else ("partial" if obligations else "not_modelled")
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "complete" if not gaps else "partial",
        "input": {
            "source_repository": source_repository,
            "canonical_repository": canonical_repository,
            "source_repo_key": repo_key,
            "target_revision": source_revision,
            "changed_paths": changed_paths,
            **{
                field: step3["input"][field]
                for field in ("source_pr_number", "base_revision")
                if field in step3["input"]
            },
        },
        "coverage": {"status": coverage_status, "items": coverage},
        "obligations": {"status": obligation_status, "items": obligations},
        "gaps": sorted(gaps),
        "warnings": sorted(warnings),
        "provenance": {
            "step3_report_sha256": step3_hash,
            "evidence_digests": sorted({evidence_digest(value) for value in [*contracts, *ci_evidence, *inventory_evidence, *semantic_indexes]}),
            "rule_set_version": RULE_SET_VERSION,
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
        },
    }
    errors = validate_step4_report(report)
    if errors:
        raise Step4Error("generated invalid Step 4 report: " + "; ".join(errors))
    return report


__all__ = ["map_test_coverage"]
