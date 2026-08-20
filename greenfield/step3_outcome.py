"""Deterministic greenfield Step 3 blast-radius outcome assembly."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.semantic_contract import validate_index
from greenfield.step2_candidates import report_sha256
from scripts.validate_greenfield_step2 import validate as validate_step2

REPORT_SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_pr_impact_step_3"
RULE_SET_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CLASSIFICATION_ORDER = {
    "confirmed": 0,
    "candidate": 1,
    "unresolved": 2,
    "stale": 3,
    "unavailable": 4,
    "unknown": 5,
}
BLAST_RADIUS = {"local", "boundary", "multi_repo", "systemic", "unknown"}


class OutcomeError(ValueError):
    """Raised when Step 3 input evidence is invalid."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutcomeError(f"{label} must be a non-empty string")
    return value.strip()


def _source_context(step2: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    errors = validate_step2(step2)
    if errors:
        raise OutcomeError("invalid Step 2 report: " + "; ".join(errors))
    data = step2["input"]
    repository = _text(data["source_repository"], "Step 2 source_repository")
    revision = _text(data["target_revision"], "Step 2 target_revision").lower()
    if not SHA.fullmatch(revision):
        raise OutcomeError("Step 2 target_revision must be a 40-character SHA")
    paths = sorted({_text(path, "changed path") for path in data["changed_paths"]})
    if not paths:
        raise OutcomeError("Step 2 changed_paths must be non-empty")
    return repository, revision, paths


def load_related_pr_evidence(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutcomeError(f"related PR evidence read failed: {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise OutcomeError("related PR evidence must be an object")
    if raw.get("schema_version") != "0.1":
        raise OutcomeError("related PR evidence schema_version must be 0.1")
    if raw.get("evidence_type") != "related_pull_requests":
        raise OutcomeError("related PR evidence_type is invalid")
    repository = _text(raw.get("source_repository"), "related source_repository")
    revision = _text(raw.get("source_revision"), "related source_revision").lower()
    if not SHA.fullmatch(revision):
        raise OutcomeError("related source_revision must be a 40-character SHA")
    source_pr = raw.get("source_pr_number")
    if not isinstance(source_pr, int) or source_pr < 1:
        raise OutcomeError("source_pr_number must be a positive integer")
    rows = raw.get("pull_requests")
    if not isinstance(rows, list):
        raise OutcomeError("pull_requests must be a list")
    normalized: list[dict[str, Any]] = []
    keys: set[tuple[str, int]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise OutcomeError(f"pull_requests[{index}] must be an object")
        target_repo = _text(row.get("repository"), "related repository")
        number = row.get("number")
        if not isinstance(number, int) or number < 1:
            raise OutcomeError("related PR number must be a positive integer")
        state = _text(row.get("state"), "related PR state").lower()
        if state not in {"open", "merged"}:
            raise OutcomeError("related PR state must be open or merged")
        head_sha = _text(row.get("head_sha"), "related PR head_sha").lower()
        base_sha = _text(row.get("base_sha"), "related PR base_sha").lower()
        if not SHA.fullmatch(head_sha) or not SHA.fullmatch(base_sha):
            raise OutcomeError(
                "related PR head_sha and base_sha must be 40-character SHAs"
            )
        relation_type = _text(row.get("relation_type"), "related PR relation_type")
        evidence = row.get("evidence")
        if not isinstance(evidence, dict) or not _text(
            evidence.get("id"), "related PR evidence.id"
        ):
            raise OutcomeError("related PR evidence.id is required")
        key = (target_repo, number)
        if key in keys:
            raise OutcomeError(f"duplicate related PR: {target_repo}#{number}")
        keys.add(key)
        changed_paths = row.get("changed_paths", [])
        interface_ids = row.get("interface_ids", [])
        if not isinstance(changed_paths, list) or any(
            not isinstance(item, str) for item in changed_paths
        ):
            raise OutcomeError("related PR changed_paths must be a list of strings")
        if not isinstance(interface_ids, list) or any(
            not isinstance(item, str) for item in interface_ids
        ):
            raise OutcomeError("related PR interface_ids must be a list of strings")
        normalized.append(
            {
                "repository": target_repo,
                "number": number,
                "state": state,
                "head_sha": head_sha,
                "base_sha": base_sha,
                "relation_type": relation_type,
                "changed_paths": sorted(set(changed_paths)),
                "interface_ids": sorted(set(interface_ids)),
                "evidence": dict(evidence),
            }
        )
    result = {
        "schema_version": "0.1",
        "evidence_type": "related_pull_requests",
        "source_repository": repository,
        "source_revision": revision,
        "source_pr_number": source_pr,
        "pull_requests": sorted(
            normalized, key=lambda row: (row["repository"], row["number"])
        ),
        "evidence_path": source.as_posix(),
        "artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    return result


def _surface(status: str, items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"status": status, "items": items, **extra}


def _candidate_rows(step2: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [row for row in step2["candidates"] if isinstance(row, dict)]


def _evidence_refs(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    unique = {
        _canonical(item): dict(item)
        for item in candidate.get("evidence", [])
        if isinstance(item, dict)
    }
    return [unique[key] for key in sorted(unique)]


def _is_repository_only(candidate: Mapping[str, Any]) -> bool:
    return (
        candidate.get("relationship_type") == "repository_inventory"
        and candidate.get("interface_id")
        == f"repository:{candidate.get('target_repository')}"
    )


def _list_count(candidate: Mapping[str, Any], key: str) -> int:
    value = candidate.get(key, [])
    return len(value) if isinstance(value, list) else 0


def _inventory_observation(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    if candidate.get("relationship_type") != "repository_inventory":
        return None
    evidence = next(
        (
            item
            for item in candidate.get("evidence", [])
            if isinstance(item, dict) and item.get("kind") == "repository_inventory"
        ),
        {},
    )
    return {
        "inspected_revision": evidence.get("inspected_revision"),
        "source_revision": evidence.get("source_revision"),
        "inventory_path_count": _list_count(candidate, "inventory_paths"),
        "workflow_path_count": _list_count(candidate, "workflow_paths"),
        "workflow_count": _list_count(candidate, "workflows"),
        "artifact_status": evidence.get("artifact_status"),
        "ci_linkage_status": evidence.get("ci_linkage_status"),
        "response_sha256": evidence.get("response_sha256"),
    }


def _merge_evidence(*values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {
        _canonical(item): dict(item)
        for items in values
        for item in items
        if isinstance(item, dict)
    }
    return [unique[key] for key in sorted(unique)]


def _merge_observations(*values: dict[str, Any] | None) -> list[dict[str, Any]]:
    unique = {
        _canonical(value): dict(value) for value in values if isinstance(value, dict)
    }
    return [unique[key] for key in sorted(unique)]


def _component_surface(
    repository: str,
    revision: str,
    paths: list[str],
    step2_hash: str,
    semantic_index: Mapping[str, Any] | None,
    gaps: list[str],
) -> dict[str, Any]:
    items = [
        {
            "kind": "file",
            "identity": path,
            "resolution": "explicit_source",
            "source_repository": repository,
            "source_revision": revision,
            "evidence": [{"kind": "step2_input", "step2_report_sha256": step2_hash}],
        }
        for path in paths
    ]
    if semantic_index is None:
        gaps.append("semantic_index_not_provided:direct_semantic_components")
        return _surface("partial", items, semantic_evidence="unavailable")
    if (
        semantic_index.get("repository") != repository
        or semantic_index.get("revision") != revision
    ):
        raise OutcomeError(
            "semantic index repository or revision does not match Step 2"
        )
    nodes = {
        node.get("key"): node
        for node in semantic_index.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("key"), str)
    }
    seen = {(item["kind"], item["identity"]) for item in items}
    for edge in semantic_index.get("edges", []):
        if not isinstance(edge, dict) or edge.get("resolution") not in {
            "explicit_source",
            "resolved_exact",
        }:
            continue
        matching = [
            evidence
            for evidence in edge.get("evidence", [])
            if isinstance(evidence, dict) and evidence.get("source_path") in paths
        ]
        if not matching:
            continue
        for key in (edge.get("source"), edge.get("target")):
            node = nodes.get(key)
            if not isinstance(node, dict):
                continue
            identity = node.get("identity")
            kind = node.get("kind")
            if (
                not isinstance(identity, str)
                or not isinstance(kind, str)
                or (kind, identity) in seen
            ):
                continue
            seen.add((kind, identity))
            items.append(
                {
                    "kind": kind,
                    "identity": identity,
                    "resolution": edge["resolution"],
                    "source_repository": repository,
                    "source_revision": revision,
                    "evidence": matching,
                }
            )
    unresolved = sum(
        1
        for edge in semantic_index.get("edges", [])
        if isinstance(edge, dict)
        and edge.get("resolution")
        in {"ambiguous", "dynamic", "unresolved", "unavailable"}
        and any(
            isinstance(evidence, dict) and evidence.get("source_path") in paths
            for evidence in edge.get("evidence", [])
        )
    )
    not_promoted = sum(
        1
        for edge in semantic_index.get("edges", [])
        if isinstance(edge, dict)
        and edge.get("resolution") in {"framework_convention", "candidate_static"}
        and any(
            isinstance(evidence, dict) and evidence.get("source_path") in paths
            for evidence in edge.get("evidence", [])
        )
    )
    if unresolved:
        gaps.append(f"semantic_components_unresolved:{unresolved}")
    if not_promoted:
        gaps.append(f"semantic_components_not_promoted:{not_promoted}")
    return _surface(
        "available" if not unresolved else "partial",
        sorted(items, key=lambda item: (item["kind"], item["identity"])),
        semantic_evidence="available",
    )


def _aggregate_repositories(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        repository = candidate["target_repository"]
        current = grouped.get(repository)
        if current is None:
            grouped[repository] = {
                "repository": repository,
                "classification": candidate["classification"],
                "evidence": _evidence_refs(candidate),
                "observations": _merge_observations(_inventory_observation(candidate)),
            }
        elif CLASSIFICATION_ORDER.get(
            candidate["classification"], 99
        ) < CLASSIFICATION_ORDER.get(current["classification"], 99):
            grouped[repository] = {
                "repository": repository,
                "classification": candidate["classification"],
                "evidence": _merge_evidence(
                    current["evidence"], _evidence_refs(candidate)
                ),
                "observations": _merge_observations(
                    *current["observations"], _inventory_observation(candidate)
                ),
            }
        else:
            current["evidence"] = _merge_evidence(
                current["evidence"], _evidence_refs(candidate)
            )
            current["observations"] = _merge_observations(
                *current["observations"], _inventory_observation(candidate)
            )
    for row in grouped.values():
        row["evidence"] = _merge_evidence(row["evidence"])
        row["observations"] = sorted(row["observations"], key=_canonical)
    return sorted(
        grouped.values(),
        key=lambda row: (
            CLASSIFICATION_ORDER.get(row["classification"], 99),
            row["repository"],
        ),
    )


def _aggregate_interfaces(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        if _is_repository_only(candidate):
            continue
        key = (candidate["target_repository"], candidate["interface_id"])
        current = grouped.get(key)
        if current is None:
            grouped[key] = {
                "interface_id": candidate["interface_id"],
                "target_repository": candidate["target_repository"],
                "relationship_type": candidate["relationship_type"],
                "classification": candidate["classification"],
                "reason": candidate["reason"],
                "evidence": _evidence_refs(candidate),
            }
            continue
        if CLASSIFICATION_ORDER.get(
            candidate["classification"], 99
        ) < CLASSIFICATION_ORDER.get(current["classification"], 99):
            current.update(
                relationship_type=candidate["relationship_type"],
                classification=candidate["classification"],
                reason=candidate["reason"],
            )
        current["evidence"] = _merge_evidence(
            current["evidence"], _evidence_refs(candidate)
        )
    return sorted(
        grouped.values(),
        key=lambda row: (
            CLASSIFICATION_ORDER.get(row["classification"], 99),
            row["target_repository"],
            row["interface_id"],
        ),
    )


def _aggregate_owners(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        if _is_repository_only(candidate):
            continue
        key = (candidate["target_repository"], candidate["interface_id"])
        grouped.setdefault(key, []).append(candidate)
    rows = []
    for (target_repository, interface_id), related in grouped.items():
        owner_values = {
            (candidate.get("owner"), candidate.get("owner_repository"))
            for candidate in related
            if candidate.get("owner") or candidate.get("owner_repository")
        }
        conflicting = len(owner_values) > 1
        owner, owner_repository = next(iter(owner_values), (None, None))
        rows.append(
            {
                "interface_id": interface_id,
                "target_repository": target_repository,
                "owner": None if conflicting else owner,
                "owner_repository": None if conflicting else owner_repository,
                "status": "unknown"
                if conflicting
                else ("available" if owner or owner_repository else "unavailable"),
                "reason": "conflicting_owner_evidence" if conflicting else None,
                "evidence": _merge_evidence(
                    *[_evidence_refs(candidate) for candidate in related]
                ),
            }
        )
    return sorted(rows, key=lambda row: (row["target_repository"], row["interface_id"]))


def _aggregate_tests(
    candidates: list[dict[str, Any]], gaps: list[str]
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    interface_candidates = [
        candidate for candidate in candidates if not _is_repository_only(candidate)
    ]
    for candidate in interface_candidates:
        tests = candidate.get("tests", [])
        if not isinstance(tests, list):
            tests = []
        for test in tests:
            valid_test = (
                isinstance(test, dict)
                and isinstance(test.get("id"), str)
                and bool(test["id"].strip())
                and isinstance(test.get("path"), str)
                and bool(test["path"].strip())
            )
            if valid_test:
                rows.append(
                    {
                        "target_repository": candidate["target_repository"],
                        "interface_id": candidate["interface_id"],
                        "status": "available",
                        "test": test,
                        "evidence": _evidence_refs(candidate),
                    }
                )
            else:
                rows.append(
                    {
                        "target_repository": candidate["target_repository"],
                        "interface_id": candidate["interface_id"],
                        "status": "unavailable",
                        "reason": "malformed_test_evidence",
                        "evidence": _evidence_refs(candidate),
                    }
                )
                gaps.append("test_suites_unavailable:malformed_test_evidence")
        if not tests:
            rows.append(
                {
                    "target_repository": candidate["target_repository"],
                    "interface_id": candidate["interface_id"],
                    "status": "unavailable",
                    "reason": "no_normalized_test_evidence",
                    "evidence": _evidence_refs(candidate),
                }
            )
    if not interface_candidates:
        return _surface("not_modelled", [], reason="no_declared_interface_evidence")
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        test = row.get("test", {})
        key = (
            row["target_repository"],
            row["interface_id"],
            f"{test.get('id')}:{test.get('path')}"
            if isinstance(test, dict)
            else row["status"],
        )
        current = unique.get(key)
        if current is None or current["status"] == "unavailable":
            unique[key] = row
        elif row["status"] == "available":
            current["evidence"] = _merge_evidence(current["evidence"], row["evidence"])
    rows = list(unique.values())
    status = (
        "available"
        if rows and all(row["status"] == "available" for row in rows)
        else "partial"
    )
    if any(row["status"] == "unavailable" for row in rows):
        gaps.append("test_suites_unavailable:no_normalized_test_evidence")
    return _surface(
        status,
        sorted(
            rows,
            key=lambda row: (
                row["target_repository"],
                row["interface_id"],
                _canonical(row.get("test", {})),
            ),
        ),
    )


def _blast_radius(candidates: list[dict[str, Any]], gaps: list[str]) -> str:
    if any(
        candidate.get("relationship_type")
        in {
            "systemic",
            "shared_infrastructure",
            "shared_schema",
            "shared_build",
            "deployment",
        }
        or candidate.get("declared_relationship_type")
        in {
            "systemic",
            "shared_infrastructure",
            "shared_schema",
            "shared_build",
            "deployment",
        }
        or any(
            evidence.get("impact_scope") == "systemic"
            for evidence in candidate.get("evidence", [])
            if isinstance(evidence, dict)
        )
        for candidate in candidates
    ):
        return "systemic"
    if any(candidate.get("classification") == "confirmed" for candidate in candidates):
        return "multi_repo"
    if any(candidate.get("classification") == "candidate" for candidate in candidates):
        return "boundary"
    return "unknown" if gaps else "local"


def _impact_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for candidate in candidates:
        repository_only = _is_repository_only(candidate)
        interface_id = "" if repository_only else candidate["interface_id"]
        scope = "repository" if repository_only else "interface"
        key = (
            scope,
            candidate["target_repository"],
            interface_id,
            candidate["relationship_type"],
            candidate["classification"],
        )
        item = grouped.setdefault(
            key,
            {
                "scope": scope,
                "target_repository": candidate["target_repository"],
                "classification": candidate["classification"],
                "relationship_type": candidate["relationship_type"],
                "reason": candidate["reason"],
                "evidence": [],
            },
        )
        if not repository_only:
            item["interface_id"] = candidate["interface_id"]
        item["evidence"] = _merge_evidence(item["evidence"], _evidence_refs(candidate))
        observation = _inventory_observation(candidate)
        if observation is not None:
            item["observation"] = observation
    return sorted(
        grouped.values(),
        key=lambda row: (
            CLASSIFICATION_ORDER.get(row["classification"], 99),
            row["target_repository"],
            row.get("interface_id", ""),
            row["relationship_type"],
            row["scope"],
        ),
    )


def assemble_outcome(
    step2: Mapping[str, Any],
    *,
    semantic_index: Mapping[str, Any] | None = None,
    related_pr_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    repository, revision, paths = _source_context(step2)
    step2_hash = report_sha256(step2)
    gaps = sorted({str(gap) for gap in step2.get("gaps", [])})
    warnings = sorted({str(warning) for warning in step2.get("warnings", [])})
    candidates = _candidate_rows(step2)
    if semantic_index is not None:
        semantic_errors = validate_index(semantic_index)
        if semantic_errors:
            raise OutcomeError("invalid semantic index: " + "; ".join(semantic_errors))
    if related_pr_evidence is not None:
        required_related = {
            "source_pr_number",
            "source_repository",
            "source_revision",
            "pull_requests",
            "evidence_path",
            "artifact_sha256",
        }
        missing_related = sorted(required_related - set(related_pr_evidence))
        if missing_related:
            raise OutcomeError(
                "related PR evidence missing: " + ", ".join(missing_related)
            )
        if not isinstance(related_pr_evidence["pull_requests"], list):
            raise OutcomeError("related PR evidence pull_requests must be a list")
        if not isinstance(
            related_pr_evidence["artifact_sha256"], str
        ) or not SHA256.fullmatch(related_pr_evidence["artifact_sha256"]):
            raise OutcomeError("related PR evidence artifact_sha256 is invalid")
        if (
            related_pr_evidence.get("source_repository") != repository
            or related_pr_evidence.get("source_revision") != revision
        ):
            raise OutcomeError(
                "related PR evidence repository or revision does not match Step 2"
            )
        related_surface = _surface(
            "available",
            list(related_pr_evidence["pull_requests"]),
            source_pr_number=related_pr_evidence["source_pr_number"],
            source_repository=related_pr_evidence["source_repository"],
            source_revision=related_pr_evidence["source_revision"],
            evidence_path=related_pr_evidence["evidence_path"],
            artifact_sha256=related_pr_evidence["artifact_sha256"],
        )
    else:
        gaps.append(
            "related_pull_requests_not_modelled:revision_pinned_artifact_not_provided"
        )
        related_surface = _surface(
            "not_modelled", [], reason="revision_pinned_artifact_not_provided"
        )
    component_surface = _component_surface(
        repository, revision, paths, step2_hash, semantic_index, gaps
    )
    repositories = _aggregate_repositories(candidates)
    interfaces = _aggregate_interfaces(candidates)
    owners = _aggregate_owners(candidates)
    tests = _aggregate_tests(candidates, gaps)
    if not candidates and not gaps:
        warnings.append("no external impact candidates were resolved")
    outcome_status = "complete" if not gaps else "partial"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": outcome_status,
        "input": {
            "source_repository": repository,
            "target_revision": revision,
            "changed_paths": paths,
        },
        "blast_radius": _blast_radius(candidates, gaps),
        "direct_components": component_surface,
        "potentially_affected_repositories": _surface(
            "available" if repositories else ("unknown" if gaps else "available"),
            repositories,
        ),
        "interfaces": _surface(
            "available" if interfaces else "not_modelled",
            interfaces,
            **({} if interfaces else {"reason": "no_declared_interface_evidence"}),
        ),
        "owners": _surface(
            "available"
            if owners and all(row["status"] == "available" for row in owners)
            else ("partial" if owners else "not_modelled"),
            owners,
            **({} if owners else {"reason": "no_declared_interface_evidence"}),
        ),
        "test_suites": tests,
        "related_pull_requests": related_surface,
        "impact": _surface(
            "available" if candidates else ("unknown" if gaps else "available"),
            _impact_rows(candidates),
        ),
        "gaps": sorted(set(gaps)),
        "warnings": sorted(set(warnings)),
        "provenance": {
            "step2_report_sha256": step2_hash,
            "semantic_index_sha256": semantic_index.get("provenance", {}).get(
                "index_sha256"
            )
            if semantic_index
            else None,
            "related_pr_artifact_sha256": related_pr_evidence.get("artifact_sha256")
            if related_pr_evidence
            else None,
            "rule_set_version": RULE_SET_VERSION,
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
        },
    }


def canonical_json(report: Mapping[str, Any]) -> str:
    return _canonical(report)


def outcome_sha256(report: Mapping[str, Any]) -> str:
    return _sha256(report)
